"""
Point d'entree des commandes : worker, scheduler, seed, backtest, db-upgrade, discover.

  python run.py worker       worker de runs avec scheduler embarque (service Docker)
  python run.py scheduler    scheduler seul
  python run.py seed         chargement des referentiels
  python run.py backtest     backtest du Trend Durability Score
  python run.py db-upgrade   ajout idempotent des tables et colonnes manquantes
  python run.py discover     candidats de concepts emergents (RG28)
"""

import calendar
import csv
import json
import logging
import signal
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select, text

import collect
import config
import pipeline
from models import (
    upgrade_db, BacktestResult, CalendarEvent, CalendarEventIn, Client, ClientIn, ComparableCase, ComparableCaseIn, init_db, KnownEvent, KnownEventIn, make_engine, make_session_factory, Run, RunStatus, Trend, TrendIn,
)


# ===========================================================================
# Section : ex worker.py
# Worker : execute les runs QUEUED un par un.
#
# Reservation par SELECT ... FOR UPDATE SKIP LOCKED. Un seul run actif a la
# fois : sous PostgreSQL un verrou consultatif serialise les reservations, puis
# on verifie qu'aucun run n'est RUNNING. Un run RUNNING trop ancien (worker
# arrete brutalement) passe en FAILED au demarrage.
#
# Le worker embarque aussi le scheduler (section suivante) pour n'avoir que trois
# services Docker : db, api, worker.
#
# Lancement : python run.py worker
# ===========================================================================

log_worker = logging.getLogger("trendpulse.worker")
CLAIM_LOCK_KEY = 74_210_001


def utcnow():
    return datetime.now(timezone.utc)


def recover_stale_runs(session):
    limit = utcnow() - timedelta(minutes=config.RUN_STALE_MINUTES)
    stale = list(session.scalars(select(Run).where(Run.status == RunStatus.RUNNING)))
    count = 0
    for run in stale:
        started = run.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started is None or started < limit:
            run.status = RunStatus.FAILED
            run.error = "worker interrompu, run marque en echec au redemarrage"
            run.finished_at = utcnow()
            count += 1
    session.commit()
    return count


def claim_next_run(session):
    """Reserve le plus ancien run QUEUED. Retourne None si un run est deja actif ou si la file est vide."""
    if session.bind.dialect.name == "postgresql":
        # Verrou de transaction : deux workers ne peuvent pas reserver en meme temps
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CLAIM_LOCK_KEY})
    active = session.scalar(select(func.count(Run.id)).where(Run.status == RunStatus.RUNNING))
    if active:
        session.rollback()
        return None
    run = session.scalar(select(Run).where(Run.status == RunStatus.QUEUED).order_by(Run.id)
                         .limit(1).with_for_update(skip_locked=True))
    if run is None:
        session.rollback()
        return None
    run.status = RunStatus.RUNNING
    run.started_at = utcnow()
    session.commit()
    return run


def work_once(session_factory):
    """Reserve et execute un run. Retourne le run termine ou None."""
    with session_factory() as session:
        run = claim_next_run(session)
        if run is None:
            return None
        log_worker.info("worker | run %s demarre", run.id)
        run = pipeline.execute_run(session, run)
        log_worker.info("worker | run %s termine : %s", run.id, run.status.value)
        return run


def worker_main():
    from apscheduler.schedulers.background import BackgroundScheduler

    from models import init_db, make_engine, make_session_factory

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = init_db(make_engine())
    factory = make_session_factory(engine)
    with factory() as s:
        seed(s)
        n = recover_stale_runs(s)
        if n:
            log_worker.warning("worker | %s run(s) bloque(s) passe(s) en FAILED", n)

    bg = BackgroundScheduler(timezone="UTC")
    schedule_jobs(bg, factory)
    bg.start()

    stop = {"flag": False}

    def handle(signum, frame):
        log_worker.info("worker | arret demande")
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)

    while not stop["flag"]:
        try:
            if work_once(factory) is None:
                time.sleep(config.WORKER_POLL_SECONDS)
        except Exception:
            log_worker.exception("worker | erreur inattendue")
            time.sleep(config.WORKER_POLL_SECONDS)
    bg.shutdown(wait=False)



# ===========================================================================
# Section : ex scheduler.py
# Planification APScheduler. Le scheduler ne lance rien lui-meme : il cree des
# runs QUEUED que le worker execute (le scoring suit chaque collecte car chaque
# run enchaine toutes les etapes).
#
#   - Google Trends, Open Food Facts et sources quotidiennes : chaque jour
#   - Reddit : toutes les 6 heures
#
# Embarque dans le worker. Lancement autonome possible : python run.py scheduler
# ===========================================================================

log_scheduler = logging.getLogger("trendpulse.scheduler")


def enqueue_run(session_factory, sources, requested_by):
    """Cree un run QUEUED, sauf si un run identique attend deja."""
    with session_factory() as s:
        for run in s.scalars(select(Run).where(Run.status == RunStatus.QUEUED)):
            if sorted((run.params or {}).get("sources") or []) == sorted(sources):
                log_scheduler.info("scheduler | run %s deja en attente pour %s", run.id, sources)
                return None
        run = pipeline.create_run(s, requested_by=requested_by, sources=sources)
        log_scheduler.info("scheduler | run %s cree pour %s", run.id, sources)
        return run.id


def weekly_digest_job(session_factory):
    with session_factory() as s:
        pipeline.send_weekly_digest(s)


def schedule_jobs(scheduler, session_factory):
    daily = [x for x in config.SCHEDULE["daily_sources"] if x in config.ENABLED_SOURCES]
    if daily:
        scheduler.add_job(enqueue_run, "cron", hour=config.SCHEDULE["daily_hour"], id="daily_collect",
                          args=[session_factory, daily, "scheduler_daily"], replace_existing=True,
                          max_instances=1, coalesce=True)
    # RG32 : digest hebdomadaire le lundi (webhook en dry_run par defaut)
    scheduler.add_job(weekly_digest_job, "cron", day_of_week="mon", hour=config.SCHEDULE["daily_hour"] + 2,
                      id="weekly_digest", args=[session_factory], replace_existing=True, max_instances=1, coalesce=True)
    if "reddit" in config.ENABLED_SOURCES:
        scheduler.add_job(enqueue_run, "interval", hours=config.SCHEDULE["reddit_every_hours"], id="reddit_collect",
                          args=[session_factory, ["reddit"], "scheduler_reddit"], replace_existing=True,
                          max_instances=1, coalesce=True)
    return scheduler


def scheduler_main():
    from apscheduler.schedulers.blocking import BlockingScheduler

    from models import init_db, make_engine, make_session_factory

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    factory = make_session_factory(init_db(make_engine()))
    scheduler = schedule_jobs(BlockingScheduler(timezone="UTC"), factory)

    def stop(signum, frame):
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    scheduler.start()



# ===========================================================================
# Section : ex seed.py
# Chargement des referentiels depuis data/seed/.
#
# Idempotent : un deuxieme lancement met a jour les referentiels sans doublon.
# Les referentiels (tendances, clients, cas, evenements) ne sont pas soumis a
# RG14, seules les tables d'historique sont append-only.
#
# Lancement : python run.py seed
# ===========================================================================

log_seed = logging.getLogger("trendpulse.seed")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_seed_files(seed_dir=None):
    """Lit et valide les fichiers seed. Leve une erreur si un fichier est invalide."""
    seed_dir = Path(seed_dir or config.SEED_DIR)
    trends = [TrendIn.model_validate(t) for t in _read_json(seed_dir / "trends.json")]
    clients = [ClientIn.model_validate(c) for c in _read_json(seed_dir / "clients.json")]
    cases = []
    for row in _read_csv(seed_dir / "comparable_cases.csv"):
        row["verified"] = str(row.get("verified", "")).strip().lower() == "true"
        cases.append(ComparableCaseIn.model_validate(row))
    events = []
    for row in _read_csv(seed_dir / "known_events.csv"):
        row = {k: (v if v != "" else None) for k, v in row.items()}
        events.append(KnownEventIn.model_validate(row))
    return trends, clients, cases, events


def seed(session, seed_dir=None):
    trends, clients, cases, events = load_seed_files(seed_dir)

    for t in trends:
        data = t.model_dump(mode="json")
        data["profile"] = t.profile.model_dump(mode="json")
        existing = session.get(Trend, t.id)
        if existing is None:
            session.add(Trend(**data))
        else:
            for key, value in data.items():
                setattr(existing, key, value)

    for c in clients:
        data = c.model_dump(mode="json")
        existing = session.get(Client, c.id)
        if existing is None:
            session.add(Client(**data))
        else:
            for key, value in data.items():
                setattr(existing, key, value)

    for case in cases:
        data = case.model_dump(mode="json")
        existing = session.get(ComparableCase, case.case_id)
        if existing is None:
            session.add(ComparableCase(**data))
        else:
            for key, value in data.items():
                setattr(existing, key, value)

    session.flush()
    for ev in events:
        found = session.scalar(select(KnownEvent).where(
            KnownEvent.trend_id == ev.trend_id,
            KnownEvent.event_date == ev.event_date,
            KnownEvent.event_label == ev.event_label,
        ))
        if found is None:
            session.add(KnownEvent(**ev.model_dump()))
        else:
            found.market = ev.market
            found.source_url = ev.source_url

    session.commit()
    calendar_report = seed_calendar(session, seed_dir)
    verified = sum(1 for c in cases if c.verified)
    log_seed.info("seed | %s tendances, %s clients, %s cas (%s verifies), %s evenements, calendrier %s",
                  len(trends), len(clients), len(cases), verified, len(events), calendar_report)
    return {"trends": len(trends), "clients": len(clients), "cases": len(cases),
            "verified_cases": verified, "events": len(events), "calendar": calendar_report}


def holiday_events(markets, years):
    """RG23a : jours feries par pays, issus de la bibliotheque holidays (aucune date saisie a la main)."""
    import holidays

    rows = []
    for market in markets:
        try:
            days = holidays.country_holidays(market, years=years)
        except NotImplementedError:
            continue
        for d, name in sorted(days.items()):
            rows.append(CalendarEventIn(date_start=d, date_end=d, name=str(name)[:200], market=market,
                                        kind="jour_ferie", source_url=config.CALENDAR["holidays_source_url"]))
    return rows


def black_friday(year):
    """
    RG23a : Black Friday = lendemain du quatrieme jeudi de novembre
    (Thanksgiving aux Etats-Unis le quatrieme jeudi, Black Friday le vendredi suivant).
    """
    first = date(year, 11, 1)
    first_thursday = first + timedelta(days=(3 - first.weekday()) % 7)
    return first_thursday + timedelta(weeks=3, days=1)


def black_friday_events(markets, years):
    rows = []
    for market in [m for m in config.BLACK_FRIDAY_MARKETS if m in markets]:
        for y in years:
            d = black_friday(y)
            rows.append(CalendarEventIn(date_start=d, date_end=d, name="Black Friday", market=market,
                                        kind="commercial", source_url=None))
    return rows


def load_calendar_csv(path):
    """Evenements mobiles sources. Une ligne sans date (TODO) est ignoree, jamais completee."""
    rows, todo = [], []
    if not Path(path).exists():
        return rows, todo
    for row in _read_csv(path):
        if "TODO" in (row.get("date_start") or "TODO") or "TODO" in (row.get("date_end") or "TODO"):
            todo.append(row.get("name"))
            continue
        rows.append(CalendarEventIn.model_validate({k: row.get(k) or None for k in
                                                    ("date_start", "date_end", "name", "market", "kind", "source_url")}))
    return rows, todo


def seed_calendar(session, seed_dir=None):
    """Remplit calendar_events (referentiel, idempotent)."""
    year = datetime.now(timezone.utc).year
    years = list(range(year - config.CALENDAR["years_back"], year + config.CALENDAR["years_forward"] + 1))
    path = Path(seed_dir) / "calendar_events.csv" if seed_dir else Path(config.CALENDAR_EVENTS_PATH)
    mobile, todo = load_calendar_csv(path)
    existing = set(session.execute(select(CalendarEvent.date_start, CalendarEvent.name, CalendarEvent.market)).all())
    added = 0
    for ev in holiday_events(config.MARKETS, years) + black_friday_events(config.MARKETS, years) + mobile:
        key = (ev.date_start, ev.name, ev.market)
        if key in existing:
            continue
        existing.add(key)
        session.add(CalendarEvent(**ev.model_dump()))
        added += 1
    session.commit()
    return {"added": added, "todo_without_date": len(todo)}




def seed_main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = init_db(make_engine())
    with make_session_factory(engine)() as session:
        print(seed(session))



# ===========================================================================
# Section : ex backtest.py
# Backtest du Trend Durability Score sur des cas etiquetes par l'equipe.
#
# data/validation_cases.csv : term, sector, label (durable, buzz, seasonal), as_of_date.
#   - seules les lignes avec un label et une as_of_date sont utilisees
#   - as_of_date au format AAAA-MM est lue comme le dernier jour du mois
#   - les series sont demandees jusqu'a as_of_date puis tronquees a cette date :
#     aucune donnee posterieure n'entre dans le calcul (pas de fuite temporelle)
#   - calibration du seuil durable sur une moitie des cas, evaluation sur l'autre
#   - sortie : matrice de confusion et rapport JSON dans data/backtest_report.json
#
# Ce backtest mesure la coherence du score sur un petit echantillon. Il ne
# donne AUCUN taux de precision commerciale.
#
# Lancement : python run.py backtest
# ===========================================================================

log_backtest = logging.getLogger("trendpulse.backtest")

LABELS = ["durable", "buzz", "seasonal"]
HISTORY_WEEKS = 156
# Autocorrelation a 52 semaines au-dela de laquelle on parle de saisonnalite (A VALIDER)
SEASONAL_ACF_MIN = config.SEASONAL_ACF_MIN
REPORT_PATH = config.DATA_DIR / "backtest_report.json"


def parse_as_of(value):
    value = (value or "").strip()
    if not value:
        return None
    parts = [int(p) for p in value.split("-")]
    if len(parts) == 2:
        return date(parts[0], parts[1], calendar.monthrange(parts[0], parts[1])[1])
    return date(parts[0], parts[1], parts[2])


def load_cases(path=None):
    """Retourne (cas utilisables, lignes ignorees avec la raison)."""
    path = path or (config.DATA_DIR / "validation_cases.csv")
    usable, skipped = [], []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            term = (row.get("term") or "").strip()
            label = (row.get("label") or "").strip()
            as_of = parse_as_of(row.get("as_of_date"))
            if not term or term == "TODO" or label not in LABELS or as_of is None:
                skipped.append({"term": term, "reason": "label ou as_of_date manquant"})
                continue
            usable.append({"term": term, "sector": row.get("sector") or "food_beverage", "label": label, "as_of": as_of})
    return usable, skipped


def truncate_payload(payload, as_of):
    """Supprime tout point posterieur a as_of (defense contre la fuite temporelle)."""
    series = {t: [p for p in pts if p["date"] <= as_of.isoformat()] for t, pts in (payload.get("series") or {}).items()}
    return {**payload, "series": series}


def autocorrelation(values, lag):
    n = len(values)
    if n <= lag + 4:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values)
    if var == 0:
        return None
    return sum((values[i] - mean) * (values[i - lag] - mean) for i in range(lag, n)) / var


def _signal_as_dict(sig):
    """WeeklySignalIn vers le format attendu par pipeline.durability_for_scope."""
    return {"id": 0, "observation_id": 0, "market": sig.market, "week": sig.week,
            "source_type": sig.source_type.value, "source_name": sig.source_name,
            "volume": sig.signal_volume, "data": sig.sector_data}


def score_case(case):
    """Collecte Google Trends jusqu'a as_of, normalise, calcule durabilite et saisonnalite."""
    as_of = case["as_of"]
    since = as_of - timedelta(weeks=HISTORY_WEEKS)
    trend = {"id": "bt_" + case["term"].replace(" ", "_"), "label": case["term"], "sector": case["sector"],
             "query_terms": {"google_trends": [case["term"]]}}
    res = collect.GoogleTrendsCollector().safe_collect(trend, case.get("market") or None, since, as_of)
    if res["status"] != "success":
        return {**case, "error": res.get("error"), "durability": None, "acf52": None}
    payload = truncate_payload(res["payload"], as_of)
    obs = {"id": 0, "source_name": "google_trends", "source_url": None, "market": res["market"],
           "status": "success", "raw_payload": payload}
    # today = lendemain d'as_of : la derniere semaine close est la semaine d'as_of au plus
    signals = [_signal_as_dict(s) for s in collect.normalize_observation(obs, trend, [], as_of + timedelta(days=1))]
    signals = [s for s in signals if s["week"] <= as_of]
    as_of_week = collect.last_closed_week(as_of + timedelta(days=1))
    start = as_of_week - timedelta(weeks=config.DURABILITY["window_weeks"] - 1)
    window = [s for s in signals if s["week"] >= start]
    scored = pipeline.durability_for_scope(window, [], 0, None, start, as_of_week, as_of)
    full = sorted(signals, key=lambda s: s["week"])
    # v2 sur tout l'historique tronque a as_of (desaisonnalisation possible des 104 semaines)
    v2 = pipeline.durability_v2_for_scope(full, scored, [], 0, None, as_of, set())
    return {**case, "durability": scored["durability"], "classification": scored["classification"],
            "missing": scored["missing"], "acf52": autocorrelation([s["volume"] for s in full], 52),
            "last_signal_week": full[-1]["week"] if full else None,
            "v2_durability": v2["durability"], "v2_classification": v2["classification"],
            "v2_score_low": v2["score_low"], "v2_score_high": v2["score_high"], "v2_flags": v2["flags"],
            "v2_features": pipeline.score_features(v2["criteria"])}


def cases_as_of(row):
    value = row.get("as_of")
    return date.fromisoformat(value) if isinstance(value, str) else value


def store_backtest(session, results, batch=None):
    """Enregistre les resultats (v1 et v2) dans backtest_results, append-only. Aucun taux de precision."""
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    for r in results:
        if r.get("durability") is None and r.get("v2_durability") is None:
            continue
        common = {"batch": batch, "term": r["term"], "sector": r.get("sector"), "market": r.get("market"),
                  "as_of": r.get("as_of"), "label": r.get("label")}
        session.add(BacktestResult(**common, version=config.SCORING_VERSION, durability=r.get("durability"),
                                   classification=r.get("classification")))
        session.add(BacktestResult(**common, version=config.SCORING_VERSION_V2, durability=r.get("v2_durability"),
                                   classification=r.get("v2_classification"), score_low=r.get("v2_score_low"),
                                   score_high=r.get("v2_score_high"), features=r.get("v2_features")))
    session.commit()
    return batch


def predict(result, threshold):
    if result.get("acf52") is not None and result["acf52"] >= SEASONAL_ACF_MIN:
        return "seasonal"
    if result.get("durability") is None:
        return None
    return "durable" if result["durability"] >= threshold else "buzz"


def split(results):
    """Deux moitiees disjointes, alternees par label pour garder un melange."""
    calib, evalu = [], []
    for i, r in enumerate(sorted(results, key=lambda r: (r["label"], r["term"]))):
        (calib if i % 2 == 0 else evalu).append(r)
    return calib, evalu


def calibrate(calib):
    """Seuil durable qui maximise les bonnes reponses sur la moitie de calibration."""
    best, best_hits = config.DURABILITY_CLASSES[2][0], -1
    for t in range(20, 91):
        hits = sum(1 for r in calib if predict(r, t) == r["label"])
        if hits > best_hits:
            best, best_hits = t, hits
    return best


def confusion(results, threshold):
    matrix = {real: {pred: 0 for pred in LABELS + ["non_calculable"]} for real in LABELS}
    for r in results:
        matrix[r["label"]][predict(r, threshold) or "non_calculable"] += 1
    return matrix


def run_backtest(cases):
    results = [score_case(c) for c in cases]
    usable = [r for r in results if r.get("durability") is not None or r.get("acf52") is not None]
    report = {"cases_used": len(usable), "cases_failed": [r["term"] for r in results if r not in usable],
              "warning": "Echantillon tres petit. Ce rapport ne donne aucun taux de precision commerciale."}
    if len(usable) < 4:
        report["status"] = "insuffisant"
        report["message"] = "Cas insuffisants pour calibrer et evaluer (minimum 4 cas avec as_of_date)."
        report["threshold"] = None
        report["confusion_matrix"] = None
    else:
        calib, evalu = split(usable)
        threshold = calibrate(calib)
        report.update({"status": "ok", "threshold": threshold,
                       "calibration_terms": [r["term"] for r in calib],
                       "evaluation_terms": [r["term"] for r in evalu],
                       "confusion_matrix": confusion(evalu, threshold)})
    report["per_case"] = [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()} for r in results]
    return report


def print_matrix(matrix):
    cols = LABELS + ["non_calculable"]
    print("reel \\ predit".ljust(16) + "".join(c.ljust(16) for c in cols))
    for real in LABELS:
        print(real.ljust(16) + "".join(str(matrix[real][c]).ljust(16) for c in cols))




# Cas de comparaison v1 contre v2 demandes par l'equipe (data/validation_cases.csv)
COMPARE_TERMS = ["yuzu", "dubai chocolate", "k-beauty"]


def compare_versions(path=None, terms=None, session=None):
    """
    Classe et score v1 contre v2 pour les cas connus a leur as_of_date.
    Un cas sans as_of_date n'est pas calcule. Aucun taux de precision.
    """
    path = path or (config.DATA_DIR / "validation_cases.csv")
    terms = terms or COMPARE_TERMS
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            term = (row.get("term") or "").strip()
            if term not in terms:
                continue
            as_of = parse_as_of(row.get("as_of_date"))
            base = {"term": term, "market": (row.get("market") or "").strip() or None,
                    "sector": row.get("sector") or "food_beverage", "label": (row.get("label") or "").strip() or None}
            if as_of is None:
                rows.append({**base, "status": "as_of_date manquante, cas non calcule"})
                continue
            res = score_case({**base, "as_of": as_of})
            if session is not None:
                store_backtest(session, [res], batch="compare-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
            rows.append({**base, "as_of": as_of.isoformat(), "status": "calcule" if res.get("durability") is not None else
                         "non calculable : " + str(res.get("error")),
                         "v1": {"score": res.get("durability"), "classe": res.get("classification")},
                         "v2": {"score": res.get("v2_durability"), "classe": res.get("v2_classification"),
                                "intervalle": [res.get("v2_score_low"), res.get("v2_score_high")],
                                "flags": res.get("v2_flags")}})
    return {"cases": rows, "warning": "Comparaison descriptive sur quelques cas. Aucun taux de precision."}


def _fmt_score(v):
    return "n/d" if v is None else str(round(v, 1))


def print_comparison(report):
    for r in report["cases"]:
        head = r["term"] + (" (" + r["market"] + ")" if r.get("market") else "")
        if "v1" not in r:
            print(head + " : " + r["status"])
            continue
        v1, v2 = r["v1"], r["v2"]
        low, high = v2["intervalle"]
        print(head + " au " + r["as_of"] + " | v1 " + _fmt_score(v1["score"]) + " " + str(v1["classe"])
              + " | v2 " + _fmt_score(v2["score"]) + " [" + _fmt_score(low) + " a " + _fmt_score(high) + "] "
              + str(v2["classe"]) + " | flags " + ", ".join(v2["flags"] or []))
    print(report["warning"])


def backtest_main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="run.py backtest")
    parser.add_argument("--compare", nargs=2, metavar=("V1", "V2"))
    args, _ = parser.parse_known_args(argv)
    if args.compare:
        if sorted(args.compare) != ["v1", "v2"]:
            print("Seule la comparaison v1 v2 est disponible.")
            return
        logging.basicConfig(level=logging.WARNING)
        with make_session_factory(init_db(make_engine()))() as session:
            print_comparison(compare_versions(session=session))
        return
    _backtest_v1()


def _backtest_v1():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cases, skipped = load_cases()
    print(str(len(cases)) + " cas utilisables, " + str(len(skipped)) + " ignores (as_of_date ou label manquant)")
    report = run_backtest(cases)
    report["skipped"] = skipped
    with make_session_factory(init_db(make_engine()))() as session:
        report["batch"] = store_backtest(session, [{**r, "as_of": cases_as_of(r)} for r in report["per_case"]])
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    if report["confusion_matrix"]:
        print("Seuil durable calibre : " + str(report["threshold"]))
        print_matrix(report["confusion_matrix"])
    else:
        print(report["message"])
    print(report["warning"])
    print("Rapport : " + str(REPORT_PATH))


# ===========================================================================
# Section : ligne de commande
# ===========================================================================

def db_upgrade_main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    added = upgrade_db(make_engine())
    print("Colonnes ajoutees : " + (", ".join(added) if added else "aucune, schema a jour"))


def discover_main():
    """RG28 : candidats de concepts emergents, statut "a valider", jamais scores ni alertes."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with make_session_factory(init_db(make_engine()))() as session:
        res = pipeline.discover_candidates(session, datetime.now(timezone.utc).date())
    print(str(res["candidates"]) + " candidat(s) a valider enregistres dans trend_candidates")
    for w in res["warnings"]:
        print("avertissement : " + w)


COMMANDS = {
    "worker": worker_main,
    "scheduler": scheduler_main,
    "seed": seed_main,
    "backtest": backtest_main,
    "db-upgrade": db_upgrade_main,
    "discover": discover_main,
}


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Commandes TrendPulse")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args, rest = parser.parse_known_args(argv)
    if args.command == "backtest":
        backtest_main(rest)
        return
    COMMANDS[args.command]()


if __name__ == "__main__":
    main()
