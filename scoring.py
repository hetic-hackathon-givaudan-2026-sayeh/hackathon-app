"""
Scores : durabilite (RG04 a RG06), Client Fit (RG07), timing et fenetre de tir (RG09, RG10), opportunite, confiance, alertes (RG11 a RG13).
"""

import math
import statistics
from datetime import date, timedelta

import analytics
import config


# ===========================================================================
# Section : ex scoring.py
# Scores TrendPulse : calcul pur, sans base ni reseau.
#
# Convention : chaque critere est une valeur entre 0 et 1, ou None s'il n'est
# pas calculable (RG05). Les scores restent non arrondis (RG11), l'arrondi se
# fait seulement a l'affichage.
# ===========================================================================

# ---------------------------------------------------------------------------
# Outils generiques
# ---------------------------------------------------------------------------


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def criterion(value, weight, source=None, detail=None):
    """Critere au format stocke dans trend_scores.criteria (RG14)."""
    return {
        "value": value,
        "weight": weight,
        "available": value is not None,
        "source": source,
        "detail": detail or {},
    }


def renormalize(criteria):
    """
    RG04 et RG05 : somme ponderee des criteres disponibles, divisee par la
    somme de leurs poids. Retourne (score sur 100 ou None, criteres manquants,
    part du poids total disponible).
    """
    total_weight = sum(c["weight"] for c in criteria.values())
    available = {k: c for k, c in criteria.items() if c["value"] is not None}
    missing = sorted(k for k in criteria if criteria[k]["value"] is None)
    weight = sum(c["weight"] for c in available.values())
    if weight == 0 or total_weight == 0:
        return None, missing, 0.0
    score = sum(c["value"] * c["weight"] for c in available.values()) / weight * 100
    return score, missing, weight / total_weight


def classify(score, levels):
    """Retourne le libelle du premier seuil atteint (levels trie par seuil decroissant)."""
    if score is None:
        return None
    for threshold, label in levels:
        if score >= threshold:
            return label
    return levels[-1][1]


# ---------------------------------------------------------------------------
# Series hebdomadaires
# ---------------------------------------------------------------------------


def normalize_series(values):
    """Ramene une serie sur 0 a 100 par rapport a son maximum."""
    peak = max(values) if values else 0
    if peak <= 0:
        return [0.0 for _ in values]
    return [v / peak * 100 for v in values]


def activity_series(series_by_source, weeks):
    """
    Combine plusieurs series {semaine: volume} en une activite moyenne 0 a 100.
    Chaque serie est normalisee par son propre maximum. Une semaine absente
    d'une serie n'entre pas dans la moyenne de cette semaine (RG05).
    """
    normalized = []
    for series in series_by_source:
        peak = max(series.values()) if series else 0
        normalized.append({w: (v / peak * 100 if peak > 0 else 0.0) for w, v in series.items()})
    result = []
    for w in weeks:
        vals = [s[w] for s in normalized if w in s]
        if vals:
            result.append(sum(vals) / len(vals))
    return result


# ---------------------------------------------------------------------------
# RG04 criteres de durabilite
# ---------------------------------------------------------------------------


def persistence(values, threshold=None, target_weeks=None):
    """Semaines consecutives au-dessus du seuil, comptees depuis la derniere semaine."""
    if not values:
        return None, {"streak_weeks": None}
    threshold = config.DURABILITY["persistence_threshold"] if threshold is None else threshold
    target_weeks = target_weeks or config.DURABILITY["persistence_target_weeks"]
    streak = 0
    for v in reversed(values):
        if v >= threshold:
            streak += 1
        else:
            break
    return clamp(streak / target_weeks), {"streak_weeks": streak, "threshold": threshold}


def _linear_fit(values):
    n = len(values)
    xs = list(range(n))
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / sxx if sxx else 0.0
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, values)]
    return slope, mean_y, residuals


def regularity(values):
    """
    Pente de regression sur 12 a 36 semaines, moins une penalite de volatilite.
    Pente relative de +scale %/semaine ou plus = 1, -scale ou moins = 0.
    """
    p = config.DURABILITY
    window = values[-p["regularity_max_weeks"]:]
    if len(window) < p["regularity_min_weeks"]:
        return None, {"weeks": len(window), "reason": "moins de 12 semaines"}
    slope, mean_y, residuals = _linear_fit(window)
    if mean_y <= 0:
        return 0.0, {"weeks": len(window), "slope_pct": 0.0, "volatility_penalty": 0.0}
    slope_pct = slope / mean_y * 100
    slope_score = clamp(0.5 + slope_pct / (2 * p["regularity_slope_scale_pct"]))
    cv = statistics.pstdev(residuals) / mean_y
    penalty = min(cv * p["volatility_factor"], p["volatility_max_penalty"])
    return clamp(slope_score - penalty), {
        "weeks": len(window), "slope_pct": slope_pct, "volatility_cv": cv, "volatility_penalty": penalty,
    }


def diversity(active_types, responded_types):
    """
    RG03 : types de sources distincts actifs. None si aucune source comptee
    dans la diversite n'a repondu avec succes.
    """
    allowed = set(config.DIVERSITY_SOURCE_TYPES)
    responded = set(responded_types) & allowed
    if not responded:
        return None, {"active_types": []}
    active = sorted(set(active_types) & allowed)
    value = clamp(len(active) / config.DURABILITY["diversity_target_types"])
    return value, {"active_types": active, "responded_types": sorted(responded)}


def diffusion(markets, categories, markets_available=True):
    """Marches distincts (hors ZZ) et categories distinctes. Une partie absente sort du calcul."""
    p = config.DURABILITY
    parts = []
    detail = {}
    if markets_available and markets is not None:
        real = sorted(set(markets) - {config.GLOBAL_MARKET})
        parts.append(clamp(len(real) / p["diffusion_target_markets"]))
        detail["markets"] = real
    if categories is not None:
        cats = sorted(set(categories))
        parts.append(clamp(len(cats) / p["diffusion_target_categories"]))
        detail["categories"] = cats
    if not parts:
        return None, detail
    return sum(parts) / len(parts), detail


def early_adoption(has_early_signal, early_sources_responded):
    """1 si signal recherche, brevet ou lancement par une marque referente. None si aucune de ces sources n'a repondu."""
    if not early_sources_responded:
        return None, {}
    return (1.0 if has_early_signal else 0.0), {"early_signal": bool(has_early_signal)}


# ---------------------------------------------------------------------------
# RG06 penalite evenementielle
# ---------------------------------------------------------------------------


def detect_isolated_spike(values):
    """
    Pic isole : pic > k x mediane, puis retention faible sur les semaines
    suivantes. Un pic trop recent pour mesurer la retention n'est pas juge.
    Retourne (pic_isole, index_du_pic, detail).
    """
    p = config.EVENT_PENALTY
    if len(values) < p["min_weeks"]:
        return False, None, {"reason": "serie trop courte"}
    base = statistics.median(values)
    if base <= 0:
        positives = [v for v in values if v > 0]
        base = statistics.mean(positives) / p["spike_k"] if len(positives) > 1 else 0
    if base <= 0:
        return False, None, {"reason": "serie nulle"}
    peak_idx = max(range(len(values)), key=lambda i: values[i])
    peak = values[peak_idx]
    if peak <= p["spike_k"] * base:
        return False, peak_idx, {"peak_ratio": peak / base}
    after = values[peak_idx + 1: peak_idx + 1 + p["retention_weeks"]]
    if len(after) < p["retention_weeks"]:
        return False, peak_idx, {"peak_ratio": peak / base, "reason": "pic trop recent, retention non mesurable"}
    retention = (sum(after) / len(after)) / peak
    return retention < p["retention_max"], peak_idx, {"peak_ratio": peak / base, "retention": retention}


def event_penalty(is_spike, peak_week, events):
    """
    RG06 : -15 si le pic isole correspond a un evenement date connu, -5 si
    aucun evenement connu, 0 sans pic isole.
    """
    p = config.EVENT_PENALTY
    if not is_spike or peak_week is None:
        return 0, {"spike": False}
    window = timedelta(days=p["event_match_days"])
    for ev in events or []:
        ev_date = ev["event_date"]
        if isinstance(ev_date, str):
            ev_date = date.fromisoformat(ev_date)
        if abs(ev_date - peak_week) <= window:
            return p["with_known_event"], {"spike": True, "peak_week": peak_week.isoformat(), "event": ev.get("event_label")}
    return p["without_known_event"], {"spike": True, "peak_week": peak_week.isoformat(), "event": None}


# ---------------------------------------------------------------------------
# RG04 score final
# ---------------------------------------------------------------------------


def durability_score(criteria, penalty=0):
    """Renormalise puis soustrait la penalite. Score non arrondi, borne a 0 et 100."""
    raw, missing, share = renormalize(criteria)
    if raw is None:
        return {"score": None, "raw": None, "penalty": penalty, "classification": None,
                "missing": missing, "available_weight_share": share}
    score = clamp(raw - penalty, 0.0, 100.0)
    return {"score": score, "raw": raw, "penalty": penalty,
            "classification": classify(score, config.DURABILITY_CLASSES),
            "missing": missing, "available_weight_share": share}


def build_durability_criteria(values):
    """values : dict nom du critere -> (valeur, detail, source)."""
    weights = config.DURABILITY_WEIGHTS
    return {name: criterion(values[name][0], weights[name], values[name][2], values[name][1]) for name in weights}


# ---------------------------------------------------------------------------
# RG12 Confidence Score
# ---------------------------------------------------------------------------


def freshness(days_since_last):
    p = config.CONFIDENCE
    if days_since_last is None:
        return 0.0
    if days_since_last <= p["fresh_full_days"]:
        return 1.0
    if days_since_last >= p["fresh_zero_days"]:
        return 0.0
    return 1 - (days_since_last - p["fresh_full_days"]) / (p["fresh_zero_days"] - p["fresh_full_days"])


def confidence_score(n_source_types, observation_weeks, days_since_last, market_covered,
                     comparable_cases, client_data_source, criteria_weight_share):
    """Formule RG12, poids dans config.CONFIDENCE_WEIGHTS. Retourne (score, detail)."""
    p = config.CONFIDENCE
    w = config.CONFIDENCE_WEIGHTS
    parts = {
        "types_sources": clamp((n_source_types or 0) / p["types_target"]),
        "profondeur": clamp((observation_weeks or 0) / p["depth_target_weeks"]),
        "fraicheur": freshness(days_since_last),
        "couverture_marche": 1.0 if market_covered else 0.0,
        "cas_comparables": clamp((comparable_cases or 0) / p["cases_target"]),
        "source_client": p["client_source_values"].get(str(client_data_source or "absent"), 0.0),
        "criteres_calculables": clamp(criteria_weight_share or 0.0),
    }
    score = sum(parts[k] * w[k] for k in w) / sum(w.values()) * 100
    return score, parts


# ---------------------------------------------------------------------------
# Phase C : Trend Durability Score v2 (RG24, RG25) et Confidence v2 (RG29)
# Memes poids que la fiche, meme renormalisation. Persistance et regularite
# viennent de la serie desaisonnalisee (analytics.py).
# ---------------------------------------------------------------------------


def _v2_criteria(pers, reg, others):
    """others : dict diversite, diffusion, adoption_precoce -> (valeur, detail, source), identiques a la v1."""
    return build_durability_criteria({
        "persistance": (pers[0], pers[1], "serie desaisonnalisee v2"),
        "regularite": (reg[0], reg[1], "serie desaisonnalisee v2"),
        **others,
    })


def durability_v2(values, weeks, others, calendar_weeks=(), events=(), n_boot=None, seed=None):
    """
    Score v2 avec intervalle bootstrap par blocs (RG25). La classe affichee
    reste celle du score central. class_uncertain si l'intervalle chevauche un seuil.
    """
    ind = analytics.indicators_v2(values, weeks, calendar_weeks, events)
    crit = _v2_criteria(ind["persistance"], ind["regularite"], others)
    result = durability_score(crit, ind["penalty"])
    samples = []
    if result["score"] is not None and len(ind["window_values"]) >= 2:
        for serie in analytics.block_bootstrap_series(ind["window_values"], n_boot, seed=seed):
            peak = analytics.peak_retention(serie, ind["window_weeks"], calendar_weeks)
            pen = analytics.event_penalty_v2(peak, events)[0]
            c = _v2_criteria(analytics.persistence_v2(serie), analytics.regularity_v2(serie), others)
            samples.append(durability_score(c, pen)["score"])
    low, high, boot = analytics.interval_from_samples(result["score"], samples)
    uncertain = low is not None and analytics.class_uncertain(low, high)
    flags = list(ind["flags"]) + (["class_uncertain"] if uncertain else [])
    crit["_penalite"] = {"value": ind["penalty"], "detail": {**ind["penalty_detail"], "peak": ind["peak"]}}
    crit["_v2"] = {"season": ind["season"], "trend_start": ind["start"], "bootstrap": boot,
                   "score_low": low, "score_high": high, "flags": flags}
    return {**result, "criteria": crit, "score_low": low, "score_high": high,
            "class_uncertain": uncertain, "flags": flags, "indicators": ind}


def confidence_v2(n_source_types, observation_weeks, days_since_last, market_covered, verified_cases,
                  client_data_source, criteria_weight_share, score_low, score_high, flags):
    """RG29 : poids dans config.CONFIDENCE_V2_WEIGHTS, penalite si seasonality_unknown ou short_history."""
    p = config.CONFIDENCE
    w = config.CONFIDENCE_V2_WEIGHTS
    width = (score_high - score_low) if score_low is not None and score_high is not None else None
    parts = {
        "types_sources": clamp((n_source_types or 0) / p["types_target"]),
        "profondeur": clamp((observation_weeks or 0) / p["depth_target_weeks"]),
        "fraicheur": freshness(days_since_last),
        "couverture_marche": 1.0 if market_covered else 0.0,
        "analogues": clamp((verified_cases or 0) / p["cases_target"]),
        "source_client": p["client_source_values"].get(str(client_data_source or "absent"), 0.0),
        "criteres_calculables": clamp(criteria_weight_share or 0.0),
        # Intervalle inconnu : aucune precision revendiquee (RG05)
        "precision_intervalle": clamp(1 - width / 100) if width is not None else 0.0,
    }
    score = sum(parts[k] * w[k] for k in w) / sum(w.values()) * 100
    penalty = sum(v for k, v in config.CONFIDENCE_V2_FLAG_PENALTY.items() if k in (flags or []))
    parts["_penalite_flags"] = penalty
    return clamp(score - penalty, 0.0, 100.0), parts


# ---------------------------------------------------------------------------
# RG11 Opportunity et RG13 alertes
# ---------------------------------------------------------------------------


def opportunity_score(durability, fit, timing):
    """
    0,45 x Durability + 0,40 x Fit + 0,15 x Timing, valeur non arrondie.
    Une composante indisponible sort du calcul et les poids sont renormalises (RG05).
    """
    w = config.OPPORTUNITY_WEIGHTS
    values = {"durability": durability, "fit": fit, "timing": timing}
    available = {k: v for k, v in values.items() if v is not None}
    missing = sorted(k for k, v in values.items() if v is None)
    weight = sum(w[k] for k in available)
    if weight == 0:
        return None, missing
    return sum(w[k] * v for k, v in available.items()) / weight, missing


def opportunity_level(score):
    return classify(score, config.OPPORTUNITY_LEVELS)


def fit_level(score):
    return classify(score, config.FIT_LEVELS)


def alert_decision(durability, fit, opportunity, confidence):
    """
    RG13 : alerte seulement si les quatre seuils sont atteints.
    Si seule la confiance manque : watchlist avec message de preuves insuffisantes.
    Sinon aucune ligne d'alerte (la tendance reste visible dans les matches).
    Retourne (statut ou None, instantane des conditions).
    """
    t = config.ALERT_THRESHOLDS
    values = {"durability": durability, "fit": fit, "opportunity": opportunity, "confidence": confidence}
    conditions = {k: {"value": v, "threshold": t[k], "ok": v is not None and v >= t[k]} for k, v in values.items()}
    business_ok = all(conditions[k]["ok"] for k in ("durability", "fit", "opportunity"))
    if business_ok and conditions["confidence"]["ok"]:
        return "ALERT", {"conditions": conditions, "message": None}
    if business_ok:
        return "WATCHLIST", {"conditions": conditions, "message": config.LOW_CONFIDENCE_MESSAGE}
    return None, {"conditions": conditions, "message": None}




# ---------------------------------------------------------------------------
# Phase D : paniers de recommandation (RG33) et signaux contraires (RG35)
# Fonctions pures, seuils dans config.
# ---------------------------------------------------------------------------

BUCKET_LEVEL = {"a_proposer": 3, "a_surveiller": 2, "ecartees": 1, None: 0, "sorti": 0}


def rg13_conditions(durability, fit, opportunity, confidence):
    """Les 4 conditions RG13 avec valeur, seuil, statut et ecart au seuil."""
    t = config.ALERT_THRESHOLDS
    values = {"durability": durability, "fit": fit, "opportunity": opportunity, "confidence": confidence}
    out = []
    for key, value in values.items():
        met = value is not None and value >= t[key]
        gap = None if value is None else max(0.0, t[key] - value)
        out.append({"key": key, "condition": config.CONDITION_LABELS[key], "value": value,
                    "threshold": t[key], "met": met, "gap": gap})
    return out


def recommendation_bucket(conditions, popular):
    """
    RG33 : "a_proposer" si les 4 conditions sont remplies. "a_surveiller" si
    une seule manque avec un ecart inferieur a SURVEILLER_MARGIN. "ecartees"
    si la tendance est populaire et ne remplit pas la RG13. Sinon aucun panier.
    Retourne (panier ou None, conditions manquantes).
    """
    missing = [c for c in conditions if not c["met"]]
    if not missing:
        return "a_proposer", []
    if len(missing) == 1 and missing[0]["gap"] is not None and missing[0]["gap"] < config.SURVEILLER_MARGIN:
        return "a_surveiller", missing
    if popular:
        return "ecartees", missing
    return None, missing


def recent_rise(values):
    """Hausse recente du signal sur la fenetre : pic divise par la mediane, moins 1. None si insuffisant."""
    if len(values) < 8:
        return None
    med = statistics.median(values)
    if med <= 0:
        return None
    return max(values) / med - 1


def popular_keys(rises):
    """Cles (tendance, marche) dont la hausse recente est dans le top POPULAR_TOP_PCT."""
    known = sorted(v for v in rises.values() if v is not None and v > 0)
    if not known:
        return set()
    n_top = max(1, math.ceil(len(known) * config.POPULAR_TOP_PCT / 100))
    threshold = known[-n_top]
    return {k for k, v in rises.items() if v is not None and v >= threshold}


def counter_evidence(retention=None, n_source_types=None, launches_recent=None, launches_previous=None,
                     flags=(), unavailable_sources=()):
    """RG35 : signaux contraires calcules en code. Jamais vide, jamais "aucun risque"."""
    out = []
    if retention is not None and retention < config.RETENTION_MID:
        out.append("rétention après pic en baisse (" + str(round(retention * 100)) + " % du niveau du pic)")
    if n_source_types is not None and n_source_types <= 1:
        out.append("un seul type de source actif")
    if launches_recent is not None and launches_previous is not None and launches_recent > launches_previous:
        out.append("concurrence en hausse : " + str(int(launches_recent)) + " lancements sur 13 semaines contre "
                   + str(int(launches_previous)) + " avant")
    labels = {"seasonality_unknown": "saisonnalité inconnue (historique inférieur à 104 semaines)",
              "short_history": "historique court (moins de 52 semaines)",
              "class_uncertain": "classe incertaine : l'intervalle du score chevauche un seuil"}
    for flag in flags or []:
        if flag in labels:
            out.append(labels[flag])
    if unavailable_sources:
        out.append("sources indisponibles : " + ", ".join(sorted(unavailable_sources)))
    return out or [config.NO_COUNTER_EVIDENCE]


def change_vs_last(previous, current):
    """
    Evolution d'une recommandation entre deux runs. previous et current :
    (panier, opportunite) ou None. nouveau, monte, stable, baisse ou sorti.
    """
    if previous is None and current is None:
        return None
    if previous is None or BUCKET_LEVEL.get(previous[0], 0) == 0:
        return "nouveau"
    if current is None:
        return "sorti"
    before, after = BUCKET_LEVEL[previous[0]], BUCKET_LEVEL[current[0]]
    if after != before:
        return "monte" if after > before else "baisse"
    if previous[1] is None or current[1] is None:
        return "stable"
    delta = current[1] - previous[1]
    if delta > config.CHANGE_TOLERANCE:
        return "monte"
    if delta < -config.CHANGE_TOLERANCE:
        return "baisse"
    return "stable"


def explain_contributions(criteria, penalty, score):
    """
    RG31 : contribution de chaque critere (poids, valeur, points). Points =
    valeur x poids / poids disponibles x 100, penalite en negatif. Si le score
    est borne a 0 ou 100, une ligne d'ajustement garde la somme egale au score.
    """
    weights = config.DURABILITY_WEIGHTS
    available = sum(criteria[k]["weight"] for k in weights if k in criteria and criteria[k]["value"] is not None)
    items = []
    for k in weights:
        c = criteria.get(k) or {"value": None, "weight": weights[k]}
        pts = c["value"] * c["weight"] / available * 100 if c["value"] is not None and available else 0.0
        items.append({"criterion": k, "weight": c["weight"], "value": c["value"],
                      "available": c["value"] is not None, "points": pts})
    if penalty:
        items.append({"criterion": "penalite_evenementielle", "weight": None, "value": penalty,
                      "available": True, "points": -float(penalty)})
    total = sum(i["points"] for i in items)
    if score is not None and abs(total - score) > 1e-9:
        items.append({"criterion": "borne_0_100", "weight": None, "value": None, "available": True,
                      "points": score - total})
    return items


# ===========================================================================
# Section : ex matching.py
# Client Fit Score (RG07). Calcul pur.
#
# Chaque critere est decompose en sous-parties. Une sous-partie sans donnee
# sort du calcul (RG05). Un critere sans aucune sous-partie disponible est
# liste dans missing et les poids sont renormalises.
# ===========================================================================

def _parts(parts):
    """parts : liste (valeur ou None, poids). Moyenne ponderee des parties disponibles."""
    avail = [(v, w) for v, w in parts if v is not None]
    if not avail:
        return None
    return sum(v * w for v, w in avail) / sum(w for _, w in avail)


def _overlap(trend_items, client_items):
    """Part des elements de la tendance retrouves chez le client. None si la tendance n'en a pas."""
    if not trend_items:
        return None
    t = {x.lower() for x in trend_items}
    c = {x.lower() for x in (client_items or [])}
    return len(t & c) / len(t)


def _price_fit(trend_tier, client_tier):
    tiers = config.PRICE_TIERS
    if trend_tier not in tiers or client_tier not in tiers:
        return None
    gap = abs(tiers.index(trend_tier) - tiers.index(client_tier))
    return {0: 1.0, 1: 0.5}.get(gap, 0.0)


def _age_fit(trend_range, client_range):
    if not trend_range or not client_range:
        return None
    lo, hi = max(trend_range[0], client_range[0]), min(trend_range[1], client_range[1])
    span = trend_range[1] - trend_range[0]
    if span <= 0:
        return None
    return clamp((hi - lo) / span)


def _parse_month(value):
    if not value:
        return None
    parts = [int(x) for x in str(value).split("-")]
    return date(parts[0], parts[1] if len(parts) > 1 else 1, 1)


def _launch_window_fit(window, today):
    """1 si la prochaine fenetre de lancement s'ouvre dans l'horizon, 0 si elle est passee."""
    if not window or not window.get("start"):
        return None
    start = _parse_month(window["start"])
    end = _parse_month(window.get("end")) or start
    if end < date(today.year, today.month, 1):
        return 0.0
    months = (start.year - today.year) * 12 + start.month - today.month
    return 1.0 if months <= config.NEXT_LAUNCH_HORIZON_MONTHS else 0.5


def client_fit(trend, client, market, window, today):
    """
    trend : dict (id, label, category, profile). client : dict (champs RG07).
    market : code ISO ou None. window : sortie de timing.analog_window ou None.
    Retourne {score, level, criteria, missing}.
    """
    w = config.FIT_WEIGHTS
    profile = trend.get("profile") or {}
    label = trend.get("label", "").lower()

    brand = _parts([
        (_price_fit(profile.get("price_tier"), client.get("price_tier")), 0.5),
        (_overlap(profile.get("claim_types"),
                  list(client.get("brand_values") or []) + [client.get("brand_positioning") or ""]), 0.5),
    ])
    target = _parts([
        (_overlap(profile.get("target_segments"), client.get("target_segments")), 0.6),
        (_age_fit(profile.get("target_age_range"), client.get("target_age_range")), 0.4),
    ])
    cats = client.get("product_categories") or []
    launches = client.get("recent_launches") or []
    already = any(label in [i.lower() for i in (l.get("ingredients") or [])] for l in launches)
    portfolio = _parts([
        ((1.0 if trend.get("category") in cats else 0.0) if cats else None, 0.7),
        ((0.0 if already else 1.0) if launches else None, 0.3),
    ])
    market_fit = None
    if market and client.get("active_markets") is not None:
        market_fit = 1.0 if market in client["active_markets"] else 0.0
    potential = _parts([
        (config.ACCOUNT_SIZE_VALUES.get(client.get("account_size_tier")), 0.7),
        ((1.0 if client["strategic_flag"] else 0.0) if client.get("strategic_flag") is not None else None, 0.3),
    ])
    cycle = client.get("innovation_cycle_months")
    cycle_part = None
    if cycle is not None and window and window.get("low") is not None:
        cycle_part = 1.0 if cycle <= window["high"] else 0.0
    capacity = _parts([
        (cycle_part, 0.4),
        (_launch_window_fit(client.get("next_launch_window"), today), 0.3),
        (config.RELATIONSHIP_VALUES.get(client.get("relationship_status")), 0.3),
    ])

    crit = {
        "alignement_marque": criterion(brand, w["alignement_marque"], "clients+profil_estime"),
        "cible": criterion(target, w["cible"], "clients+profil_estime"),
        "portefeuille": criterion(portfolio, w["portefeuille"], "clients", {"deja_lance": already}),
        "marche": criterion(market_fit, w["marche"], "clients", {"market": market}),
        "potentiel": criterion(potential, w["potentiel"], "clients"),
        "capacite_agir": criterion(capacity, w["capacite_agir"], "clients",
                                   {"innovation_cycle_months": cycle, "parametre_reglable": True}),
    }
    score, missing, share = renormalize(crit)
    return {"score": score, "level": fit_level(score), "criteria": crit, "missing": missing,
            "client_data_source": client.get("client_data_source")}



# ===========================================================================
# Section : ex timing.py
# Market Timing Score (RG09) et fenetre de tir (RG10). Calcul pur.
#
# Fenetre de tir : toujours un intervalle de delais en mois issu de cas
# comparables verifies (verified=true et source_url). Sous 3 cas, aucune
# fenetre n'est calculee et le message "Donnees comparables insuffisantes"
# est affiche. Aucun cas n'est invente.
# ===========================================================================

def eligible_cases(cases, sector):
    """Cas utilisables : meme secteur, verifies, avec URL et delai observe."""
    return [c for c in cases
            if c["sector"] == sector and c.get("verified") and c.get("source_url")
            and c.get("observed_delay_months") is not None]


def analog_window(cases, sector, target_market=None):
    """
    Retourne {low, high, count, case_ids, confidence_level, message}.
    Si au moins 3 cas visent le marche cible, ils sont preferes aux cas du secteur.
    """
    pool = eligible_cases(cases, sector)
    local = [c for c in pool if target_market and c.get("target_market") == target_market]
    if len(local) >= config.ANALOG["min_cases"]:
        pool = local
    ids = sorted(c["case_id"] for c in pool)
    n = len(pool)
    if n < config.ANALOG["min_cases"]:
        return {"low": None, "high": None, "count": n, "case_ids": ids,
                "confidence_level": "faible", "message": config.INSUFFICIENT_ANALOGS_MESSAGE}
    delays = sorted(float(c["observed_delay_months"]) for c in pool)
    if n >= 4:
        q = statistics.quantiles(delays, n=4, method="inclusive")
        low, high = q[0], q[2]
    else:
        low, high = delays[0], delays[-1]
    level = "élevé" if n >= config.ANALOG["high_confidence_cases"] else "moyen"
    return {"low": low, "high": high, "count": n, "case_ids": ids, "confidence_level": level, "message": None}


def local_growth(values, weeks=None):
    """Croissance en % : moyenne des N dernieres semaines vs N precedentes."""
    weeks = weeks or config.TIMING["growth_weeks"]
    if len(values) < 2 * weeks:
        return None
    recent = values[-weeks:]
    previous = values[-2 * weeks:-weeks]
    prev = sum(previous) / weeks
    if prev <= 0:
        return None
    return (sum(recent) / weeks - prev) / prev * 100


def maturity_stage(values, growth, launch_count):
    """Stade signal_faible, emergent, croissance, maturite ou declin (seuils config, A VALIDER)."""
    p = config.TIMING
    if len(values) < p["min_weeks"] or growth is None:
        return "signal_faible"
    if growth >= p["emergent_growth_min"] and (launch_count is None or launch_count < p["emergent_max_launches"]):
        return "emergent"
    if growth >= p["croissance_growth_min"]:
        return "croissance"
    if growth >= p["maturite_growth_min"]:
        return "maturite"
    return "declin"


def client_cycle_fit(cycle_months, window):
    """1 si le client sort un produit avant le debut de la fenetre, 0,5 avant la fin, 0 sinon."""
    if cycle_months is None or window is None or window.get("low") is None:
        return None
    if cycle_months <= window["low"]:
        return 1.0
    if cycle_months <= window["high"]:
        return 0.5
    return 0.0


def timing_score(stage, growth, competitor_count, competition_available, cycle_fit):
    """Score RG09 renormalise sur les criteres disponibles. Retourne (score, criteres, manquants)."""
    w = config.TIMING_WEIGHTS
    p = config.TIMING
    growth_value = None
    if growth is not None:
        growth_value = clamp((growth - p["growth_floor_pct"]) / (p["growth_cap_pct"] - p["growth_floor_pct"]))
    competition_value = None
    if competition_available:
        competition_value = 1 - clamp((competitor_count or 0) / p["competition_saturation"])
    crit = {
        "maturite": criterion(config.MATURITY_POINTS.get(stage) if stage else None, w["maturite"], "weekly_signals", {"stage": stage}),
        "croissance_locale": criterion(growth_value, w["croissance_locale"], "weekly_signals", {"growth_pct": growth}),
        "concurrence": criterion(competition_value, w["concurrence"], "lancement_produit", {"launch_count": competitor_count}),
        "cycle_client": criterion(cycle_fit, w["cycle_client"], "clients", {}),
    }
    score, missing, _ = renormalize(crit)
    return score, crit, missing
