"""
Pipeline TrendPulse.

Etapes isolees aux noms stables, futures tasks Airflow (task_id identiques) :
  collect, normalize, score_durability, score_timing, match_clients, alert, narrate
Chaque etape lit et ecrit en base (session, run). Rien ne passe par la memoire
d'une etape a l'autre, donc chaque etape peut etre rejouee seule.
Historique append-only (RG14) : uniquement des INSERT sur les tables d'historique.

Lancement direct : python pipeline.py [--sources reddit,google_trends] [--skip-collect]
"""

import argparse
import json
import logging
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

import agent
import analytics
import collect
import config
import scoring
from collect import last_closed_week
from models import (
    AlertStatus, ConfidenceLevel, MaturityStage, RunStatus, SourceStatus, SourceType,
    Alert, CalendarEvent, CaseSeries, Client, ClientMatch, ComparableCase, KnownEvent, Notification, Recommendation,
    RecommendationRecord, TrendCandidate, Narrative, Run, SourceObservation,
    TimingEstimate, Trend, TrendScore, WeeklySignal,
)

logger = logging.getLogger("trendpulse.pipeline")

GEO_SOURCES = set(config.GEO_SOURCES)
CATEGORY_SOURCES = {"open_food_facts", "open_beauty_facts"}


def utcnow():
    return datetime.now(timezone.utc)


def _trend_dict(t):
    return {"id": t.id, "label": t.label, "sector": t.sector.value, "category": t.category,
            "query_terms": t.query_terms or {}, "profile": t.profile or {}}


def _client_dict(c):
    return {"id": c.id, "label": c.label, "sector": c.sector.value, "brand_positioning": c.brand_positioning,
            "price_tier": c.price_tier, "brand_values": c.brand_values, "target_segments": c.target_segments,
            "target_age_range": c.target_age_range, "product_categories": c.product_categories,
            "recent_launches": c.recent_launches, "active_markets": c.active_markets,
            "account_size_tier": c.account_size_tier, "strategic_flag": c.strategic_flag,
            "innovation_cycle_months": c.innovation_cycle_months, "next_launch_window": c.next_launch_window,
            "relationship_status": c.relationship_status, "client_data_source": c.client_data_source.value}


def _case_dict(c):
    return {"case_id": c.case_id, "sector": c.sector.value, "target_market": c.target_market,
            "verified": c.verified, "source_url": c.source_url, "observed_delay_months": c.observed_delay_months}


def _signal_dict(s):
    return {"id": s.id, "observation_id": s.observation_id, "market": s.market, "week": s.week,
            "source_type": s.source_type.value, "source_name": s.source_name,
            "volume": s.signal_volume, "data": s.sector_data or {},
            "granularity": s.granularity or "hebdomadaire", "tone_mean": s.tone_mean}


def _run_today(run):
    value = (run.params or {}).get("as_of_date")
    return date.fromisoformat(value) if value else utcnow().date()


def _selected_trends(session, run):
    ids = (run.params or {}).get("trend_ids")
    q = select(Trend).order_by(Trend.id)
    if ids:
        q = q.where(Trend.id.in_(ids))
    return list(session.scalars(q))


def _load_signals(session, trend_id, as_of):
    start = as_of - timedelta(weeks=config.DURABILITY["window_weeks"] - 1)
    rows = session.scalars(select(WeeklySignal).where(
        WeeklySignal.trend_id == trend_id, WeeklySignal.week >= start, WeeklySignal.week <= as_of))
    return [_signal_dict(s) for s in rows], start


def _load_history(session, trend_id, as_of):
    """Historique complet jusqu'a as_of (la v2 desaisonnalise sur 104 semaines et plus)."""
    rows = session.scalars(select(WeeklySignal).where(WeeklySignal.trend_id == trend_id, WeeklySignal.week <= as_of))
    return [_signal_dict(s) for s in rows]


def _calendar_weeks(session, market):
    """RG23a : semaines couvertes par calendar_events pour un marche (ZZ pour la portee globale)."""
    weeks = set()
    for ev in session.scalars(select(CalendarEvent).where(CalendarEvent.market == (market or config.GLOBAL_MARKET))):
        d = ev.date_start
        while d <= ev.date_end:
            weeks.add(collect.week_start(d))
            d += timedelta(days=1)
    return weeks


def parse_partial_date(value):
    """Date des cas comparables : AAAA-MM-JJ, AAAA-MM (premier du mois) ou None. L'annee seule est trop imprecise."""
    value = (value or "").strip()
    parts = value.split("-")
    try:
        if len(parts) == 3:
            return date.fromisoformat(value)
        if len(parts) == 2:
            return date(int(parts[0]), int(parts[1]), 1)
    except ValueError:
        return None
    return None


def fill_case_series(session, today, collector):
    """
    RG26 : historique Google Trends des cas verifies, collecte une seule fois
    par cas (case_series append-only). Fenetre de 26 semaines avant le premier
    signal, 5 ans au plus (granularite hebdomadaire de Google Trends).
    """
    done = {"collected": [], "skipped": [], "errors": []}
    for case in session.scalars(select(ComparableCase).where(ComparableCase.verified.is_(True))):
        if session.scalar(select(func.count(CaseSeries.id)).where(CaseSeries.case_id == case.case_id)):
            done["skipped"].append(case.case_id)
            continue
        first = parse_partial_date(case.first_signal_date)
        if first is None or parse_partial_date(case.mainstream_date) is None:
            done["skipped"].append(case.case_id)
            continue
        since = first - timedelta(weeks=26)
        until = min(since + timedelta(weeks=260), today)
        geo = case.target_market if case.target_market and case.target_market != config.GLOBAL_MARKET else None
        trend = {"id": "case_" + case.case_id, "sector": case.sector.value,
                 "query_terms": {"google_trends": [case.case_label.lower()]}}
        res = collector.safe_collect(trend, geo, since, until)
        if res["status"] != "success":
            done["errors"].append(case.case_id + " : " + str(res.get("error")))
            continue
        rows = collect._norm_google(res["payload"], {"market": geo or config.GLOBAL_MARKET}, until + timedelta(days=1))
        for r in rows:
            session.add(CaseSeries(case_id=case.case_id, market=r["market"], week=r["week"], value=r["volume"]))
        done["collected"].append(case.case_id)
    session.commit()
    return done


def _case_series(session, sector):
    """Series des cas verifies du secteur, pour les analogues DTW."""
    out = []
    for case in session.scalars(select(ComparableCase).where(ComparableCase.verified.is_(True),
                                                             ComparableCase.sector == sector)):
        rows = list(session.scalars(select(CaseSeries).where(CaseSeries.case_id == case.case_id).order_by(CaseSeries.week)))
        if rows:
            out.append({"case_id": case.case_id, "label": case.case_label, "weeks": [r.week for r in rows],
                        "values": [r.value for r in rows], "first_signal_date": parse_partial_date(case.first_signal_date),
                        "mainstream_date": parse_partial_date(case.mainstream_date)})
    return out


def _events(session, trend_id):
    return [{"market": e.market, "event_date": e.event_date, "event_label": e.event_label}
            for e in session.scalars(select(KnownEvent).where(KnownEvent.trend_id == trend_id))]


def _cases(session):
    return [_case_dict(c) for c in session.scalars(select(ComparableCase))]


def tracked_markets(signals):
    """Marches suivis (config.MARKETS) presents dans les signaux. La diffusion globale compte tous les pays."""
    return sorted({s["market"] for s in signals if s["market"] in config.MARKETS})


def _weeks(start, end):
    out = []
    w = start
    while w <= end:
        out.append(w)
        w += timedelta(days=7)
    return out


# ---------------------------------------------------------------------------
# 1. collect
# ---------------------------------------------------------------------------


def step_collect(session, run, today):
    params = run.params or {}
    since = today - timedelta(weeks=config.COLLECT_LOOKBACK_WEEKS)
    trends = _selected_trends(session, run)
    status = dict(run.source_status or {})
    for name in collect.disabled_source_names(params.get("sources")):
        status[name] = {"state": SourceStatus.disabled.value}
    for collector in collect.enabled_collectors(params.get("sources")):
        summary = {"state": "done", "success": 0, "error": 0, "not_applicable": 0, "requests": 0, "errors": []}
        for trend in trends:
            td = _trend_dict(trend)
            if td["sector"] not in collector.sectors:
                continue
            markets = config.MARKETS if collector.geo_capable else [None]
            for market in markets:
                res = collector.safe_collect(td, market, since, today)
                session.add(SourceObservation(
                    run_id=run.id, trend_id=td["id"], source_type=SourceType(res["source_type"]), source_name=res["source_name"],
                    source_url=res.get("source_url"), market=res["market"],
                    collected_at=datetime.fromisoformat(res["collected_at"]), status=SourceStatus(res["status"]),
                    error=res.get("error"), content_hash=collect.content_hash(res["payload"]),
                    raw_payload=res["payload"],
                ))
                summary[res["status"]] = summary.get(res["status"], 0) + 1
                summary["requests"] += res.get("request_count", 0)
                if res.get("error") and len(summary["errors"]) < 5:
                    summary["errors"].append(td["id"] + "/" + res["market"] + " : " + res["error"])
        if summary["success"] == 0 and summary["error"] > 0:
            summary["state"] = "failed"
        status[collector.name] = summary
        logger.info("collect | %s | %s", collector.name, {k: v for k, v in summary.items() if k != "errors"})
        session.commit()
        if collector.name == "google_trends":
            status["_case_series"] = fill_case_series(session, today, collector)
    run.source_status = status
    session.commit()


# ---------------------------------------------------------------------------
# 2. normalize
# ---------------------------------------------------------------------------


def step_normalize(session, run, today):
    obs_rows = session.scalars(select(SourceObservation).where(
        SourceObservation.run_id == run.id, SourceObservation.status == SourceStatus.success))
    trends = {t.id: _trend_dict(t) for t in session.scalars(select(Trend))}
    inserted = skipped = 0
    for obs in obs_rows:
        trend = trends[obs.trend_id]
        payload = obs.raw_payload
        if obs.source_name == "rss_trade_press":
            # Les flux ne gardent que les derniers articles : fusion avec les collectes precedentes
            history = session.scalars(select(SourceObservation.raw_payload).where(
                SourceObservation.trend_id == obs.trend_id, SourceObservation.source_name == "rss_trade_press",
                SourceObservation.status == SourceStatus.success, SourceObservation.id <= obs.id))
            payload = collect.merge_rss_payloads(list(history))
        obs_dict = {"id": obs.id, "source_name": obs.source_name, "source_url": obs.source_url,
                    "market": obs.market, "status": obs.status.value, "raw_payload": payload}
        signals = collect.normalize_observation(obs_dict, trend, _events(session, obs.trend_id), today)
        if not signals:
            continue
        names = {sig.source_name for sig in signals}
        existing = set(session.execute(select(WeeklySignal.source_name, WeeklySignal.market, WeeklySignal.week).where(
            WeeklySignal.trend_id == obs.trend_id, WeeklySignal.source_name.in_(names))).all())
        for sig in signals:
            key = (sig.source_name, sig.market, sig.week)
            if key in existing:
                # Semaine deja ecrite : jamais de mise a jour (RG14)
                skipped += 1
                continue
            existing.add(key)
            session.add(WeeklySignal(**sig.model_dump()))
            inserted += 1
        session.commit()
    status = dict(run.source_status or {})
    status["_normalize"] = {"inserted": inserted, "skipped_existing_weeks": skipped}
    run.source_status = status
    session.commit()


# ---------------------------------------------------------------------------
# 3. score_durability
# ---------------------------------------------------------------------------


def durability_for_scope(signals, events, sector_case_count, market, start, as_of, today):
    """
    Calcul pur du Trend Durability Score et de la confiance pour une portee
    (market=None : global, sinon un code ISO). Retourne un dict pret a stocker.
    """
    scoped = signals if market is None else [s for s in signals if s["market"] == market]
    weeks = _weeks(start, as_of)

    # Persistance et regularite : series continues, sinon toutes les sources
    def series_of(rows):
        by_key = defaultdict(dict)
        for s in rows:
            by_key[(s["source_name"], s["market"])][s["week"]] = s["volume"]
        return list(by_key.values())

    persist_rows = [s for s in scoped if s["source_name"] in config.PERSISTENCE_SOURCES]
    series_source = "persistance"
    series_list = series_of(persist_rows)
    if not series_list:
        series_list = series_of(scoped)
        series_source = "toutes_sources"
    present_weeks = [w for w in weeks if any(w in s for s in series_list)]
    values = scoring.activity_series(series_list, present_weeks)

    pers = scoring.persistence(values) if values else (None, {})
    reg = scoring.regularity(values) if values else (None, {})

    active_types = {s["source_type"] for s in scoped if s["volume"] > 0}
    responded_types = {s["source_type"] for s in scoped}
    div = scoring.diversity(active_types, responded_types)

    geo_rows = [s for s in scoped if s["source_name"] in GEO_SOURCES or s["source_type"] == "retail_ecommerce"]
    cat_rows = [s for s in scoped if s["source_name"] in CATEGORY_SOURCES]
    markets = {s["market"] for s in geo_rows if s["volume"] > 0} if market is None else None
    categories = None
    if cat_rows:
        categories = sorted({c for s in cat_rows if s["volume"] > 0 for c in (s["data"].get("categories") or [])})
    # Diffusion par marche calculable seulement si une source geolocalisee a repondu pour un vrai pays (RG05)
    geo_known = any(s["market"] != config.GLOBAL_MARKET for s in geo_rows)
    dif = scoring.diffusion(markets, categories, markets_available=(market is None and geo_known))

    early_types = set(config.DURABILITY["early_adoption_types"])
    early_rows = [s for s in scoped if s["source_type"] in early_types or s["source_type"] == "lancement_produit"]
    has_early = any(s["volume"] > 0 and (s["source_type"] in early_types or
                    s["data"].get("launch_brand_tier") in config.DURABILITY["reference_brand_tiers"])
                    for s in early_rows)
    early = scoring.early_adoption(has_early, bool(early_rows))

    crit = scoring.build_durability_criteria({
        "persistance": (pers[0], pers[1], series_source),
        "regularite": (reg[0], reg[1], series_source),
        "diversite": (div[0], div[1], "source_types"),
        "diffusion": (dif[0], dif[1], "geo+categories"),
        "adoption_precoce": (early[0], early[1], "recherche/brevet/lancement"),
    })

    scoped_events = [e for e in events if market is None or e.get("market") in (None, "", market)]
    is_spike, idx, spike_detail = scoring.detect_isolated_spike(values)
    peak_week = present_weeks[idx] if idx is not None and idx < len(present_weeks) else None
    penalty, pen_detail = scoring.event_penalty(is_spike, peak_week, scoped_events)
    result = scoring.durability_score(crit, penalty)

    covered = sorted({s["market"] for s in geo_rows if s["market"] != config.GLOBAL_MARKET})
    last_week = max((s["week"] for s in scoped), default=None)
    days_since = (today - (last_week + timedelta(days=6))).days if last_week else None
    n_types = len(set(div[1].get("active_types", [])))
    obs_weeks = len({s["week"] for s in scoped})
    market_covered = bool(covered) if market is None else market in covered
    confidence, conf_parts = scoring.confidence_score(
        n_types, obs_weeks, days_since, market_covered, sector_case_count, "absent", result["available_weight_share"])

    crit["_penalite"] = {"value": penalty, "detail": {**pen_detail, **spike_detail}}
    crit["_confiance"] = {"value": confidence, "detail": conf_parts, "inputs": {
        "n_source_types": n_types, "observation_weeks": obs_weeks, "days_since_last": days_since,
        "covered_markets": covered, "comparable_cases": sector_case_count,
        "criteria_weight_share": result["available_weight_share"], "client_data_source": "absent"}}
    return {
        "durability": result["score"], "classification": result["classification"], "confidence": confidence,
        "criteria": crit, "missing": result["missing"], "penalty": penalty,
        "observation_ids": sorted({s["observation_id"] for s in scoped}),
        "active_types": sorted(div[1].get("active_types", [])), "values": values, "weeks": present_weeks,
    }


def durability_v2_for_scope(history, v1, events, verified_cases, market, today, calendar_weeks, n_boot=None):
    """
    Calcul pur v2 (RG23a, RG23b, RG24, RG25, RG29) pour une portee. Diversite, diffusion et
    adoption precoce sont reprises de la v1 (memes definitions, RG03 et RG04).
    """
    scoped = history if market is None else [s for s in history if s["market"] == market]
    rows = [s for s in scoped if s["source_name"] in config.PERSISTENCE_SOURCES] or \
        [s for s in scoped if s.get("granularity", "hebdomadaire") == "hebdomadaire"]
    by_key = defaultdict(dict)
    for s in rows:
        by_key[(s["source_name"], s["market"])][s["week"]] = s["volume"]
    weeks = sorted({w for series in by_key.values() for w in series})
    values = scoring.activity_series(list(by_key.values()), weeks)
    others = {k: (v1["criteria"][k]["value"], v1["criteria"][k]["detail"], v1["criteria"][k]["source"])
              for k in ("diversite", "diffusion", "adoption_precoce")}
    scoped_events = [e for e in events if market is None or e.get("market") in (None, "", market)]
    res = scoring.durability_v2(values, weeks, others, calendar_weeks, scoped_events, n_boot)
    inputs = v1["criteria"]["_confiance"]["inputs"]
    covered = inputs["covered_markets"]
    market_covered = bool(covered) if market is None else market in covered
    confidence, parts = scoring.confidence_v2(
        inputs["n_source_types"], inputs["observation_weeks"], inputs["days_since_last"], market_covered,
        verified_cases, "absent", res["available_weight_share"], res["score_low"], res["score_high"], res["flags"])
    crit = res["criteria"]
    crit["_confiance"] = {"value": confidence, "detail": parts, "inputs": {
        **inputs, "verified_cases": verified_cases, "score_low": res["score_low"], "score_high": res["score_high"],
        "flags": res["flags"], "criteria_weight_share": res["available_weight_share"]}}
    return {"durability": res["score"], "classification": res["classification"], "confidence": confidence,
            "criteria": crit, "missing": res["missing"], "penalty": crit["_penalite"]["value"],
            "score_low": res["score_low"], "score_high": res["score_high"], "class_uncertain": res["class_uncertain"],
            "flags": res["flags"], "observation_ids": v1["observation_ids"], "values": values, "weeks": weeks}


def step_score_durability(session, run, today):
    as_of = last_closed_week(today)
    cases = _cases(session)
    for trend in _selected_trends(session, run):
        signals, start = _load_signals(session, trend.id, as_of)
        events = _events(session, trend.id)
        n_cases = len(scoring.eligible_cases(cases, trend.sector.value))
        markets = tracked_markets(signals)
        history = _load_history(session, trend.id, as_of)
        for market in [None] + markets:
            res = durability_for_scope(signals, events, n_cases, market, start, as_of, today)
            session.add(TrendScore(
                run_id=run.id, trend_id=trend.id, market=market, as_of_week=as_of,
                durability=res["durability"], confidence=res["confidence"], classification=res["classification"],
                criteria=json.loads(json.dumps(res["criteria"], default=str)), missing_criteria=res["missing"],
                observation_ids=res["observation_ids"], scoring_version=config.SCORING_VERSION,
            ))
            # v2 stockee a cote de la v1 (RG14 append-only, v1 conservee pour comparaison)
            v2 = durability_v2_for_scope(history, res, events, n_cases, market, today, _calendar_weeks(session, market))
            session.add(TrendScore(
                run_id=run.id, trend_id=trend.id, market=market, as_of_week=as_of,
                durability=v2["durability"], confidence=v2["confidence"], classification=v2["classification"],
                criteria=json.loads(json.dumps(v2["criteria"], default=str)), missing_criteria=v2["missing"],
                observation_ids=v2["observation_ids"], scoring_version=config.SCORING_VERSION_V2,
                score_low=v2["score_low"], score_high=v2["score_high"], class_uncertain=v2["class_uncertain"],
                flags=v2["flags"],
            ))
        # Premiere apparition (referentiel, pas d'historique)
        if trend.first_seen_date is None:
            positive = sorted((s for s in signals if s["volume"] > 0 and s["market"] != config.GLOBAL_MARKET
                               and s["source_name"] in GEO_SOURCES), key=lambda s: s["week"])
            if positive:
                trend.first_seen_date = positive[0]["week"]
                trend.first_seen_market = positive[0]["market"]
        session.commit()


# ---------------------------------------------------------------------------
# 4. score_timing
# ---------------------------------------------------------------------------


def timing_for_market(signals, market, sector, cases, client=None, as_of=None, case_series=None):
    """Calcul pur RG09 et RG10 pour une tendance dans un marche, analogues DTW (RG26)."""
    rows = [s for s in signals if s["market"] == market]
    by_key = defaultdict(dict)
    for s in rows:
        by_key[s["source_name"]][s["week"]] = s["volume"]
    weeks = sorted({s["week"] for s in rows})
    values = scoring.activity_series(list(by_key.values()), weeks)
    growth = scoring.local_growth(values)
    launch_rows = [s for s in rows if s["source_type"] == "lancement_produit"]
    competition_available = bool(launch_rows)
    launch_count = int(sum(s["volume"] for s in launch_rows)) if competition_available else None
    first_launch = min((s["week"] for s in launch_rows if s["volume"] > 0), default=None)
    stage = scoring.maturity_stage(values, growth, launch_count)
    window = scoring.analog_window(cases, sector, market)
    verified = len(scoring.eligible_cases(cases, sector))
    analogs = analytics.find_analogs(values, case_series or [], verified)
    if analogs["status"] == "ok":
        # RG26 alimente la fenetre de tir RG10, toujours en intervalle
        window = {**window, "low": analogs["analog_delay_low"], "high": analogs["analog_delay_high"],
                  "case_ids": [a["case_id"] for a in analogs["analogs"]], "count": len(analogs["analogs"]),
                  "message": None, "source": "dtw"}
    cycle_fit = scoring.client_cycle_fit(client.get("innovation_cycle_months"), window) if client else None
    score, crit, missing = scoring.timing_score(stage, growth, launch_count, competition_available, cycle_fit)
    channel = next((s["data"].get("menu_channel") for s in reversed(rows) if s["data"].get("menu_channel")), None)
    return {
        "market": market, "maturity_stage": stage, "local_growth_rate": growth,
        "competitor_launch_count": launch_count, "competitor_first_launch_date": first_launch,
        "client_cycle_fit": cycle_fit, "channel_stage": channel, "window": window,
        "timing_score": score, "criteria": crit, "missing": missing, "analogs": analogs,
    }


def _store_timing(session, run, trend_id, res, client_id=None):
    w = res["window"]
    row = TimingEstimate(
        run_id=run.id, trend_id=trend_id, market=res["market"], client_id=client_id,
        maturity_stage=MaturityStage(res["maturity_stage"]) if res["maturity_stage"] else None, local_growth_rate=res["local_growth_rate"],
        competitor_launch_count=res["competitor_launch_count"],
        competitor_first_launch_date=res["competitor_first_launch_date"],
        client_cycle_fit=res["client_cycle_fit"], channel_stage=res["channel_stage"],
        analog_delay_low=w["low"], analog_delay_high=w["high"], analog_case_count=w["count"],
        analog_case_ids=w["case_ids"], confidence_level=ConfidenceLevel(w["confidence_level"]), timing_score=res["timing_score"],
        criteria=json.loads(json.dumps(res["criteria"], default=str)), window_message=w["message"],
        scoring_version=config.SCORING_VERSION, analogs=json.loads(json.dumps(res.get("analogs"), default=str)),
    )
    session.add(row)
    session.flush()
    return row


def step_score_timing(session, run, today):
    as_of = last_closed_week(today)
    cases = _cases(session)
    for trend in _selected_trends(session, run):
        signals, _ = _load_signals(session, trend.id, as_of)
        markets = tracked_markets(signals)
        series = _case_series(session, trend.sector)
        for market in markets:
            _store_timing(session, run, trend.id, timing_for_market(signals, market, trend.sector.value, cases,
                                                                    case_series=series))
        session.commit()


# ---------------------------------------------------------------------------
# 5. match_clients
# ---------------------------------------------------------------------------


def step_match_clients(session, run, today):
    as_of = last_closed_week(today)
    cases = _cases(session)
    for trend in _selected_trends(session, run):
        td = _trend_dict(trend)
        scores = {s.market: s for s in session.scalars(select(TrendScore).where(
            TrendScore.run_id == run.id, TrendScore.trend_id == trend.id,
            TrendScore.scoring_version == primary_version()))}
        global_score = scores.get(None)
        if global_score is None:
            continue
        signals, _ = _load_signals(session, trend.id, as_of)
        signal_markets = set(tracked_markets(signals))
        inputs = global_score.criteria["_confiance"]["inputs"]
        clients = session.scalars(select(Client).where(Client.sector == trend.sector))
        series = _case_series(session, trend.sector)
        for client in clients:
            cd = _client_dict(client)
            for market in sorted(set(cd["active_markets"]) | signal_markets):
                ts = scores.get(market) or global_score
                timing_row = None
                window = None
                if market in signal_markets:
                    res = timing_for_market(signals, market, td["sector"], cases, client=cd, case_series=series)
                    timing_row = _store_timing(session, run, trend.id, res, client_id=client.id)
                    window = res["window"]
                fit = scoring.client_fit(td, cd, market, window, today)
                timing_value = timing_row.timing_score if timing_row else None
                opp, opp_missing = scoring.opportunity_score(ts.durability, fit["score"], timing_value)
                conf_inputs = ts.criteria["_confiance"]["inputs"] if ts is not global_score else inputs
                confidence = match_confidence(ts, conf_inputs, market in inputs["covered_markets"],
                                              window["count"] if window else conf_inputs["comparable_cases"],
                                              cd["client_data_source"])
                session.add(ClientMatch(
                    run_id=run.id, trend_id=trend.id, client_id=client.id, market=market,
                    fit_score=fit["score"], fit_level=fit["level"],
                    fit_criteria={**fit["criteria"], "_missing": fit["missing"],
                                  "_durability_scope": ts.market or "global"},
                    timing_estimate_id=timing_row.id if timing_row else None, trend_score_id=ts.id,
                    durability_score=ts.durability, timing_score=timing_value, opportunity_score=opp,
                    confidence_score=confidence, level=scoring.opportunity_level(opp),
                    missing_inputs=opp_missing, scoring_version=config.SCORING_VERSION,
                ))
        session.commit()


def primary_version():
    """Version de score utilisee en aval (config.SCORING_PRIMARY, v2 par defaut)."""
    return config.SCORING_PRIMARY if config.SCORING_PRIMARY == config.SCORING_VERSION_V2 else config.SCORING_VERSION


def match_confidence(ts, inputs, market_covered, cases, client_source):
    """Confiance d'un match : RG12 pour un score v1, RG29 pour un score v2."""
    if ts.scoring_version == config.SCORING_VERSION_V2:
        return scoring.confidence_v2(
            inputs["n_source_types"], inputs["observation_weeks"], inputs["days_since_last"], market_covered,
            cases, client_source, inputs["criteria_weight_share"], ts.score_low, ts.score_high, ts.flags or [])[0]
    return scoring.confidence_score(
        inputs["n_source_types"], inputs["observation_weeks"], inputs["days_since_last"], market_covered,
        cases, client_source, inputs["criteria_weight_share"])[0]


# ---------------------------------------------------------------------------
# 6. alert
# ---------------------------------------------------------------------------


def step_alert(session, run, today):
    for m in session.scalars(select(ClientMatch).where(ClientMatch.run_id == run.id)):
        status, snapshot = scoring.alert_decision(m.durability_score, m.fit_score, m.opportunity_score, m.confidence_score)
        if status:
            session.add(Alert(match_id=m.id, status=AlertStatus(status), conditions=json.loads(json.dumps(snapshot, default=str))))
    session.commit()


# ---------------------------------------------------------------------------
# 6 bis. recommend (RG33 a RG35)
# ---------------------------------------------------------------------------


def _scope_signals(signals, market):
    rows = [s for s in signals if s["market"] == market]
    return rows or signals


def _activity(rows):
    persist = [s for s in rows if s["source_name"] in config.PERSISTENCE_SOURCES] or \
        [s for s in rows if s.get("granularity", "hebdomadaire") == "hebdomadaire"]
    by_key = defaultdict(dict)
    for s in persist:
        by_key[(s["source_name"], s["market"])][s["week"]] = s["volume"]
    weeks = sorted({w for series in by_key.values() for w in series})
    return scoring.activity_series(list(by_key.values()), weeks)


def scope_activity(history, market=None):
    """Semaines et activite 0 a 100 d'une portee (sources de persistance, sinon sources hebdomadaires)."""
    rows = _scope_signals(history, market) if market else history
    persist = [s for s in rows if s["source_name"] in config.PERSISTENCE_SOURCES] or \
        [s for s in rows if s.get("granularity", "hebdomadaire") == "hebdomadaire"]
    by_key = defaultdict(dict)
    for s in persist:
        by_key[(s["source_name"], s["market"])][s["week"]] = s["volume"]
    weeks = sorted({w for series in by_key.values() for w in series})
    return weeks, scoring.activity_series(list(by_key.values()), weeks)


def score_features(criteria):
    """Indicateurs RG24 d'un score v2, entree du challenger (phase F)."""
    feats = {k: (criteria.get(k) or {}).get("value") for k in ("persistance", "regularite", "diversite", "diffusion",
                                                               "adoption_precoce")}
    feats["penalite"] = (criteria.get("_penalite") or {}).get("value")
    return feats


def based_on_items(signals, market, as_of):
    """
    RG34 : un element par type de source, tire de weekly_signals, avec son
    evidence_id (sig-<id>). Signal le plus fort des 13 dernieres semaines.
    """
    start = as_of - timedelta(weeks=config.TIMING["growth_weeks"] - 1)
    recent = [s for s in _scope_signals(signals, market) if s["week"] >= start and s["volume"] > 0]
    best = {}
    for s in recent:
        if s["source_type"] not in best or s["volume"] > best[s["source_type"]]["volume"]:
            best[s["source_type"]] = s
    items = []
    for stype, s in sorted(best.items()):
        total = sum(x["volume"] for x in recent if x["source_type"] == stype)
        items.append({"source_type": stype, "evidence_id": "sig-" + str(s["id"]),
                      "summary": (s["source_name"] + ", marche " + s["market"] + " : volume " + str(round(s["volume"], 1))
                                  + " la semaine du " + s["week"].isoformat() + ", total " + str(round(total, 1))
                                  + " sur 13 semaines")})
    return items


def _launch_counts(signals, market, as_of):
    n = config.TIMING["growth_weeks"]
    rows = [s for s in signals if s["market"] == market and s["source_type"] == "lancement_produit"]
    if not rows:
        return None, None
    recent = sum(s["volume"] for s in rows if s["week"] > as_of - timedelta(weeks=n))
    previous = sum(s["volume"] for s in rows if as_of - timedelta(weeks=2 * n) < s["week"] <= as_of - timedelta(weeks=n))
    return recent, previous


def _unavailable_sources(run):
    out = []
    for name, st in (run.source_status or {}).items():
        if name.startswith("_") or not isinstance(st, dict):
            continue
        if st.get("state") == "failed" or st.get("indisponible"):
            out.append(name)
    return out


def _why_for_client(fit_criteria):
    items = []
    for name, c in (fit_criteria or {}).items():
        if name.startswith("_") or not isinstance(c, dict):
            continue
        pts = c["value"] * c["weight"] if c.get("value") is not None else None
        items.append({"criterion": name, "value": c.get("value"), "weight": c["weight"],
                      "points": None if pts is None else round(pts, 1)})
    items.sort(key=lambda i: -(i["points"] if i["points"] is not None else -1))
    return items[:2]


def _previous_recommendations(session, run, client_id):
    prev_run = session.scalar(select(func.max(RecommendationRecord.run_id)).where(
        RecommendationRecord.client_id == client_id, RecommendationRecord.run_id < run.id))
    if prev_run is None:
        return {}
    return {(r.trend_id, r.market): (r.bucket, (r.payload.get("scores") or {}).get("opportunity"))
            for r in session.scalars(select(RecommendationRecord).where(
                RecommendationRecord.client_id == client_id, RecommendationRecord.run_id == prev_run))
            if r.bucket != "sorti"}


def build_recommendation(session, run, match, bucket, missing, as_of, signals, unavailable):
    trend = session.get(Trend, match.trend_id)
    client = session.get(Client, match.client_id)
    ts = session.get(TrendScore, match.trend_score_id) if match.trend_score_id else None
    te = session.get(TimingEstimate, match.timing_estimate_id) if match.timing_estimate_id else None
    flags = list((ts.flags if ts else None) or [])
    inputs = (ts.criteria.get("_confiance", {}).get("inputs", {}) if ts else {})
    peak = (ts.criteria.get("_penalite", {}).get("detail", {}).get("peak", {}) if ts else {})
    penalty = ts.criteria.get("_penalite", {}).get("value") if ts else None
    recent, previous = _launch_counts(signals, match.market, as_of)
    caveats = []
    if client.client_data_source.value == "simule":
        caveats.append("données client simulées, classement à libeller comme démonstration")
    if trend.sector.value == "fragrance" and (inputs.get("n_source_types") or 0) < config.FRAGRANCE_MIN_SOURCE_TYPES:
        caveats.append("couverture Fragrance faible : peu de sources disponibles, confiance laissée basse sans compensation")
    if te is None or te.analog_delay_low is None:
        caveats.append("fenêtre de tir non disponible : " + ((te.window_message if te else None)
                                                            or config.INSUFFICIENT_ANALOGS_MESSAGE))
    reason = None
    if bucket == "ecartees":
        parts = [c["condition"] + " " + ("non calculable" if c["value"] is None else
                                         str(round(c["value"], 1)) + " sous le seuil " + str(c["threshold"]))
                 for c in missing]
        if penalty:
            parts.append("pénalité événementielle de " + str(round(penalty, 1)) + " points")
        reason = ", ".join(parts)
    rec = {
        "trend_id": trend.id, "trend_label": trend.label, "market": match.market, "client_id": client.id,
        "client_data_source": client.client_data_source.value, "bucket": bucket,
        "why_for_client": _why_for_client(match.fit_criteria),
        "based_on": based_on_items(signals, match.market, as_of),
        "analogs": te.analogs if te else None,
        "timing_window": {"low": te.analog_delay_low if te else None, "high": te.analog_delay_high if te else None,
                          "message": te.window_message if te else config.INSUFFICIENT_ANALOGS_MESSAGE},
        "scores": {"durability": match.durability_score, "fit": match.fit_score, "timing": match.timing_score,
                   "opportunity": match.opportunity_score, "confidence": match.confidence_score,
                   "score_low": ts.score_low if ts else None, "score_high": ts.score_high if ts else None},
        "caveats": caveats,
        "counter_evidence": scoring.counter_evidence(peak.get("retention"), inputs.get("n_source_types"),
                                                     recent, previous, flags, unavailable),
        "missing_condition": missing[0]["condition"] if bucket == "a_surveiller" else None,
        "reason": reason,
    }
    text, origin = agent.suggested_action(rec)
    rec["suggested_action"], rec["origin"] = text, origin.value
    return rec


def step_recommend(session, run, today):
    """Paniers a_proposer, a_surveiller, ecartees par client et marche, append-only (RG14)."""
    as_of = last_closed_week(today)
    matches = list(session.scalars(select(ClientMatch).where(ClientMatch.run_id == run.id)))
    signals_by_trend = {}
    rises = {}
    for m in matches:
        if m.trend_id not in signals_by_trend:
            signals_by_trend[m.trend_id] = _load_signals(session, m.trend_id, as_of)[0]
        key = (m.trend_id, m.market)
        if key not in rises:
            rises[key] = scoring.recent_rise(_activity(_scope_signals(signals_by_trend[m.trend_id], m.market)))
    popular = scoring.popular_keys(rises)
    unavailable = _unavailable_sources(run)
    grouped = defaultdict(lambda: defaultdict(list))
    for m in matches:
        conds = scoring.rg13_conditions(m.durability_score, m.fit_score, m.opportunity_score, m.confidence_score)
        bucket, missing = scoring.recommendation_bucket(conds, (m.trend_id, m.market) in popular)
        if bucket:
            grouped[(m.client_id, m.market)][bucket].append((m, missing))
    earlier_clients = set(session.scalars(select(RecommendationRecord.client_id).where(
        RecommendationRecord.run_id < run.id).distinct()))
    previous_by_client = {c: _previous_recommendations(session, run, c)
                          for c in earlier_clients | {c for c, _m in grouped}}
    stored = set()
    for (client_id, market), buckets in sorted(grouped.items()):
        prev = previous_by_client[client_id]
        for bucket, items in buckets.items():
            items.sort(key=lambda it: -(it[0].opportunity_score if it[0].opportunity_score is not None else -1))
            for rank, (m, missing) in enumerate(items[:config.RECO_LIMITS[bucket]], start=1):
                rec = build_recommendation(session, run, m, bucket, missing, as_of, signals_by_trend[m.trend_id],
                                           unavailable)
                rec["rank"] = rank
                rec["change_vs_last_run"] = scoring.change_vs_last(prev.get((m.trend_id, market)),
                                                                   (bucket, m.opportunity_score))
                payload = Recommendation.model_validate(rec).model_dump(mode="json")
                session.add(RecommendationRecord(run_id=run.id, client_id=client_id, trend_id=m.trend_id,
                                                 market=market, bucket=bucket, rank=rank, payload=payload))
                stored.add((client_id, m.trend_id, market))
    # Sortis : presents au run precedent, absents de tout panier maintenant
    for client_id, prev in previous_by_client.items():
        for (trend_id, market), (bucket, _opp) in prev.items():
            if (client_id, trend_id, market) not in stored:
                session.add(RecommendationRecord(run_id=run.id, client_id=client_id, trend_id=trend_id, market=market,
                                                 bucket="sorti", rank=0, payload={
                                                     "trend_id": trend_id, "market": market, "previous_bucket": bucket,
                                                     "change_vs_last_run": "sorti"}))
    session.commit()


# ---------------------------------------------------------------------------
# 7. narrate
# ---------------------------------------------------------------------------


def build_evidence(session, trend_id, as_of):
    """Preuves citables par le LLM : signaux forts et titres Reddit (texte externe)."""
    signals, _ = _load_signals(session, trend_id, as_of)
    evidence = []
    best = {}
    for s in signals:
        key = (s["source_name"], s["market"])
        if s["volume"] > 0 and (key not in best or s["volume"] > best[key]["volume"]):
            best[key] = s
    for (source, market), s in sorted(best.items()):
        evidence.append({"evidence_id": "sig-" + str(s["id"]), "kind": "signal", "external": False,
                         "text": source + " marche " + market + " semaine " + s["week"].isoformat()
                                 + " volume " + str(round(s["volume"], 1))})
    obs = session.scalar(select(SourceObservation).where(
        SourceObservation.trend_id == trend_id, SourceObservation.source_name == "reddit",
        SourceObservation.status == SourceStatus.success).order_by(SourceObservation.id.desc()))
    if obs:
        for post in (obs.raw_payload or {}).get("posts", [])[-5:]:
            evidence.append({"evidence_id": "obs-" + str(obs.id) + "-" + post["id"], "kind": "reddit_post",
                             "external": True, "text": post.get("title", "")})
    return evidence[:config.LLM_MAX_EVIDENCE]


def build_pitch_context(session, match, today):
    trend = session.get(Trend, match.trend_id)
    client = session.get(Client, match.client_id)
    ts = session.get(TrendScore, match.trend_score_id) if match.trend_score_id else None
    te = session.get(TimingEstimate, match.timing_estimate_id) if match.timing_estimate_id else None
    window = {"low": te.analog_delay_low, "high": te.analog_delay_high} if te else None
    active = ts.criteria.get("diversite", {}).get("detail", {}).get("active_types", []) if ts else []
    return {
        "trend": _trend_dict(trend), "client": _client_dict(client),
        "match": {"id": match.id, "market": match.market}, "window": window,
        "classification": ts.classification if ts else None, "active_types": active,
        "evidence": build_evidence(session, trend.id, last_closed_week(today)),
    }


def step_narrate(session, run, today):
    as_of = last_closed_week(today)
    alerts = list(session.scalars(select(Alert).join(ClientMatch, Alert.match_id == ClientMatch.id)
                                  .where(ClientMatch.run_id == run.id).order_by(ClientMatch.opportunity_score.desc())))
    for alert in alerts[:config.NARRATE_MAX_ITEMS]:
        match = session.get(ClientMatch, alert.match_id)
        out = agent.generate_pitch(build_pitch_context(session, match, today))
        session.add(Narrative(entity_type="client_match", entity_id=str(match.id), origin=out.origin,
                              model=out.model, content=out.content))
    scores = session.scalars(select(TrendScore).where(TrendScore.run_id == run.id, TrendScore.market.is_(None),
                                                      TrendScore.scoring_version == primary_version()))
    for ts in list(scores)[:config.NARRATE_MAX_ITEMS]:
        trend = session.get(Trend, ts.trend_id)
        out = agent.summarize_trend({
            "trend": _trend_dict(trend), "classification": ts.classification, "missing": ts.missing_criteria,
            "penalty": ts.criteria.get("_penalite", {}).get("value"),
            "active_types": ts.criteria.get("diversite", {}).get("detail", {}).get("active_types", []),
            "observation_weeks": ts.criteria.get("_confiance", {}).get("inputs", {}).get("observation_weeks"),
            "evidence": build_evidence(session, trend.id, as_of),
        })
        session.add(Narrative(entity_type="trend_score", entity_id=str(ts.id), origin=out.origin,
                              model=out.model, content=out.content))
    session.commit()


# ---------------------------------------------------------------------------
# Phase G : notifications (RG32), decouverte (RG28), what-if et brief
# Hors liste des steps : les noms des steps restent stables.
# ---------------------------------------------------------------------------


def notify(session, kind, text, details=None):
    """
    RG32 : message compatible Slack ({"text": ...}). dry_run par defaut :
    rien n'est envoye, l'envoi simule est journalise dans notifications.
    """
    payload = {"text": text, "trendpulse": {"kind": kind, **(details or {})}}
    dry = config.WEBHOOK_DRY_RUN or not config.WEBHOOK_URL
    row = Notification(kind=kind, payload=payload, dry_run=dry, status="dry_run" if dry else "a_envoyer")
    if not dry:
        try:
            code = collect.post_json(config.WEBHOOK_URL, payload)
            row.status = "envoye" if code < 300 else "echec"
            row.error = None if code < 300 else "HTTP " + str(code)
        except Exception as exc:
            row.status, row.error = "echec", type(exc).__name__ + " " + str(exc)[:200]
    session.add(row)
    session.commit()
    return row


def notify_alerts(session, run):
    """RG32 : seules les alertes RG13 (statut ALERT) sont envoyees, jamais les watchlists ni les projections."""
    rows = list(session.execute(select(Alert, ClientMatch).join(ClientMatch, Alert.match_id == ClientMatch.id)
                                .where(ClientMatch.run_id == run.id, Alert.status == AlertStatus.ALERT)))
    if not rows:
        return None
    lines = []
    for alert, m in rows:
        trend = session.get(Trend, m.trend_id)
        client = session.get(Client, m.client_id)
        lines.append("- " + trend.label + " pour " + client.label + " (" + m.market + ", données client "
                     + client.client_data_source.value + ") : opportunité " + str(round(m.opportunity_score or 0, 1)))
    text = "TrendPulse, alertes RG13 du run " + str(run.id) + " :\n" + "\n".join(lines)
    return notify(session, "alert", text, {"run_id": run.id, "alert_ids": [a.id for a, _m in rows]})


def send_weekly_digest(session):
    """RG32 : digest hebdomadaire redige par l'agent (lecture seule) puis envoye ou simule."""
    ans = agent.synthesis(session.connection(), "weekly_digest")
    return notify(session, "weekly_digest", "TrendPulse, digest hebdomadaire : " + ans.answer,
                  {"origin": ans.origin.value, "tools_used": ans.tools_used})


def _known_terms(session):
    terms = []
    for t in session.scalars(select(Trend)):
        terms.append(t.label)
        for values in (t.query_terms or {}).values():
            for v in values or []:
                terms.append(v if isinstance(v, str) else v.get("article", ""))
    return terms


def discover_candidates(session, today, fetch_gdelt=True):
    """
    RG28 : titres RSS deja collectes et titres GDELT recents, n-grammes en
    hausse, regroupement par embeddings Ollama si disponible. Stockage en
    trend_candidates avec le statut "a valider". Aucun score, aucune alerte.
    """
    p = config.DISCOVER
    as_of = today
    since = as_of - timedelta(weeks=p["recent_weeks"] + p["baseline_weeks"])
    trend_sector = {t.id: t.sector.value for t in session.scalars(select(Trend))}
    docs_by_sector = defaultdict(list)
    seen_links = set()
    for obs in session.scalars(select(SourceObservation).where(SourceObservation.source_name == "rss_trade_press",
                                                               SourceObservation.status == SourceStatus.success)):
        for it in (obs.raw_payload or {}).get("items", []):
            if it.get("link") in seen_links:
                continue
            seen_links.add(it.get("link"))
            docs_by_sector[trend_sector.get(obs.trend_id)].append(
                (collect.as_date(it["published"]), it.get("title"), "rss:" + it.get("feed", "")))
    warnings = []
    if fetch_gdelt and "gdelt" in config.ENABLED_SOURCES:
        for sector, query in p["gdelt_queries"].items():
            try:
                docs_by_sector[sector].extend(collect.gdelt_titles(query, since, as_of))
            except Exception as exc:
                warnings.append("gdelt " + sector + " : " + str(exc)[:120])
    known = _known_terms(session)
    stored = []
    for sector, docs in sorted(docs_by_sector.items(), key=lambda kv: str(kv[0])):
        cands = analytics.rising_ngrams(docs, as_of, known)
        if not cands:
            continue
        cands, method = analytics.group_candidates(cands, agent.embed_texts([c["ngram"] for c in cands]))
        for c in cands:
            row = TrendCandidate(ngram=c["ngram"], sector=sector, group_label=c["group_label"],
                                 recent_count=c["recent_count"], baseline_count=c["baseline_count"],
                                 rise_ratio=c["rise_ratio"], sources=c["sources"], status="à valider", grouping=method)
            session.add(row)
            stored.append(row)
    session.commit()
    return {"candidates": len(stored), "warnings": warnings}


def whatif(session, trend_id, client_id, market=None, innovation_cycle_months=None, today=None):
    """Recalcule fit, timing et opportunite avec des entrees modifiees, sans rien persister."""
    today = today or utcnow().date()
    as_of = last_closed_week(today)
    trend = session.get(Trend, trend_id)
    client = session.get(Client, client_id)
    if trend is None or client is None:
        return None
    td, cd = _trend_dict(trend), _client_dict(client)
    signals, _ = _load_signals(session, trend_id, as_of)
    cases = _cases(session)
    series = _case_series(session, trend.sector)

    def compute(cdict, mk):
        mk = mk or (cdict["active_markets"][0] if cdict["active_markets"] else config.GLOBAL_MARKET)
        res = timing_for_market(signals, mk, td["sector"], cases, client=cdict, case_series=series) \
            if any(s["market"] == mk for s in signals) else None
        fit = scoring.client_fit(td, cdict, mk, res["window"] if res else None, today)
        ts = session.scalar(select(TrendScore).where(TrendScore.trend_id == trend_id,
                                                     TrendScore.scoring_version == primary_version(),
                                                     TrendScore.market == mk).order_by(TrendScore.id.desc())) or \
            session.scalar(select(TrendScore).where(TrendScore.trend_id == trend_id, TrendScore.market.is_(None),
                                                    TrendScore.scoring_version == primary_version())
                           .order_by(TrendScore.id.desc()))
        durability = ts.durability if ts else None
        timing_value = res["timing_score"] if res else None
        opp, missing = scoring.opportunity_score(durability, fit["score"], timing_value)
        return {"market": mk, "fit": fit["score"], "fit_level": fit["level"], "timing": timing_value,
                "durability": durability, "opportunity": opp, "opportunity_level": scoring.opportunity_level(opp),
                "missing": missing, "innovation_cycle_months": cdict.get("innovation_cycle_months")}

    baseline = compute(cd, market)
    scenario_client = dict(cd)
    if innovation_cycle_months is not None:
        scenario_client["innovation_cycle_months"] = innovation_cycle_months
    scenario = compute(scenario_client, market or baseline["market"])
    session.rollback()
    return {"trend_id": trend_id, "client_id": client_id, "client_data_source": cd["client_data_source"],
            "baseline": baseline, "scenario": scenario, "persisted": False}


def build_brief(session, match_id):
    """Export d'un match : scores, recommandation, pitch, en JSON et en markdown."""
    m = session.get(ClientMatch, match_id)
    if m is None:
        return None
    trend, client = session.get(Trend, m.trend_id), session.get(Client, m.client_id)
    reco = session.scalar(select(RecommendationRecord).where(
        RecommendationRecord.run_id == m.run_id, RecommendationRecord.client_id == m.client_id,
        RecommendationRecord.trend_id == m.trend_id, RecommendationRecord.market == m.market))
    pitch = session.scalar(select(Narrative).where(Narrative.entity_type == "client_match",
                                                   Narrative.entity_id == str(m.id)).order_by(Narrative.id.desc()))
    data = {"match_id": m.id, "run_id": m.run_id, "trend": {"id": trend.id, "label": trend.label, "sector": trend.sector.value},
            "client": {"id": client.id, "label": client.label, "client_data_source": client.client_data_source.value},
            "market": m.market,
            "scores": {"durability": m.durability_score, "fit": m.fit_score, "timing": m.timing_score,
                       "opportunity": m.opportunity_score, "confidence": m.confidence_score, "level": m.level},
            "recommendation": reco.payload if reco else None,
            "pitch": {"origin": pitch.origin.value, "content": pitch.content} if pitch else None}

    def fmt(v):
        return "non disponible" if v is None else str(round(v, 1))
    md = ["# Brief " + trend.label + " pour " + client.label, "",
          "Marché : " + m.market + ". Données client : " + client.client_data_source.value + ".", "",
          "## Scores", ""]
    for k, v in data["scores"].items():
        md.append("- " + k + " : " + (v if isinstance(v, str) else fmt(v)))
    if reco:
        p = reco.payload
        md += ["", "## Recommandation (" + reco.bucket + ")", "", p.get("suggested_action", ""), "",
               "### Signaux contraires", ""] + ["- " + x for x in p.get("counter_evidence", [])]
        if p.get("based_on"):
            md += ["", "### Preuves", ""] + ["- " + b["summary"] + " (" + b["evidence_id"] + ")" for b in p["based_on"]]
    if pitch:
        md += ["", "## Pitch (" + pitch.origin.value + ")", ""] + ["- " + k + " : " + str(v) for k, v in pitch.content.items()
                                                                  if k != "cited_evidence_ids"]
    return data, "\n".join(md) + "\n"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

STEPS = [
    ("collect", step_collect),
    ("normalize", step_normalize),
    ("score_durability", step_score_durability),
    ("score_timing", step_score_timing),
    ("match_clients", step_match_clients),
    ("alert", step_alert),
    ("recommend", step_recommend),
    ("narrate", step_narrate),
]


def create_run(session, requested_by="cli", **params):
    run = Run(status=RunStatus.QUEUED, requested_by=requested_by, scoring_version=config.SCORING_VERSION,
              params={k: v for k, v in params.items() if v is not None}, source_status={})
    session.add(run)
    session.commit()
    return run


def execute_run(session, run):
    """Execute toutes les etapes. Le run doit deja etre RUNNING (reserve par le worker)."""
    today = _run_today(run)
    skip = set((run.params or {}).get("skip_steps") or [])
    steps_log = {}
    try:
        for name, fn in STEPS:
            if name in skip:
                steps_log[name] = "skipped"
                continue
            t0 = time.monotonic()
            fn(session, run, today)
            steps_log[name] = round(time.monotonic() - t0, 2)
        run.status = RunStatus.SUCCESS
        # RG32 : seules les alertes RG13 sont notifiees (dry_run par defaut)
        notify_alerts(session, run)
    except Exception as exc:
        session.rollback()
        logger.exception("run %s en echec", run.id)
        run.status = RunStatus.FAILED
        run.error = type(exc).__name__ + " " + str(exc)[:500]
    status = dict(run.source_status or {})
    status["_steps"] = steps_log
    run.source_status = status
    run.finished_at = utcnow()
    session.commit()
    return run


def run_now(session, **params):
    """Cree un run et l'execute immediatement (CLI et tests)."""
    run = create_run(session, **params)
    run.status = RunStatus.RUNNING
    run.started_at = utcnow()
    session.commit()
    return execute_run(session, run)


if __name__ == "__main__":
    from models import init_db, make_engine, make_session_factory
    from run import seed

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Pipeline TrendPulse")
    parser.add_argument("--sources", help="sources separees par des virgules")
    parser.add_argument("--trends", help="identifiants de tendances separes par des virgules")
    parser.add_argument("--skip-collect", action="store_true")
    args = parser.parse_args()

    engine = init_db(make_engine())
    with make_session_factory(engine)() as s:
        seed(s)
        r = run_now(s, requested_by="cli",
                    sources=args.sources.split(",") if args.sources else None,
                    trend_ids=args.trends.split(",") if args.trends else None,
                    skip_steps=["collect", "normalize"] if args.skip_collect else None)
        print(json.dumps({"run_id": r.id, "status": r.status.value, "error": r.error,
                          "source_status": r.source_status}, indent=2, ensure_ascii=False, default=str))
