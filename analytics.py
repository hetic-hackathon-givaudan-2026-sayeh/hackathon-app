"""
Analyses statistiques de TrendPulse.

Aucune fonction de ce module ne declenche d'alerte. Les sorties sont des
indicateurs consommes par scoring.py, pipeline.py et l'API. Fonctions pures :
aucune ecriture en base ici. Tous les parametres sont dans config.py.
"""

import math
import re
from datetime import date, timedelta

import numpy as np
from scipy import stats

import config


# ===========================================================================
# Section : outils robustes
# ===========================================================================

def mad(values):
    """Ecart absolu median (non mis a l'echelle)."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0
    return float(np.median(np.abs(arr - np.median(arr))))


def robust_z(value, values):
    """z robuste : (x - mediane) / (1,4826 x MAD). None si la MAD est nulle."""
    scale = 1.4826 * mad(values)
    if scale <= 0:
        return None
    return (value - float(np.median(values))) / scale


def smooth(values, k=None):
    """Moyenne mobile arriere sur k semaines (k = SMOOTH_WEEKS)."""
    k = k or config.SMOOTH_WEEKS
    arr = np.asarray(values, dtype=float)
    if arr.size == 0 or k <= 1:
        return arr.tolist()
    out = [float(arr[max(0, i - k + 1): i + 1].mean()) for i in range(arr.size)]
    return out


def _clamp(v, low=0.0, high=1.0):
    return max(low, min(high, v))


_PAIRS = {}


def theil_sen(values):
    """
    Pente de Theil-Sen : mediane des pentes entre toutes les paires de points.
    Ordonnee = mediane(y) - pente x mediane(t), comme scipy.stats.theilslopes,
    en numpy pur pour tenir les BOOTSTRAP_N tirages.
    """
    y = np.asarray(values, dtype=float)
    n = y.size
    if n < 2:
        return 0.0, float(y.mean()) if n else 0.0
    if n not in _PAIRS:
        _PAIRS[n] = np.triu_indices(n, 1)
    i, j = _PAIRS[n]
    slope = float(np.median((y[j] - y[i]) / (j - i)))
    return slope, float(np.median(y)) - slope * (n - 1) / 2


# ===========================================================================
# Section : desaisonnalisation et indicateurs v2 (RG23b, RG24)
# ===========================================================================

def seasonal_acf(values, lag=None):
    """
    RG23b : autocorrelation au lag saisonnier (52 semaines) calculee sur la serie
    detendancee : la droite de Theil-Sen est retiree avant le calcul, pour qu'une
    simple tendance lineaire ne passe pas pour une saison.
    """
    lag = lag or config.SEASONAL_PERIOD
    arr = np.asarray(values, dtype=float)
    if arr.size <= lag + 4:
        return None
    slope, intercept = theil_sen(arr)
    x = arr - (intercept + slope * np.arange(arr.size))
    x = x - x.mean()
    var = float((x ** 2).sum())
    if var == 0:
        return None
    return float((x[lag:] * x[:-lag]).sum()) / var


def deseasonalize(values):
    """
    RG23b : decomposition STL (statsmodels) si la serie fait au moins
    MIN_WEEKS_SEASONAL semaines et si une saisonnalite est detectee
    (autocorrelation a 52 semaines >= SEASONAL_ACF_MIN). Moins de 104 semaines :
    aucune correction et flag seasonality_unknown.
    Retourne (serie corrigee, residus STL ou None, detail).
    """
    arr = np.asarray(values, dtype=float)
    if arr.size < config.MIN_WEEKS_SEASONAL:
        return arr.tolist(), None, {"seasonality_unknown": True, "method": "aucune correction",
                                    "weeks": int(arr.size)}
    acf = seasonal_acf(arr)
    if acf is None or acf < config.SEASONAL_ACF_MIN:
        return arr.tolist(), None, {"seasonality_unknown": False, "method": "aucune correction, saisonnalite non detectee",
                                    "acf52": None if acf is None else round(acf, 4), "weeks": int(arr.size)}
    from statsmodels.tsa.seasonal import STL

    res = STL(arr, period=config.SEASONAL_PERIOD, robust=True).fit()
    adjusted = arr - res.seasonal
    var_rs = float(np.var(res.resid + res.seasonal))
    strength = _clamp(1 - float(np.var(res.resid)) / var_rs) if var_rs > 0 else 0.0
    return adjusted.tolist(), res.resid.tolist(), {"seasonality_unknown": False, "method": "STL",
                                                   "acf52": round(acf, 4), "seasonal_strength": round(strength, 4),
                                                   "weeks": int(arr.size)}


def persistence_v2(values):
    """
    RG24 persistance : part des 26 dernieres semaines au-dessus de
    (mediane de la periode de base + 1 MAD). Base = 26 premieres semaines de la
    fenetre de 52. Moins de 52 semaines : moitie de base, moitie recente, flag short_history.
    """
    p = config.V2
    window = list(values[-p["window_weeks"]:])
    n = len(window)
    short = n < p["window_weeks"]
    if short:
        base_len = n // 2
        recent_len = n - base_len
    else:
        base_len, recent_len = p["base_weeks"], p["recent_weeks"]
    if base_len < 4 or recent_len < 4:
        return None, {"reason": "moins de 8 semaines", "short_history": True}
    base = window[:base_len]
    recent = window[-recent_len:]
    threshold = float(np.median(base)) + max(mad(base), config.PERSISTENCE_MAD_FLOOR)
    share = sum(1 for v in recent if v > threshold) / len(recent)
    return share, {"threshold": round(threshold, 4), "base_weeks": base_len, "recent_weeks": recent_len,
                   "weeks_above": sum(1 for v in recent if v > threshold), "short_history": short}


def regularity_v2(values):
    """
    RG24 regularite : tau de Kendall (scipy) et pente de Theil-Sen sur 12 a 36
    semaines, combines, moins une penalite de dispersion des residus autour de
    la droite. Meme echelle de pente et meme plafond de penalite que la v1.
    """
    d = config.DURABILITY
    window = np.asarray(values[-d["regularity_max_weeks"]:], dtype=float)
    if window.size < d["regularity_min_weeks"]:
        return None, {"weeks": int(window.size), "reason": "moins de 12 semaines"}
    t = np.arange(window.size, dtype=float)
    level = float(np.median(np.abs(window)))
    if level <= 0:
        return 0.0, {"weeks": int(window.size), "reason": "niveau nul"}
    if float(np.ptp(window)) == 0:
        tau = 0.0
    else:
        tau = float(stats.kendalltau(t, window).statistic)
        tau = 0.0 if math.isnan(tau) else tau
    slope, intercept = theil_sen(window)
    slope_pct = float(slope) / level * 100
    slope_score = _clamp(0.5 + slope_pct / (2 * d["regularity_slope_scale_pct"]))
    tau_score = (tau + 1) / 2
    residuals = window - (intercept + slope * t)
    dispersion = 1.4826 * mad(residuals) / level
    penalty = min(dispersion * d["volatility_factor"], d["volatility_max_penalty"])
    value = _clamp((slope_score + tau_score) / 2 - penalty)
    return value, {"weeks": int(window.size), "kendall_tau": round(tau, 4), "theil_sen_slope_pct": round(slope_pct, 4),
                   "residual_dispersion": round(dispersion, 4), "dispersion_penalty": round(penalty, 4)}


def peak_retention(values, weeks, calendar_weeks=()):
    """
    RG24 retention apres pic : moyenne des semaines +4 a +12 apres le pic
    divisee par le niveau du pic. Les semaines du calendrier (RG23a) ne peuvent
    pas etre un pic organique. Moins de 12 semaines apres le pic : retention
    inconnue et flag peak_recent (RG05).
    """
    p = config.V2
    cal = set(calendar_weeks)
    candidates = [i for i in range(len(values)) if weeks[i] not in cal]
    if len(values) < config.EVENT_PENALTY["min_weeks"] or not candidates:
        return {"peak_index": None, "peak_week": None, "z": None, "isolated": False, "retention": None,
                "peak_recent": False, "reason": "serie trop courte"}
    idx = max(candidates, key=lambda i: values[i])
    peak = values[idx]
    z = robust_z(peak, values)
    isolated = z is not None and z > config.PEAK_Z
    after = values[idx + p["retention_from"]: idx + p["retention_to"] + 1]
    peak_recent = len(values) - 1 - idx < p["retention_to"]
    retention = None
    if not peak_recent and peak > 0:
        retention = float(np.mean(after)) / peak
    return {"peak_index": idx, "peak_week": weeks[idx].isoformat(), "z": None if z is None else round(z, 3),
            "isolated": isolated, "retention": None if retention is None else round(retention, 4),
            "peak_recent": peak_recent, "calendar_weeks_excluded": len(cal & set(weeks))}


def event_penalty_v2(peak, events):
    """
    RG06 v2 : pic isole (z robuste > PEAK_Z) et retention < RETENTION_MID.
    Penalite lineaire de 5 a 15 points, maximale sous RETENTION_LOW. Un
    evenement connu date pres du pic (known_events) donne la penalite maximale.
    Retention inconnue (pic recent) : aucune penalite, flag peak_recent.
    """
    ep = config.EVENT_PENALTY
    low_pts, high_pts = ep["without_known_event"], ep["with_known_event"]
    if not peak.get("isolated") or peak.get("retention") is None:
        return 0.0, {"spike": bool(peak.get("isolated")), "reason": "pas de pic isole mesurable"}
    ret = peak["retention"]
    if ret >= config.RETENTION_MID:
        return 0.0, {"spike": True, "retention": ret, "reason": "retention suffisante"}
    peak_week = date.fromisoformat(peak["peak_week"])
    window = timedelta(days=ep["event_match_days"])
    for ev in events or []:
        ev_date = ev["event_date"] if isinstance(ev["event_date"], date) else date.fromisoformat(str(ev["event_date"]))
        if abs(ev_date - peak_week) <= window:
            return float(high_pts), {"spike": True, "retention": ret, "event": ev.get("event_label")}
    if ret <= config.RETENTION_LOW:
        pts = float(high_pts)
    else:
        pts = low_pts + (high_pts - low_pts) * (config.RETENTION_MID - ret) / (config.RETENTION_MID - config.RETENTION_LOW)
    return round(pts, 2), {"spike": True, "retention": ret, "event": None}


def cusum_start(values, weeks):
    """
    Date de debut de tendance par CUSUM maison (numpy) sur la serie
    standardisee par la periode de base. ruptures (PELT) est utilise en
    complement seulement s'il est installe.
    """
    p = config.V2
    arr = np.asarray(values, dtype=float)
    if arr.size < 8:
        return {"start_week": None, "method": "cusum", "reason": "serie trop courte"}
    base = arr[: max(4, min(p["base_weeks"], arr.size // 2))]
    scale = 1.4826 * mad(base) or float(np.std(base)) or 1.0
    z = (arr - float(np.median(base))) / scale
    s = 0.0
    onset = None
    start = None
    for i, v in enumerate(z):
        s = max(0.0, s + v - p["cusum_k"])
        if s == 0:
            onset = None
        elif onset is None:
            onset = i
        if s > p["cusum_h"]:
            start = onset
            break
    out = {"start_week": weeks[start].isoformat() if start is not None else None, "method": "cusum"}
    try:
        import ruptures

        algo = ruptures.Pelt(model="rbf").fit(arr.reshape(-1, 1))
        bkps = [b for b in algo.predict(pen=10) if b < arr.size]
        out["pelt_breaks"] = [weeks[b].isoformat() for b in bkps]
    except ImportError:
        out["pelt_breaks"] = None
    return out


def indicators_v2(values, weeks, calendar_weeks=(), events=()):
    """
    Indicateurs RG24 sur la serie desaisonnalisee. values et weeks : historique
    complet disponible (jusqu'a 104 semaines et plus), la fenetre de 52 semaines
    est prise a la fin. Retourne un dict avec criteres, penalite, flags et series.
    """
    adjusted, _resid, season = deseasonalize(values)
    w = config.V2["window_weeks"]
    win_vals = adjusted[-w:]
    win_weeks = list(weeks[-w:])
    pers, pers_d = persistence_v2(win_vals)
    reg, reg_d = regularity_v2(win_vals)
    peak = peak_retention(win_vals, win_weeks, calendar_weeks)
    penalty, pen_d = event_penalty_v2(peak, events)
    flags = []
    if season["seasonality_unknown"]:
        flags.append("seasonality_unknown")
    if len(values) < w:
        flags.append("short_history")
    if peak.get("peak_recent"):
        flags.append("peak_recent")
    return {
        "persistance": (pers, pers_d), "regularite": (reg, reg_d), "peak": peak,
        "penalty": penalty, "penalty_detail": pen_d, "season": season, "flags": flags,
        "start": cusum_start(win_vals, win_weeks), "window_values": win_vals, "window_weeks": win_weeks,
    }


# ===========================================================================
# Section : intervalle par bootstrap (RG25)
# ===========================================================================

def block_bootstrap_series(values, n=None, block_len=None, seed=None):
    """
    Series reechantillonnees : droite de Theil-Sen + residus tires par blocs
    circulaires de BLOCK_LEN semaines. Graine fixe pour la reproductibilite.
    """
    n = config.BOOTSTRAP_N if n is None else n
    block_len = block_len or config.BLOCK_LEN
    rng = np.random.default_rng(config.BOOTSTRAP_SEED if seed is None else seed)
    arr = np.asarray(values, dtype=float)
    size = arr.size
    t = np.arange(size, dtype=float)
    slope, intercept = theil_sen(arr)
    fitted = intercept + slope * t
    resid = arr - fitted
    out = []
    for _ in range(n):
        starts = rng.integers(0, size, size=math.ceil(size / block_len))
        idx = np.concatenate([(np.arange(s, s + block_len) % size) for s in starts])[:size]
        out.append((fitted + resid[idx]).tolist())
    return out


def class_uncertain(low, high, classes=None):
    """Vrai si l'intervalle chevauche un seuil de classe (hors seuil 0)."""
    classes = classes or config.DURABILITY_CLASSES
    return any(low < thr <= high for thr, _label in classes if thr > 0)


def interval_from_samples(center, samples):
    """
    Percentiles 10 et 90. L'intervalle affiche contient toujours le score
    central : si un percentile le laisse dehors, la borne est etendue et signalee.
    """
    clean = [s for s in samples if s is not None]
    if center is None or not clean:
        return None, None, {"samples": len(clean)}
    low, high = float(np.percentile(clean, 10)), float(np.percentile(clean, 90))
    extended = center < low or center > high
    low, high = min(low, center), max(high, center)
    return low, high, {"samples": len(clean), "center_outside_percentiles": extended}


# ===========================================================================
# Section : analogues par DTW (RG26)
# ===========================================================================

def znorm(values):
    arr = np.asarray(values, dtype=float)
    sd = float(arr.std())
    if sd == 0:
        return np.zeros_like(arr)
    return (arr - arr.mean()) / sd


def dtw_distance(a, b, window=None):
    """DTW avec bande de Sakoe-Chiba (DTW_WINDOW). Distance 0 pour deux series identiques."""
    window = config.DTW_WINDOW if window is None else window
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    n, m = a.size, b.size
    w = max(window, abs(n - m))
    cost = np.full((n + 1, m + 1), np.inf)
    cost[0, 0] = 0.0
    for i in range(1, n + 1):
        lo, hi = max(1, i - w), min(m, i + w)
        for j in range(lo, hi + 1):
            d = abs(a[i - 1] - b[j - 1])
            cost[i, j] = d + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])
    return float(cost[n, m])


def best_alignment(query, ref, window=None):
    """Meilleure fin de fenetre dans ref (meme longueur que query), series normalisees. Retourne (distance, index_fin)."""
    q = znorm(query)
    n = q.size
    best = (math.inf, None)
    for end in range(n - 1, len(ref)):
        seg = znorm(ref[end - n + 1: end + 1])
        dist = dtw_distance(q, seg, window)
        if dist < best[0]:
            best = (dist, end)
    return best


def _months_between(d1, d2):
    return (d2 - d1).days / 30.4375


def find_analogs(query_values, case_series, n_verified_cases):
    """
    RG26 : les TOP analogues les plus proches parmi les cas verifies du
    secteur, avec distance, semaine alignee et phase. Moins de MIN_ANALOGS cas
    verifies : "non disponible" (RG05). case_series : liste de dicts
    (case_id, label, weeks, values, first_signal_date, mainstream_date).
    """
    if n_verified_cases < config.MIN_ANALOGS or len(case_series) < config.MIN_ANALOGS:
        return {"status": "non disponible", "reason": "moins de " + str(config.MIN_ANALOGS) + " cas verifies avec historique",
                "analogs": [], "analog_delay_low": None, "analog_delay_high": None}
    q = list(query_values)[-config.V2["window_weeks"]:]
    results = []
    for case in case_series:
        if len(case["values"]) < len(q):
            continue
        dist, end = best_alignment(q, case["values"])
        if end is None:
            continue
        aligned = case["weeks"][end]
        first, main = case.get("first_signal_date"), case.get("mainstream_date")
        if main and aligned >= main:
            phase, remaining = "apres mainstream", 0.0
        elif first and aligned < first:
            phase, remaining = "avant premier signal", _months_between(aligned, main) if main else None
        else:
            phase, remaining = "entre premier signal et mainstream", _months_between(aligned, main) if main else None
        results.append({"case_id": case["case_id"], "label": case.get("label"), "distance": round(dist, 4),
                        "aligned_week": aligned.isoformat(), "phase": phase,
                        "months_to_mainstream": None if remaining is None else round(remaining, 1)})
    results.sort(key=lambda r: r["distance"])
    top = results[: config.V2["top_analogs"]]
    delays = [r["months_to_mainstream"] for r in top if r["months_to_mainstream"] is not None]
    if len(top) < config.MIN_ANALOGS or not delays:
        return {"status": "non disponible", "reason": "analogues alignes insuffisants", "analogs": top,
                "analog_delay_low": None, "analog_delay_high": None}
    return {"status": "ok", "analogs": top, "analog_delay_low": min(delays), "analog_delay_high": max(delays)}


# ===========================================================================
# Section : sequence de propagation (RG27)
# ===========================================================================

def propagation_sequence(series_by_type):
    """
    RG27 : premiere semaine ou chaque type de source franchit le seuil RG24
    (mediane de base + 1 MAD), ordre d'apparition et correlation decalee
    (lag borne) avec le premier type. Affichage seulement, jamais dans un
    score. Libelle "sequence observee", aucune causalite.
    series_by_type : {source_type: {semaine: volume}}.
    """
    firsts = {}
    for stype, series in series_by_type.items():
        weeks = sorted(series)
        vals = [series[w] for w in weeks]
        if len(vals) < 8:
            continue
        base = vals[: max(4, min(config.V2["base_weeks"], len(vals) // 2))]
        thr = float(np.median(base)) + mad(base)
        for w, v in zip(weeks, vals):
            if v > thr and v > 0:
                firsts[stype] = w
                break
    order = sorted(firsts, key=lambda k: (firsts[k], k))
    lags = []
    if order:
        ref_type = order[0]
        ref = series_by_type[ref_type]
        for other in order[1:]:
            best = None
            for lag in range(0, config.V2["max_lag_weeks"] + 1):
                pairs = [(ref[w], series_by_type[other].get(w + timedelta(weeks=lag)))
                         for w in sorted(ref)]
                pairs = [(a, b) for a, b in pairs if b is not None]
                if len(pairs) < 6:
                    continue
                xa = np.array([p[0] for p in pairs], dtype=float)
                xb = np.array([p[1] for p in pairs], dtype=float)
                if xa.std() == 0 or xb.std() == 0:
                    continue
                corr = float(np.corrcoef(xa, xb)[0, 1])
                if best is None or corr > best[1]:
                    best = (lag, corr)
            lags.append({"from": ref_type, "to": other, "lag_weeks": None if best is None else best[0],
                         "correlation": None if best is None else round(best[1], 3)})
    return {"label": "séquence observée", "note": "ordre d'apparition observé, aucune causalité",
            "order": [{"source_type": t, "first_week": firsts[t].isoformat()} for t in order],
            "lagged_correlation": lags}


# ===========================================================================
# Section : projection statistique optionnelle (RG19)
# Ne declenche jamais d'alerte, libelle "projection statistique", jamais de
# date unique. scikit-learn et ruptures sont des extras (requirements-ml.txt).
# ===========================================================================

PROJECTION_LABEL = "projection statistique"
CHALLENGER_FEATURES = ["persistance", "regularite", "diversite", "diffusion", "adoption_precoce", "penalite"]


def ml_extras_available():
    try:
        import ruptures  # noqa: F401
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


def _holt(series, horizon):
    """Lissage exponentiel avec tendance amortie (statsmodels). Retourne (prevision, ecart-type des residus)."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    arr = np.asarray(series, dtype=float)
    if arr.size < 10 or float(np.ptp(arr)) == 0:
        return np.full(horizon, float(arr[-1]) if arr.size else 0.0), float(np.std(arr)) if arr.size else 0.0
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(arr, trend="add", damped_trend=True, initialization_method="estimated").fit()
    return np.asarray(fit.forecast(horizon), dtype=float), float(np.std(fit.resid))


def _project_values(values, horizon):
    """Prevision sur la serie desaisonnalisee puis reintegration de la saison de l'annee precedente."""
    adjusted, _resid, season = deseasonalize(values)
    seasonal = np.asarray(values, dtype=float) - np.asarray(adjusted, dtype=float)
    fc, sd = _holt(adjusted, horizon)
    if season.get("method") == "STL":
        period = config.SEASONAL_PERIOD
        fc = fc + np.array([seasonal[len(values) - period + (k % period)] for k in range(horizon)])
    return fc, sd, season


def naive_seasonal(values, horizon):
    """Baseline naive saisonniere : meme semaine un an plus tot, sinon derniere valeur."""
    p = config.SEASONAL_PERIOD
    if len(values) >= p:
        return np.array([values[len(values) - p + (k % p)] for k in range(horizon)], dtype=float)
    return np.full(horizon, float(values[-1]))


def rolling_backtest(values, horizon, n_origins=4):
    """Backtest glissant : erreur absolue moyenne du modele et de la baseline sur plusieurs origines passees."""
    model_err, base_err = [], []
    for i in range(n_origins, 0, -1):
        cut = len(values) - horizon * i
        if cut < 26:
            continue
        past, future = values[:cut], np.asarray(values[cut:cut + horizon], dtype=float)
        fc, _sd, _s = _project_values(past, horizon)
        model_err.append(float(np.mean(np.abs(fc[:future.size] - future))))
        base_err.append(float(np.mean(np.abs(naive_seasonal(past, horizon)[:future.size] - future))))
    if not model_err:
        return {"origins": 0, "model_mae": None, "baseline_mae": None, "beats_baseline": None}
    return {"origins": len(model_err), "model_mae": round(float(np.mean(model_err)), 3),
            "baseline_mae": round(float(np.mean(base_err)), 3),
            "beats_baseline": float(np.mean(model_err)) < float(np.mean(base_err))}


def projection(values, weeks, horizon=8):
    """
    RG19 : projection par lissage exponentiel avec intervalle de prevision a
    80 pour cent, comparee a la baseline naive saisonniere. "non fiable" si
    elle ne la bat pas. Jamais de date unique, jamais d'alerte.
    """
    if len(values) < 26:
        return {"label": PROJECTION_LABEL, "status": "non disponible", "reason": "moins de 26 semaines d'historique"}
    fc, sd, season = _project_values(values, horizon)
    bt = rolling_backtest(values, horizon)
    z = 1.2816
    points = []
    for k in range(horizon):
        week = weeks[-1] + timedelta(weeks=k + 1)
        half = z * sd * math.sqrt(k + 1)
        points.append({"week": week.isoformat(), "projection": round(float(fc[k]), 2),
                       "low": round(float(fc[k]) - half, 2), "high": round(float(fc[k]) + half, 2)})
    reliable = bool(bt["beats_baseline"])
    return {"label": PROJECTION_LABEL, "status": "ok" if reliable else "non fiable",
            "note": "intervalle de prevision, aucune date unique, aucune alerte declenchee",
            "seasonality": season.get("method"), "points": points, "backtest": bt}


def challenger(train_rows, current_features, rule_class):
    """
    Challenger durable ou buzz par regression logistique sur les indicateurs
    RG24, valide sur un split temporel. Affiche n_train et un avertissement
    petit echantillon. Drapeau "a examiner" en cas de desaccord avec les regles.
    """
    rows = [r for r in train_rows if r.get("label") in ("durable", "buzz")
            and all((r.get("features") or {}).get(f) is not None for f in CHALLENGER_FEATURES)]
    rows.sort(key=lambda r: str(r.get("as_of")))
    n_train = int(len(rows) * 0.7)
    base = {"model": "regression logistique", "n_train": n_train, "n_validation": len(rows) - n_train,
            "warning": "petit echantillon, resultat indicatif"}
    train, valid = rows[:n_train], rows[n_train:]
    if n_train < 6 or len({r["label"] for r in train}) < 2:
        return {**base, "status": "insuffisant", "reason": "au moins 6 cas etiquetes des deux classes requis"}
    if any(current_features.get(f) is None for f in CHALLENGER_FEATURES):
        return {**base, "status": "non disponible", "reason": "indicateurs RG24 incomplets pour cette tendance"}
    from sklearn.linear_model import LogisticRegression

    x = np.array([[r["features"][f] for f in CHALLENGER_FEATURES] for r in train], dtype=float)
    y = np.array([1 if r["label"] == "durable" else 0 for r in train])
    model = LogisticRegression(max_iter=1000).fit(x, y)
    matrix = {"durable": {"durable": 0, "buzz": 0}, "buzz": {"durable": 0, "buzz": 0}}
    for r in valid:
        pred = "durable" if model.predict(np.array([[r["features"][f] for f in CHALLENGER_FEATURES]]))[0] == 1 else "buzz"
        matrix[r["label"]][pred] += 1
    cur = np.array([[current_features[f] for f in CHALLENGER_FEATURES]], dtype=float)
    pred = "durable" if model.predict(cur)[0] == 1 else "buzz"
    return {**base, "status": "ok", "prediction": pred, "rule_based": rule_class,
            "a_examiner": pred != rule_class, "validation_matrix": matrix}


# ===========================================================================
# Section : decouverte de candidats (RG28)
# Montee de frequence de n-grammes dans les titres GDELT et RSS. Les candidats
# sont "a valider" : jamais scores ni alertes sans validation humaine.
# ===========================================================================

_TOKEN = re.compile(r"[a-zà-öø-ÿ0-9][a-zà-öø-ÿ0-9'-]*", re.IGNORECASE)


def title_ngrams(title, max_n=None):
    """N-grammes de 1 a max_n mots, sans mot vide en debut ou fin, mots d'au moins 3 lettres."""
    max_n = max_n or config.DISCOVER["max_ngram"]
    tokens = [t.lower().strip("'-") for t in _TOKEN.findall(title or "")]
    out = set()
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            gram = tokens[i:i + n]
            if gram[0] in config.STOPWORDS or gram[-1] in config.STOPWORDS:
                continue
            if any(len(t) < 3 or t.isdigit() for t in (gram[0], gram[-1])):
                continue
            out.add(" ".join(gram))
    return out


def rising_ngrams(docs, as_of, known_terms=()):
    """
    docs : liste de (date, titre, source). Compare la frequence recente
    (recent_weeks) a la base (baseline_weeks) avec un lissage de +1.
    Exclut les concepts deja suivis. Retourne les candidats tries.
    """
    p = config.DISCOVER
    recent_start = as_of - timedelta(weeks=p["recent_weeks"])
    base_start = recent_start - timedelta(weeks=p["baseline_weeks"])
    recent, base, sources = {}, {}, {}
    seen = set()
    for d, title, source in docs:
        key = (title or "").strip().lower()
        if not key or key in seen or d is None or d <= base_start or d > as_of:
            continue
        seen.add(key)
        target = recent if d > recent_start else base
        for g in title_ngrams(title):
            target[g] = target.get(g, 0) + 1
            sources.setdefault(g, set()).add(source)
    known = [k.lower() for k in known_terms if k]
    out = []
    for g, n in recent.items():
        if n < p["min_recent_count"] or any(g in k or k in g for k in known):
            continue
        b = base.get(g, 0)
        ratio = (n / p["recent_weeks"]) / ((b + 1) / p["baseline_weeks"])
        if ratio >= p["min_rise_ratio"]:
            out.append({"ngram": g, "recent_count": n, "baseline_count": b, "rise_ratio": round(ratio, 2),
                        "sources": sorted(sources[g])})
    # Un n-gramme inclus dans un plus long de meme frequence est redondant
    out = [c for c in out if not any(o is not c and c["ngram"] in o["ngram"] and o["recent_count"] == c["recent_count"]
                                     for o in out)]
    out.sort(key=lambda c: (-c["rise_ratio"] * c["recent_count"], c["ngram"]))
    return out[:p["max_candidates"]]


def group_candidates(candidates, vectors=None):
    """
    Regroupe les candidats. Avec des embeddings (Ollama, optionnel) : similarite
    cosinus. Sinon repli sur les n-grammes : un candidat rejoint le groupe d'un
    candidat avec lequel il partage un mot. Retourne (candidats, methode).
    """
    if vectors is not None and len(vectors) == len(candidates):
        mats = [np.asarray(v, dtype=float) for v in vectors]
        method = "embeddings"

        def similar(i, j):
            a, b = mats[i], mats[j]
            den = float(np.linalg.norm(a) * np.linalg.norm(b))
            return den > 0 and float(a @ b) / den >= config.DISCOVER["embed_similarity"]
    else:
        method = "ngrammes"
        words = [set(c["ngram"].split()) for c in candidates]

        def similar(i, j):
            return bool(words[i] & words[j])
    leaders = []
    for i, c in enumerate(candidates):
        leader = next((j for j in leaders if similar(i, j)), None)
        if leader is None:
            leaders.append(i)
            c["group_label"] = c["ngram"]
        else:
            c["group_label"] = candidates[leader]["ngram"]
    return candidates, method
