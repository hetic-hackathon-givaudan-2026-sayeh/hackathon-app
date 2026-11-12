"""
Tests des scores et du backtest, sans reseau.
"""

from datetime import date, timedelta

import pytest

import collect
import config
import conftest
import run as runner
import scoring


# ===========================================================================
# Section : ex test_scoring.py
# Tests des regles de scoring. Calcul pur, sans reseau.
# ===========================================================================

def _criteria(values):
    """values : nom -> valeur (0 a 1) ou None."""
    return {name: scoring.criterion(values.get(name), w) for name, w in config.DURABILITY_WEIGHTS.items()}


# ---------------------------------------------------------------------------
# RG04 et RG05 renormalisation
# ---------------------------------------------------------------------------


def test_les_cinq_poids_font_95():
    assert sum(config.DURABILITY_WEIGHTS.values()) == 95


def test_renormalisation_par_la_somme_des_poids_disponibles():
    crit = _criteria({"persistance": 1.0, "regularite": 0.5, "diversite": None,
                      "diffusion": 0.4, "adoption_precoce": 0.0})
    score, missing, share = scoring.renormalize(crit)
    # (30 x 1 + 20 x 0,5 + 15 x 0,4 + 10 x 0) / 75 x 100
    assert score == pytest.approx(46 / 75 * 100)
    assert missing == ["diversite"]
    assert share == pytest.approx(75 / 95)


def test_tous_criteres_disponibles_divise_par_95():
    crit = _criteria({k: 1.0 for k in config.DURABILITY_WEIGHTS})
    score, missing, share = scoring.renormalize(crit)
    assert score == pytest.approx(100)
    assert missing == []
    assert share == pytest.approx(1.0)


def test_critere_manquant_exclu_et_non_compte_comme_zero():
    base = {"persistance": 0.8, "regularite": 0.8, "diffusion": 0.8, "adoption_precoce": 0.8}
    with_none = scoring.durability_score(_criteria({**base, "diversite": None}))
    with_zero = scoring.durability_score(_criteria({**base, "diversite": 0.0}))
    assert with_none["score"] == pytest.approx(80)
    assert "diversite" in with_none["missing"]
    assert with_zero["score"] < with_none["score"]
    assert with_zero["missing"] == []


def test_aucun_critere_calculable_donne_none():
    result = scoring.durability_score(_criteria({}))
    assert result["score"] is None
    assert result["classification"] is None
    assert len(result["missing"]) == 5


# ---------------------------------------------------------------------------
# RG06 penalite
# ---------------------------------------------------------------------------


def test_penalite_soustraite_apres_renormalisation():
    crit = _criteria({"persistance": 1.0, "regularite": 0.5, "diversite": None,
                      "diffusion": 0.4, "adoption_precoce": 0.0})
    result = scoring.durability_score(crit, penalty=15)
    assert result["score"] == pytest.approx(46 / 75 * 100 - 15)
    assert result["classification"] == "Weak Signal"


def test_score_borne_a_zero_par_la_penalite():
    result = scoring.durability_score(_criteria({k: 0.05 for k in config.DURABILITY_WEIGHTS}), penalty=15)
    assert result["score"] == 0.0


def _spike_series():
    return [10.0] * 20 + [100.0] + [12.0] * 6


def test_pic_isole_detecte():
    is_spike, idx, detail = scoring.detect_isolated_spike(_spike_series())
    assert is_spike is True
    assert idx == 20
    assert detail["retention"] < config.EVENT_PENALTY["retention_max"]


def test_pic_suivi_de_retention_non_isole():
    series = [10.0] * 20 + [100.0] + [80.0] * 6
    assert scoring.detect_isolated_spike(series)[0] is False


def test_pic_trop_recent_non_juge():
    series = [10.0] * 20 + [100.0, 12.0]
    is_spike, _, detail = scoring.detect_isolated_spike(series)
    assert is_spike is False
    assert "retention" in detail["reason"]


def test_penalite_15_avec_evenement_5_sans_evenement_0_sans_pic():
    peak = date(2023, 12, 18)
    events = [{"event_date": date(2023, 12, 19), "event_label": "video virale"}]
    assert scoring.event_penalty(True, peak, events)[0] == 15
    assert scoring.event_penalty(True, peak, [])[0] == 5
    far = [{"event_date": peak + timedelta(days=90), "event_label": "autre"}]
    assert scoring.event_penalty(True, peak, far)[0] == 5
    assert scoring.event_penalty(False, peak, events)[0] == 0


# ---------------------------------------------------------------------------
# Seuils de classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("score,label", [
    (0, "Noise/Buzz"), (39.99, "Noise/Buzz"), (40, "Weak Signal"), (59.99, "Weak Signal"),
    (60, "Emerging Trend"), (74.99, "Emerging Trend"), (75, "Validated Trend"),
    (89.99, "Validated Trend"), (90, "Accelerating Trend"), (100, "Accelerating Trend"),
])
def test_seuils_durability(score, label):
    assert scoring.classify(score, config.DURABILITY_CLASSES) == label


@pytest.mark.parametrize("score,label", [
    (49.99, "Low"), (50, "Potential"), (64.99, "Potential"), (65, "Strong"), (79.99, "Strong"), (80, "Priority"),
])
def test_seuils_fit(score, label):
    assert scoring.fit_level(score) == label


@pytest.mark.parametrize("score,label", [
    (54.99, "Low Priority"), (55, "Watchlist"), (69.99, "Watchlist"), (70, "Recommended"),
    (84.99, "Recommended"), (85, "High Priority"),
])
def test_seuils_opportunity(score, label):
    assert scoring.opportunity_level(score) == label


# ---------------------------------------------------------------------------
# RG11 Opportunity
# ---------------------------------------------------------------------------


def test_exemple_opportunity_78_92_88():
    score, missing = scoring.opportunity_score(78, 92, 88)
    assert score == pytest.approx(85.1)
    assert round(score, 1) == 85.1
    assert missing == []
    assert scoring.opportunity_level(score) == "High Priority"


def test_opportunity_valeur_non_arrondie():
    score, _ = scoring.opportunity_score(77.77, 91.11, 66.66)
    assert score == 0.45 * 77.77 + 0.40 * 91.11 + 0.15 * 66.66


def test_opportunity_timing_indisponible_renormalise():
    score, missing = scoring.opportunity_score(80, 70, None)
    assert missing == ["timing"]
    assert score == pytest.approx((0.45 * 80 + 0.40 * 70) / 0.85)


# ---------------------------------------------------------------------------
# RG13 alerte a quatre conditions
# ---------------------------------------------------------------------------


def test_alerte_si_quatre_conditions():
    status, snap = scoring.alert_decision(60, 65, 70, 70)
    assert status == "ALERT"
    assert all(c["ok"] for c in snap["conditions"].values())


@pytest.mark.parametrize("values", [(59.9, 80, 80, 80), (80, 64.9, 80, 80), (80, 80, 69.9, 80)])
def test_pas_alerte_si_condition_metier_manque(values):
    status, snap = scoring.alert_decision(*values)
    assert status is None


def test_confiance_insuffisante_donne_watchlist_et_message():
    status, snap = scoring.alert_decision(80, 80, 80, 69.9)
    assert status == "WATCHLIST"
    assert snap["message"] == "Opportunité détectée, preuves insuffisantes avant contact client."


def test_valeur_indisponible_ne_valide_pas_une_condition():
    status, _ = scoring.alert_decision(None, 80, 80, 80)
    assert status is None


# ---------------------------------------------------------------------------
# RG12 Confidence
# ---------------------------------------------------------------------------


def test_confiance_cas_bien_documente_environ_89():
    # 4 types, 52 semaines, 3 jours, marche couvert, 4 cas, client simule,
    # 1 critere manquant sur 5 pesant 20 (part disponible 75 sur 95)
    score, parts = scoring.confidence_score(4, 52, 3, True, 4, "simule", 75 / 95)
    assert score == pytest.approx(89, abs=1)


def test_confiance_cas_pauvre_environ_49():
    # 2 types, 20 semaines, 10 jours, marche non couvert, 0 cas, client simule, 40 % du poids manquant
    score, parts = scoring.confidence_score(2, 20, 10, False, 0, "simule", 0.6)
    assert score == pytest.approx(49, abs=1)


def test_fraicheur_decroissance_lineaire():
    assert scoring.freshness(7) == 1.0
    assert scoring.freshness(60) == 0.0
    assert scoring.freshness(33.5) == pytest.approx(0.5)


def test_poids_confiance_font_100():
    assert sum(config.CONFIDENCE_WEIGHTS.values()) == 100


# ---------------------------------------------------------------------------
# Criteres unitaires
# ---------------------------------------------------------------------------


def test_persistance_compte_les_semaines_consecutives_finales():
    values = [50.0] * 10 + [5.0] + [40.0] * 13
    value, detail = scoring.persistence(values, threshold=25, target_weeks=26)
    assert detail["streak_weeks"] == 13
    assert value == pytest.approx(0.5)


def test_regularite_indisponible_sous_12_semaines():
    assert scoring.regularity([10.0] * 11)[0] is None


def test_regularite_pente_positive_meilleure_que_negative():
    up = scoring.regularity([10 + i for i in range(30)])[0]
    down = scoring.regularity([40 - i for i in range(30)])[0]
    assert up > 0.5 > down


def test_diversite_hors_liste_ignoree():
    value, detail = scoring.diversity(["social", "reference_persistance"], ["social", "reference_persistance"])
    assert value == pytest.approx(0.25)
    assert detail["active_types"] == ["social"]


def test_diversite_indisponible_sans_source_comptee():
    assert scoring.diversity([], ["reference_persistance"])[0] is None


def test_diffusion_exclut_zz():
    value, detail = scoring.diffusion(["FR", "US", "ZZ"], None)
    assert detail["markets"] == ["FR", "US"]
    assert value == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# RG10 fenetre de tir toujours en intervalle
# ---------------------------------------------------------------------------



def _case(cid, delay, verified=True, url="https://exemple.org/source", sector="food_beverage", target="US"):
    return {"case_id": cid, "sector": sector, "target_market": target, "verified": verified,
            "source_url": url, "observed_delay_months": delay}


def test_fenetre_toujours_un_intervalle_jamais_une_date():
    for n in range(3, 9):
        cases = [_case("C" + str(i), 10 + 4 * i) for i in range(n)]
        w = scoring.analog_window(cases, "food_beverage")
        assert w["low"] is not None and w["high"] is not None
        assert w["low"] < w["high"]
        assert w["count"] == n
        assert set(w) >= {"low", "high", "count", "case_ids", "confidence_level"}


def test_fenetre_sous_trois_cas_aucun_intervalle_et_message():
    w = scoring.analog_window([_case("A", 12), _case("B", 24)], "food_beverage")
    assert w["low"] is None and w["high"] is None
    assert w["confidence_level"] == "faible"
    assert w["message"] == config.INSUFFICIENT_ANALOGS_MESSAGE


def test_fenetre_ignore_cas_non_verifies_ou_sans_url():
    cases = [_case("A", 12), _case("B", 24), _case("C", 30, verified=False), _case("D", 36, url=None),
             _case("E", 40, sector="beauty")]
    w = scoring.analog_window(cases, "food_beverage")
    assert w["count"] == 2
    assert w["case_ids"] == ["A", "B"]


def test_niveaux_de_confiance_fenetre():
    assert scoring.analog_window([_case(str(i), 10 + i) for i in range(3)], "food_beverage")["confidence_level"] == "moyen"
    assert scoring.analog_window([_case(str(i), 10 + i) for i in range(6)], "food_beverage")["confidence_level"] == "élevé"


def test_fenetre_prefere_le_marche_cible_si_trois_cas():
    cases = [_case("US1", 10), _case("US2", 12), _case("US3", 14), _case("FR1", 40, target="FR")]
    assert scoring.analog_window(cases, "food_beverage", "US")["case_ids"] == ["US1", "US2", "US3"]
    assert scoring.analog_window(cases, "food_beverage", "FR")["count"] == 4


def test_seed_aucun_cas_verifie_sans_preuve():
    from run import load_seed_files
    _, _, cases, _ = load_seed_files()
    for c in cases:
        if c.verified:
            assert c.source_url and c.first_signal_date and c.mainstream_date
            assert c.observed_delay_months is not None


# ---------------------------------------------------------------------------
# RG09 Market Timing
# ---------------------------------------------------------------------------


def test_timing_renormalise_sans_cycle_client():
    score, crit, missing = scoring.timing_score("emergent", 50.0, 0, True, None)
    assert missing == ["cycle_client"]
    assert score == pytest.approx(100)


def test_timing_concurrence_indisponible_hors_calcul():
    _, crit, missing = scoring.timing_score("croissance", 10.0, None, False, 1.0)
    assert "concurrence" in missing
    assert crit["concurrence"]["value"] is None


def test_stades_de_maturite():
    values = [1.0] * 30
    assert scoring.maturity_stage(values[:5], 40, 0) == "signal_faible"
    assert scoring.maturity_stage(values, 40, 0) == "emergent"
    assert scoring.maturity_stage(values, 40, 50) == "croissance"
    assert scoring.maturity_stage(values, 0, 0) == "maturite"
    assert scoring.maturity_stage(values, -30, 0) == "declin"


def test_cycle_client_compare_a_la_fenetre():
    window = {"low": 12, "high": 24}
    assert scoring.client_cycle_fit(10, window) == 1.0
    assert scoring.client_cycle_fit(18, window) == 0.5
    assert scoring.client_cycle_fit(30, window) == 0.0
    assert scoring.client_cycle_fit(10, {"low": None, "high": None}) is None


# ---------------------------------------------------------------------------
# RG07 Client Fit
# ---------------------------------------------------------------------------

TODAY = date(2026, 11, 12)
TREND = {"id": "t", "label": "Yuzu", "category": "agrumes",
         "profile": {"price_tier": "premium", "target_segments": ["foodies"], "target_age_range": [25, 45],
                     "claim_types": ["naturel"]}}
CLIENT = {"price_tier": "premium", "brand_values": ["naturel"], "brand_positioning": "naturel",
          "target_segments": ["foodies"], "target_age_range": [25, 45], "product_categories": ["agrumes"],
          "recent_launches": [{"label": "x", "ingredients": ["peche"]}], "active_markets": ["FR"],
          "account_size_tier": "key", "strategic_flag": True, "innovation_cycle_months": 12,
          "next_launch_window": {"start": "2027-03", "end": "2027-06"}, "relationship_status": "active",
          "client_data_source": "simule"}


def test_fit_parfait_priority():
    res = scoring.client_fit(TREND, CLIENT, "FR", {"low": 12, "high": 24}, TODAY)
    assert res["score"] == pytest.approx(100)
    assert res["level"] == "Priority"
    assert res["missing"] == []


def test_fit_poids_rg07():
    assert config.FIT_WEIGHTS == {"alignement_marque": 25, "cible": 20, "portefeuille": 20,
                                  "marche": 15, "potentiel": 10, "capacite_agir": 10}


def test_fit_marche_inactif_compte_zero():
    res = scoring.client_fit(TREND, CLIENT, "US", {"low": 12, "high": 24}, TODAY)
    assert res["criteria"]["marche"]["value"] == 0.0
    assert res["score"] == pytest.approx(85)


def test_fit_profil_absent_critere_exclu():
    trend = {**TREND, "profile": {}}
    res = scoring.client_fit(trend, CLIENT, "FR", None, TODAY)
    assert "cible" in res["missing"]
    assert res["criteria"]["alignement_marque"]["value"] is None


def test_fit_ingredient_deja_lance_reduit_portefeuille():
    client = {**CLIENT, "recent_launches": [{"label": "x", "ingredients": ["yuzu"]}]}
    res = scoring.client_fit(TREND, client, "FR", None, TODAY)
    assert res["criteria"]["portefeuille"]["value"] == pytest.approx(0.7)



# ===========================================================================
# Section : ex test_backtest.py
# Tests du backtest, sans reseau (Google Trends remplace par une fixture qui
# renvoie volontairement des points posterieurs a as_of_date).
# ===========================================================================

@pytest.fixture
def fake_gt(monkeypatch):
    monkeypatch.setattr(collect.GoogleTrendsCollector, "_fetch", conftest.fake_google_fetch)
    monkeypatch.setattr(collect, "_sleep", lambda s: None)


def test_csv_prerempli_sans_invention():
    cases, skipped = runner.load_cases()
    # Seul Dubai Chocolate a une date citee par l'equipe
    assert [c["term"] for c in cases] == ["dubai chocolate"]
    assert cases[0]["as_of"] == date(2023, 12, 31)
    assert cases[0]["label"] == "buzz"
    # 7 cas sans date, k-beauty sans label ni date (phase C), ligne TODO
    assert len(skipped) == 9


def test_aucune_fuite_temporelle(fake_gt):
    as_of = date(2025, 6, 30)
    res = runner.score_case({"term": "yuzu", "sector": "food_beverage", "label": "durable", "as_of": as_of})
    assert res["last_signal_week"] <= as_of


def test_troncature_payload():
    payload = {"series": {"x": [{"date": "2025-06-29"}, {"date": "2025-07-06"}]}}
    out = runner.truncate_payload(payload, date(2025, 6, 30))
    assert [p["date"] for p in out["series"]["x"]] == ["2025-06-29"]


def test_calibration_et_evaluation_disjointes(fake_gt):
    cases = [{"term": t, "sector": "food_beverage", "label": lab, "as_of": date(2025, 6, 30)}
             for t, lab in [("yuzu", "durable"), ("matcha", "durable"), ("hojicha", "durable"),
                            ("dubai chocolate", "buzz"), ("snail mucin", "buzz"), ("rice toner", "buzz")]]
    report = runner.run_backtest(cases)
    assert report["status"] == "ok"
    assert not set(report["calibration_terms"]) & set(report["evaluation_terms"])
    total = sum(sum(row.values()) for row in report["confusion_matrix"].values())
    assert total == len(report["evaluation_terms"])
    assert "precision commerciale" in report["warning"]


def test_moins_de_quatre_cas_insuffisant(fake_gt):
    report = runner.run_backtest([{"term": "yuzu", "sector": "food_beverage", "label": "durable",
                                     "as_of": date(2025, 6, 30)}])
    assert report["status"] == "insuffisant"
    assert report["confusion_matrix"] is None


def test_autocorrelation_saisonniere():
    import math
    seasonal = [50 + 40 * math.sin(2 * math.pi * i / 52) for i in range(156)]
    assert runner.autocorrelation(seasonal, 52) > runner.SEASONAL_ACF_MIN
    assert runner.predict({"acf52": 0.9, "durability": 80}, 60) == "seasonal"


# ===========================================================================
# Section : algorithmes v2 (phase C, RG23a, RG23b a RG29)
# ===========================================================================

import math as _math  # noqa: E402

import numpy as _np  # noqa: E402

import analytics  # noqa: E402

_WEEKS = [date(2023, 1, 2) + timedelta(weeks=i) for i in range(156)]


def _others(div=0.5, dif=0.5, early=0.5):
    return {"diversite": (div, {}, "test"), "diffusion": (dif, {}, "test"), "adoption_precoce": (early, {}, "test")}


def _noise(n, scale, seed=7):
    return _np.random.default_rng(seed).normal(0, scale, n)


def test_v2_serie_saisonniere_non_durable():
    values = [50 + 40 * _math.sin(2 * _math.pi * i / 52) for i in range(156)] + _noise(156, 2)
    res = scoring.durability_v2(list(values), _WEEKS, _others(), n_boot=20)
    assert "seasonality_unknown" not in res["flags"]
    assert res["indicators"]["season"]["method"] == "STL"
    assert res["score"] < 75
    assert res["classification"] not in ("Validated Trend", "Accelerating Trend")


def test_rg23b_tendance_lineaire_sans_saison_ne_declenche_pas_stl():
    values = list(_np.linspace(10, 90, 156) + _noise(156, 1.0))
    assert analytics.seasonal_acf(values) < config.SEASONAL_ACF_MIN
    _adj, resid, info = analytics.deseasonalize(values)
    assert info["method"] != "STL" and resid is None and not info["seasonality_unknown"]


def test_v2_pic_viral_retombee_buzz_avec_penalite():
    values = list(10 + _noise(104, 1.0))
    for k, v in enumerate([100, 70, 40, 25, 18, 14]):
        values[70 + k] = v
    res = scoring.durability_v2(values, _WEEKS[:104], _others(div=0.25, dif=0.2, early=0.0), n_boot=20)
    peak = res["indicators"]["peak"]
    assert peak["isolated"] and peak["retention"] < config.RETENTION_LOW
    assert res["indicators"]["penalty"] == config.EVENT_PENALTY["with_known_event"]
    assert res["classification"] == "Noise/Buzz"


def test_v2_penalite_lineaire_entre_retention_mid_et_low():
    mid = (config.RETENTION_MID + config.RETENTION_LOW) / 2
    peak = {"isolated": True, "retention": mid, "peak_week": "2025-01-06"}
    pts, _ = analytics.event_penalty_v2(peak, [])
    assert pts == pytest.approx(10.0)
    assert analytics.event_penalty_v2({**peak, "retention": 0.7}, [])[0] == 0
    # Evenement connu pres du pic : penalite maximale
    ev = [{"event_date": date(2025, 1, 10), "event_label": "video virale"}]
    assert analytics.event_penalty_v2(peak, ev)[0] == 15


def test_v2_croissance_reguliere_bruitee_durable():
    values = list(_np.linspace(20, 80, 104) + _noise(104, 2.0))
    res = scoring.durability_v2(values, _WEEKS[:104], _others(div=1.0, dif=0.8, early=1.0), n_boot=20)
    assert res["indicators"]["penalty"] == 0
    assert res["classification"] in ("Validated Trend", "Accelerating Trend")
    assert res["criteria"]["regularite"]["detail"]["kendall_tau"] > 0.8


def test_v2_serie_courte_flags_et_confiance_reduite():
    values = list(_np.linspace(10, 30, 30))
    res = scoring.durability_v2(values, _WEEKS[:30], _others(), n_boot=10)
    assert "seasonality_unknown" in res["flags"] and "short_history" in res["flags"]
    args = (4, 30, 3, True, 2, "simule", 1.0, res["score_low"], res["score_high"])
    with_flags, _ = scoring.confidence_v2(*args, res["flags"])
    without, _ = scoring.confidence_v2(*args, [])
    assert with_flags == pytest.approx(without - 10)


def test_v2_pic_recent_retention_inconnue():
    values = [10.0] * 48 + [100.0, 60.0, 40.0, 30.0]
    peak = analytics.peak_retention(values, _WEEKS[:52])
    assert peak["peak_recent"] and peak["retention"] is None
    assert analytics.event_penalty_v2(peak, [])[0] == 0


def test_v2_semaine_calendaire_pas_un_pic_organique():
    values = [10.0 + (i % 3) for i in range(52)]
    values[20] = 100.0
    assert analytics.peak_retention(values, _WEEKS[:52])["isolated"]
    excluded = analytics.peak_retention(values, _WEEKS[:52], calendar_weeks={_WEEKS[20]})
    assert excluded["peak_week"] != _WEEKS[20].isoformat() and not excluded["isolated"]


def test_bootstrap_reproductible_et_intervalle_contient_le_centre():
    values = list(_np.linspace(20, 60, 104) + _noise(104, 5.0))
    a = scoring.durability_v2(values, _WEEKS[:104], _others(), n_boot=50, seed=3)
    b = scoring.durability_v2(values, _WEEKS[:104], _others(), n_boot=50, seed=3)
    assert (a["score_low"], a["score_high"]) == (b["score_low"], b["score_high"])
    assert a["score_low"] <= a["score"] <= a["score_high"]
    assert a["class_uncertain"] == analytics.class_uncertain(a["score_low"], a["score_high"])


def test_class_uncertain_si_seuil_chevauche():
    assert analytics.class_uncertain(72, 78)
    assert not analytics.class_uncertain(76, 88)


def test_dtw_distance_nulle_sur_series_identiques():
    s = [float(v) for v in _np.sin(_np.linspace(0, 6, 40))]
    assert analytics.dtw_distance(s, s) == 0
    assert analytics.dtw_distance(s, [v + 1 for v in s]) > 0


def test_dtw_analogues_non_disponibles_sous_trois_cas():
    res = analytics.find_analogs([1.0] * 52, [], n_verified_cases=2)
    assert res["status"] == "non disponible" and res["analog_delay_low"] is None


def test_dtw_trois_analogues_avec_phase_et_delai():
    base = list(_np.concatenate([_np.full(40, 5.0), _np.linspace(5, 80, 120), _np.full(40, 80.0)]))
    weeks = [date(2015, 1, 5) + timedelta(weeks=i) for i in range(200)]
    cases = [{"case_id": "C" + str(k), "label": "cas " + str(k), "weeks": weeks,
              "values": [v + k for v in base], "first_signal_date": weeks[40], "mainstream_date": weeks[160]}
             for k in range(4)]
    query = base[60:112]
    res = analytics.find_analogs(query, cases, n_verified_cases=4)
    assert res["status"] == "ok" and len(res["analogs"]) == 3
    assert res["analogs"][0]["phase"] == "entre premier signal et mainstream"
    assert res["analog_delay_low"] <= res["analog_delay_high"]


def test_propagation_ordre_correct_sans_causalite():
    weeks = _WEEKS[:60]

    def rising(start):
        return {w: (2.0 + (i % 2)) if i < start else 20.0 + i for i, w in enumerate(weeks)}

    seq = analytics.propagation_sequence({"presse_professionnelle": rising(30), "social": rising(10),
                                          "lancement_produit": rising(45)})
    assert [o["source_type"] for o in seq["order"]] == ["social", "presse_professionnelle", "lancement_produit"]
    assert seq["label"] == "séquence observée" and "causalité" in seq["note"]
    assert all(lag["lag_weeks"] is None or 0 <= lag["lag_weeks"] <= 12 for lag in seq["lagged_correlation"])


def test_cusum_detecte_le_debut():
    values = [10.0 + (i % 2) for i in range(30)] + [10.0 + 3 * k for k in range(1, 23)]
    out = analytics.cusum_start(values, _WEEKS[:52])
    assert out["start_week"] is not None and out["start_week"] >= _WEEKS[28].isoformat()


def test_backtest_compare_v1_v2(fake_gt):
    report = runner.compare_versions()
    by_term = {r["term"]: r for r in report["cases"]}
    assert set(by_term) == {"yuzu", "dubai chocolate", "k-beauty"}
    assert by_term["yuzu"]["market"] == "FR" and "manquante" in by_term["yuzu"]["status"]
    assert "manquante" in by_term["k-beauty"]["status"]
    dubai = by_term["dubai chocolate"]
    assert dubai["status"] == "calcule" and dubai["v1"]["score"] is not None and dubai["v2"]["score"] is not None
    assert "precision" in report["warning"] and "%" not in str(report)


# ===========================================================================
# Section : projection statistique optionnelle (phase F, RG19)
# ===========================================================================


def test_projection_intervalle_et_libelle():
    values = list(_np.linspace(20, 60, 130) + 5 * _np.sin(_np.arange(130) / 3) + _noise(130, 2.0))
    weeks = [date(2023, 1, 2) + timedelta(weeks=i) for i in range(130)]
    res = analytics.projection(values, weeks, horizon=8)
    assert res["label"] == "projection statistique"
    assert len(res["points"]) == 8 and all(p["low"] <= p["projection"] <= p["high"] for p in res["points"])
    assert res["status"] in ("ok", "non fiable") and res["backtest"]["origins"] >= 1
    assert "date unique" in res["note"]


def test_projection_non_fiable_si_baseline_meilleure():
    values = [50 + 40 * _math.sin(2 * _math.pi * i / 52) for i in range(156)]
    weeks = [date(2023, 1, 2) + timedelta(weeks=i) for i in range(156)]
    res = analytics.projection(values, weeks, horizon=8)
    assert res["backtest"]["baseline_mae"] == pytest.approx(0, abs=1e-9)
    assert res["status"] == "non fiable"


def test_challenger_petit_echantillon_et_a_examiner():
    feats = {f: 0.5 for f in analytics.CHALLENGER_FEATURES}
    few = [{"label": "durable", "features": feats, "as_of": "2024-01-01"}] * 3
    assert analytics.challenger(few, feats, "durable")["status"] == "insuffisant"
    rows = []
    for k in range(12):
        good = k % 2 == 0
        f = {f: (0.9 if good else 0.1) for f in analytics.CHALLENGER_FEATURES}
        f["penalite"] = 0 if good else 15
        rows.append({"label": "durable" if good else "buzz", "features": f, "as_of": "2024-%02d-01" % (k + 1)})
    cur = {f: 0.9 for f in analytics.CHALLENGER_FEATURES}
    cur["penalite"] = 0
    res = analytics.challenger(rows, cur, "buzz")
    assert res["status"] == "ok" and res["n_train"] == 8 and "petit echantillon" in res["warning"]
    assert res["prediction"] == "durable" and res["a_examiner"] is True
    assert "accuracy" not in str(res) and "%" not in str(res)
