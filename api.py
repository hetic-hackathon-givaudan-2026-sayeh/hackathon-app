"""
API REST TrendPulse.

Toutes les routes sauf /api/health exigent l'en-tete X-API-Key (exposition
ngrok). Sans API_KEY configuree, les routes protegees repondent 503.
Toutes les reponses sont des modeles Pydantic.

Lancement : uvicorn api:app --host 0.0.0.0 --port 8000
"""

import csv
import io
import json
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text

import agent
import analytics
import collect
import config
import pipeline
import scoring
from collect import last_closed_week
from models import (
    AlertOut, AlertStatus, ClientOut, ComparableCaseOut, DashboardKpis, DashboardOut, HealthOut, MatchOut,
    NarrativeRecordOut, PitchOut, RunCreate, RunOut, RunStatus, Sector, TimingOut, TimingWithCases,
    TrendDetailOut, TrendListItem, TrendOut, TrendScoreOut, WeeklyPoint, ExplainOut, Recommendation,
    RecommendationRecord, RecommendationRecordOut, RecommendationsOut, AgentChatIn, AgentChatOut, ChatMessage,
    SynthesisIn, SynthesisOut, BacktestResult, ClientIn, CsvImportRow, Feedback, FeedbackIn, FeedbackOut, ImportOut,
    TrendCandidate, TrendCandidateOut, WhatIfIn,
    Alert, Client, ClientMatch, ComparableCase, Narrative, Run, TimingEstimate, Trend, TrendScore, WeeklySignal,
)

VALIDATED_CLASSES = ("Validated Trend", "Accelerating Trend")


def require_api_key(x_api_key: Optional[str] = Header(default=None)):
    if not config.API_KEY:
        raise HTTPException(status_code=503, detail="API_KEY non configuree cote serveur")
    if not x_api_key or not secrets.compare_digest(x_api_key, config.API_KEY):
        raise HTTPException(status_code=401, detail="X-API-Key invalide ou absente")


def create_app(session_factory=None):
    state = {"factory": session_factory}

    @asynccontextmanager
    async def lifespan(app):
        if state["factory"] is None:
            from models import init_db, make_engine, make_session_factory
            from run import seed

            engine = init_db(make_engine())
            state["factory"] = make_session_factory(engine)
            with state["factory"]() as s:
                seed(s)
        yield

    app = FastAPI(title="TrendPulse API", version=config.SCORING_VERSION, docs_url="/docs", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def get_session():
        with state["factory"]() as s:
            yield s

    auth = [Depends(require_api_key)]

    # -- helpers -----------------------------------------------------------

    def latest_scores(session, market=None):
        """Dernier score par tendance pour une portee (global si market est None)."""
        cond = TrendScore.market.is_(None) if market is None else TrendScore.market == market
        # Version principale seulement (v2), la v1 reste en base pour comparaison
        sub = select(func.max(TrendScore.id)).where(cond, TrendScore.scoring_version == pipeline.primary_version()) \
            .group_by(TrendScore.trend_id)
        return {s.trend_id: s for s in session.scalars(select(TrendScore).where(TrendScore.id.in_(sub)))}

    def latest_match_run_id(session):
        return session.scalar(select(func.max(ClientMatch.run_id)))

    def match_out(m, sources=None):
        # RG30 : chaque ligne porte le libelle reel ou simule de son client
        out = MatchOut.model_validate(m)
        out.client_data_source = (sources or {}).get(m.client_id)
        return out

    def client_sources(session):
        return {c.id: c.client_data_source.value for c in session.scalars(select(Client))}

    def alert_out(a, m):
        return AlertOut(id=a.id, match_id=a.match_id, run_id=m.run_id, trend_id=m.trend_id, client_id=m.client_id,
                        market=m.market, status=a.status, conditions=a.conditions, created_at=a.created_at)

    def run_alerts(session, status=None):
        run_id = latest_match_run_id(session)
        if run_id is None:
            return []
        q = (select(Alert, ClientMatch).join(ClientMatch, Alert.match_id == ClientMatch.id)
             .where(ClientMatch.run_id == run_id).order_by(ClientMatch.opportunity_score.desc()))
        if status:
            q = q.where(Alert.status == status)
        return [alert_out(a, m) for a, m in session.execute(q).all()]

    # -- routes ------------------------------------------------------------

    @app.get("/api/health", response_model=HealthOut)
    def health(session=Depends(get_session)):
        try:
            session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False
        return HealthOut(status="ok" if db_ok else "degraded", database=db_ok, llm=agent.check_ollama(),
                         enabled_sources=config.ENABLED_SOURCES, scoring_version=config.SCORING_VERSION)

    @app.get("/api/dashboard", response_model=DashboardOut, dependencies=auth)
    def dashboard(session=Depends(get_session)):
        scores = latest_scores(session)
        trends = {t.id: t for t in session.scalars(select(Trend))}
        values = [s.durability for s in scores.values() if s.durability is not None]
        alerts = run_alerts(session)
        last_run = session.scalar(select(Run).order_by(Run.id.desc()).limit(1))
        top = sorted(scores.values(), key=lambda s: (s.durability is not None, s.durability or 0), reverse=True)[:5]
        return DashboardOut(
            kpis=DashboardKpis(
                trends_tracked=len(trends),
                trends_validated_or_better=sum(1 for s in scores.values() if s.classification in VALIDATED_CLASSES),
                active_alerts=sum(1 for a in alerts if a.status == AlertStatus.ALERT),
                watchlist=sum(1 for a in alerts if a.status == AlertStatus.WATCHLIST),
                mean_durability=sum(values) / len(values) if values else None,
                last_run_id=last_run.id if last_run else None,
                last_run_status=last_run.status if last_run else None,
                last_run_finished_at=last_run.finished_at if last_run else None,
            ),
            top_trends=[TrendListItem(trend=TrendOut.model_validate(trends[s.trend_id]),
                                      latest_score=TrendScoreOut.model_validate(s)) for s in top],
            alerts=alerts[:5],
            source_status=(last_run.source_status if last_run else {}),
        )

    @app.get("/api/trends", response_model=list[TrendListItem], dependencies=auth)
    def list_trends(sector: Optional[Sector] = None, market: Optional[str] = None,
                    classification: Optional[str] = None, session=Depends(get_session)):
        scores = latest_scores(session, market.upper() if market else None)
        q = select(Trend).order_by(Trend.id)
        if sector:
            q = q.where(Trend.sector == sector)
        items = []
        for t in session.scalars(q):
            s = scores.get(t.id)
            if classification and (s is None or s.classification != classification):
                continue
            if market and s is None:
                continue
            items.append(TrendListItem(trend=TrendOut.model_validate(t),
                                       latest_score=TrendScoreOut.model_validate(s) if s else None))
        return sorted(items, key=lambda i: (i.latest_score is not None and i.latest_score.durability is not None,
                                            i.latest_score.durability if i.latest_score and i.latest_score.durability else 0),
                      reverse=True)

    @app.get("/api/trends/{trend_id}", response_model=TrendDetailOut, dependencies=auth)
    def trend_detail(trend_id: str, market: Optional[str] = None, session=Depends(get_session)):
        trend = session.get(Trend, trend_id)
        if trend is None:
            raise HTTPException(status_code=404, detail="Tendance inconnue")
        cond = TrendScore.market.is_(None) if not market else TrendScore.market == market.upper()
        history = list(session.scalars(select(TrendScore).where(
            TrendScore.trend_id == trend_id, cond, TrendScore.scoring_version == pipeline.primary_version())
            .order_by(TrendScore.id)))
        as_of = history[-1].as_of_week if history else last_closed_week(datetime.now(timezone.utc).date())
        start = as_of - timedelta(weeks=config.DURABILITY["window_weeks"] - 1)
        sig_q = select(WeeklySignal).where(WeeklySignal.trend_id == trend_id, WeeklySignal.week >= start,
                                           WeeklySignal.week <= as_of).order_by(WeeklySignal.week)
        if market:
            sig_q = sig_q.where(WeeklySignal.market == market.upper())
        signals = list(session.scalars(sig_q))
        origins = {}
        for s in signals:
            o = (s.sector_data or {}).get("data_origin", "reel")
            origins[o] = origins.get(o, 0) + 1
        timing_items = []
        last_timing_run = session.scalar(select(func.max(TimingEstimate.run_id)).where(TimingEstimate.trend_id == trend_id))
        if last_timing_run:
            for te in session.scalars(select(TimingEstimate).where(
                    TimingEstimate.run_id == last_timing_run, TimingEstimate.trend_id == trend_id,
                    TimingEstimate.client_id.is_(None)).order_by(TimingEstimate.market)):
                cases = session.scalars(select(ComparableCase).where(ComparableCase.case_id.in_(te.analog_case_ids or [])))
                timing_items.append(TimingWithCases(estimate=TimingOut.model_validate(te),
                                                    comparable_cases=[ComparableCaseOut.model_validate(c) for c in cases]))
        narrative = None
        if history:
            n = session.scalar(select(Narrative).where(
                Narrative.entity_type == "trend_score",
                Narrative.entity_id.in_([str(h.id) for h in history])).order_by(Narrative.id.desc()).limit(1))
            narrative = NarrativeRecordOut.model_validate(n) if n else None
        return TrendDetailOut(
            trend=TrendOut.model_validate(trend),
            score_history=[TrendScoreOut.model_validate(h) for h in history],
            weekly_signals=[WeeklyPoint(week=s.week, market=s.market, source_type=s.source_type,
                                        source_name=s.source_name, signal_volume=s.signal_volume,
                                        event_linked=s.event_linked,
                                        data_origin=(s.sector_data or {}).get("data_origin")) for s in signals],
            timing=timing_items, narrative=narrative, data_origins=origins,
        )

    @app.get("/api/matches", response_model=list[MatchOut], dependencies=auth)
    def matches(trend_id: Optional[str] = None, client_id: Optional[str] = None, market: Optional[str] = None,
                level: Optional[str] = None, limit: int = Query(default=100, le=500), session=Depends(get_session)):
        run_id = latest_match_run_id(session)
        if run_id is None:
            return []
        q = select(ClientMatch).where(ClientMatch.run_id == run_id)
        if trend_id:
            q = q.where(ClientMatch.trend_id == trend_id)
        if client_id:
            q = q.where(ClientMatch.client_id == client_id)
        if market:
            q = q.where(ClientMatch.market == market.upper())
        if level:
            q = q.where(ClientMatch.level == level)
        rows = sorted(session.scalars(q), key=lambda m: m.opportunity_score or 0, reverse=True)[:limit]
        sources = client_sources(session)
        return [match_out(m, sources) for m in rows]

    @app.get("/api/alerts", response_model=list[AlertOut], dependencies=auth)
    def alerts(status: Optional[AlertStatus] = None, session=Depends(get_session)):
        return run_alerts(session, status)

    @app.get("/api/clients", response_model=list[ClientOut], dependencies=auth)
    def clients(session=Depends(get_session)):
        return [ClientOut.model_validate(c) for c in session.scalars(select(Client).order_by(Client.id))]

    @app.get("/api/pitch/{trend_id}/{client_id}", response_model=PitchOut, dependencies=auth)
    def pitch(trend_id: str, client_id: str, market: Optional[str] = None, session=Depends(get_session)):
        run_id = latest_match_run_id(session)
        q = select(ClientMatch).where(ClientMatch.run_id == run_id, ClientMatch.trend_id == trend_id,
                                      ClientMatch.client_id == client_id)
        if market:
            q = q.where(ClientMatch.market == market.upper())
        candidates = sorted(session.scalars(q), key=lambda m: m.opportunity_score or 0, reverse=True)
        if not candidates:
            raise HTTPException(status_code=404, detail="Aucun match pour ce couple, lancez POST /api/runs")
        match = candidates[0]
        today = datetime.now(timezone.utc).date()
        ctx = pipeline.build_pitch_context(session, match, today)
        out = agent.generate_pitch(ctx)
        session.add(Narrative(entity_type="client_match", entity_id=str(match.id), origin=out.origin,
                              model=out.model, content=out.content))
        session.commit()
        return PitchOut(trend_id=trend_id, client_id=client_id, market=match.market, match=match_out(match),
                        narrative=out, evidence_ids=[e["evidence_id"] for e in ctx["evidence"]])

    @app.post("/api/runs", response_model=RunOut, status_code=202, dependencies=auth)
    def create_run(body: RunCreate, session=Depends(get_session)):
        known = set(collect.build_registry())
        unknown = sorted(set(body.sources or []) - known)
        if unknown:
            raise HTTPException(status_code=422, detail="Sources inconnues : " + ", ".join(unknown))
        if body.trend_ids:
            found = set(session.scalars(select(Trend.id).where(Trend.id.in_(body.trend_ids))))
            missing = sorted(set(body.trend_ids) - found)
            if missing:
                raise HTTPException(status_code=422, detail="Tendances inconnues : " + ", ".join(missing))
        run = pipeline.create_run(session, requested_by=body.requested_by, sources=body.sources,
                                  trend_ids=body.trend_ids,
                                  as_of_date=body.as_of_date.isoformat() if body.as_of_date else None)
        return RunOut.model_validate(run)

    @app.get("/api/runs/{run_id}", response_model=RunOut, dependencies=auth)
    def get_run(run_id: int, session=Depends(get_session)):
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run inconnu")
        return RunOut.model_validate(run)

    # -- phase D : explication et recommandations ------------------------------

    @app.get("/api/trend/{trend_id}/explain", response_model=ExplainOut, dependencies=auth)
    def explain(trend_id: str, market: Optional[str] = None, session=Depends(get_session)):
        """RG31 : contribution de chaque critere, la somme des points egale le score."""
        if session.get(Trend, trend_id) is None:
            raise HTTPException(status_code=404, detail="Tendance inconnue")
        mk = market.upper() if market else None
        ts = latest_scores(session, mk).get(trend_id)
        if ts is None:
            raise HTTPException(status_code=404, detail="Aucun score pour cette portee")
        crit = ts.criteria or {}
        penalty = (crit.get("_penalite") or {}).get("value") or 0
        items = scoring.explain_contributions(crit, penalty, ts.durability)
        te = None
        if mk:
            te = session.scalar(select(TimingEstimate).where(TimingEstimate.trend_id == trend_id,
                                                             TimingEstimate.market == mk,
                                                             TimingEstimate.client_id.is_(None))
                                .order_by(TimingEstimate.id.desc()))
        v2 = crit.get("_v2") or {}
        return ExplainOut(
            trend_id=trend_id, market=mk, scoring_version=ts.scoring_version, score=ts.durability,
            classification=ts.classification, contributions=items,
            points_total=sum(i["points"] for i in items) if ts.durability is not None else None,
            indicators_v2={"persistance": (crit.get("persistance") or {}).get("detail"),
                           "regularite": (crit.get("regularite") or {}).get("detail"),
                           "penalite": (crit.get("_penalite") or {}).get("detail"),
                           "saisonnalite": v2.get("season"), "debut_tendance": v2.get("trend_start")},
            flags=ts.flags or [], interval={"score_low": ts.score_low, "score_high": ts.score_high},
            analogs=te.analogs if te else None, missing_criteria=ts.missing_criteria or [],
        )

    def _latest_reco_run(session, client_id):
        return session.scalar(select(func.max(RecommendationRecord.run_id)).where(
            RecommendationRecord.client_id == client_id))

    @app.get("/api/recommendations/{client_id}", response_model=RecommendationsOut, dependencies=auth)
    def recommendations(client_id: str, market: Optional[str] = None, limit: int = Query(5, ge=1, le=5),
                        session=Depends(get_session)):
        client = session.get(Client, client_id)
        if client is None:
            raise HTTPException(status_code=404, detail="Client inconnu")
        run_id = _latest_reco_run(session, client_id)
        out = RecommendationsOut(client_id=client_id, client_data_source=client.client_data_source.value,
                                 run_id=run_id, market=market.upper() if market else None)
        if run_id is None:
            out.message = "Aucune recommandation calculee pour ce client"
            return out
        q = select(RecommendationRecord).where(RecommendationRecord.client_id == client_id,
                                               RecommendationRecord.run_id == run_id)
        if market:
            q = q.where(RecommendationRecord.market == market.upper())
        for r in session.scalars(q.order_by(RecommendationRecord.market, RecommendationRecord.rank)):
            if r.bucket == "sorti":
                out.sorties.append(r.payload)
                continue
            bucket = getattr(out, r.bucket)
            if r.rank <= min(limit, config.RECO_LIMITS[r.bucket]):
                bucket.append(Recommendation.model_validate(r.payload))
        return out

    @app.get("/api/recommendations/{client_id}/history", response_model=list[RecommendationRecordOut],
             dependencies=auth)
    def recommendations_history(client_id: str, session=Depends(get_session)):
        if session.get(Client, client_id) is None:
            raise HTTPException(status_code=404, detail="Client inconnu")
        return list(session.scalars(select(RecommendationRecord).where(RecommendationRecord.client_id == client_id)
                                    .order_by(RecommendationRecord.run_id, RecommendationRecord.id)))

    # -- phase E : agent en lecture seule (RG17, RG18) --------------------------

    def agent_connection(session):
        """Connexion de l'agent : role agent_ro si AGENT_DATABASE_URL est fourni, sinon la base courante."""
        if config.AGENT_DATABASE_URL:
            if "agent_engine" not in state:
                from models import make_engine
                state["agent_engine"] = make_engine(config.AGENT_DATABASE_URL)
            return state["agent_engine"].connect()
        return session.connection()

    @app.post("/api/agent/chat", response_model=AgentChatOut, dependencies=auth)
    def agent_chat(body: AgentChatIn, session=Depends(get_session)):
        n = config.AGENT_MEMORY_EXCHANGES * 2
        history = list(session.scalars(select(ChatMessage).where(ChatMessage.session_id == body.session_id)
                                       .order_by(ChatMessage.id.desc()).limit(n)))[::-1]
        conn = agent_connection(session)
        try:
            ans = agent.chat(conn, body.message, body.context.model_dump() if body.context else None,
                             [{"role": h.role, "content": h.content} for h in history])
        finally:
            if config.AGENT_DATABASE_URL:
                conn.close()
        session.add(ChatMessage(session_id=body.session_id, role="user", content=body.message))
        session.add(ChatMessage(session_id=body.session_id, role="assistant", content=ans.answer,
                                origin=ans.origin.value, tools_used=ans.tools_used))
        session.commit()
        return AgentChatOut(session_id=body.session_id, **ans.model_dump())

    @app.post("/api/agent/synthesis", response_model=SynthesisOut, dependencies=auth)
    def agent_synthesis(body: SynthesisIn, session=Depends(get_session)):
        if body.kind.value == "trend_brief" and not body.trend_id:
            raise HTTPException(status_code=422, detail="trend_id requis pour trend_brief")
        if body.kind.value == "client_brief" and not body.client_id:
            raise HTTPException(status_code=422, detail="client_id requis pour client_brief")
        conn = agent_connection(session)
        try:
            ans = agent.synthesis(conn, body.kind.value, body.trend_id, body.client_id,
                                  body.market.upper() if body.market else None)
        finally:
            if config.AGENT_DATABASE_URL:
                conn.close()
        entity = body.trend_id or body.client_id or "global"
        row = Narrative(entity_type="synthesis_" + body.kind.value, entity_id=entity, origin=ans.origin,
                        model=config.AGENT_LLM_MODEL if ans.origin.value == "llm" else None,
                        content=ans.model_dump(mode="json"))
        session.add(row)
        session.commit()
        return SynthesisOut(kind=body.kind, narrative_id=row.id, **ans.model_dump())

    # -- phase F : projection statistique optionnelle (RG19) --------------------

    @app.get("/api/predict/{trend_id}", dependencies=auth)
    def predict(trend_id: str, market: Optional[str] = None, horizon_weeks: int = Query(8, ge=1, le=26),
                session=Depends(get_session)):
        """Projection et challenger. 501 si scikit-learn ou ruptures sont absents. Ne cree jamais d'alerte."""
        if not analytics.ml_extras_available():
            raise HTTPException(status_code=501, detail="Extras absents : installer requirements-ml.txt")
        if session.get(Trend, trend_id) is None:
            raise HTTPException(status_code=404, detail="Tendance inconnue")
        mk = market.upper() if market else None
        as_of = last_closed_week(datetime.now(timezone.utc).date())
        history = pipeline._load_history(session, trend_id, as_of)
        weeks, values = pipeline.scope_activity(history, mk)
        proj = analytics.projection(values, weeks, horizon_weeks)
        ts = latest_scores(session, mk).get(trend_id)
        train = [{"label": b.label, "features": b.features, "as_of": b.as_of} for b in session.scalars(
            select(BacktestResult).where(BacktestResult.version == config.SCORING_VERSION_V2))]
        if ts is None:
            chal = {"status": "non disponible", "reason": "aucun score v2 pour cette portee"}
        else:
            rule = "durable" if ts.classification in VALIDATED_CLASSES else "buzz"
            chal = analytics.challenger(train, pipeline.score_features(ts.criteria or {}), rule)
        return {"trend_id": trend_id, "market": mk or "global", "label": analytics.PROJECTION_LABEL,
                "projection": proj, "challenger": chal, "alert_triggered": False}

    # -- phase G : fonctionnalites commerciales -------------------------------

    @app.post("/api/clients/import", response_model=ImportOut, dependencies=auth)
    async def clients_import(request: Request, kind: str = Query(..., pattern="^(clients|sales)$"),
                             client_id: Optional[str] = None):
        """
        RG30 : import du vrai referentiel client (kind=clients) ou des ventes
        (kind=sales, client_id requis) en JSON (liste) ou CSV (text/csv).
        Les clients importes sont client_data_source = reel. Les ventes vont
        dans data/import et passent par le collecteur csv_import (RG22).
        """
        raw = (await request.body()).decode("utf-8", errors="replace")
        is_csv = "csv" in (request.headers.get("content-type") or "")
        try:
            rows = list(csv.DictReader(io.StringIO(raw))) if is_csv else json.loads(raw or "[]")
        except ValueError:
            raise HTTPException(status_code=422, detail="Corps illisible : JSON (liste) ou CSV attendu")
        if not isinstance(rows, list):
            raise HTTPException(status_code=422, detail="Une liste de lignes est attendue")
        with state["factory"]() as session:
            if kind == "clients":
                return import_clients(session, rows, is_csv)
            if not client_id or session.get(Client, client_id) is None:
                raise HTTPException(status_code=422, detail="client_id existant requis pour importer des ventes")
            return import_sales(session, rows, client_id)

    def import_clients(session, rows, is_csv):
        accepted, rejected = 0, []
        for i, row in enumerate(rows, start=2 if is_csv else 0):
            try:
                if is_csv:
                    row = {k: v for k, v in row.items() if v not in (None, "")}
                    for key in ("brand_values", "target_segments", "product_categories", "active_markets"):
                        if key in row:
                            row[key] = [x.strip() for x in row[key].split("|") if x.strip()]
                data = ClientIn.model_validate({**row, "client_data_source": "reel"})
            except (ValidationError, AttributeError, TypeError) as exc:
                rejected.append({"line": i, "reason": str(exc)[:200]})
                continue
            existing = session.get(Client, data.id)
            if existing is not None and existing.client_data_source.value != "reel":
                # Jamais de melange : un identifiant de demo ne devient pas un client reel
                rejected.append({"line": i, "reason": "identifiant deja utilise par un client simule"})
                continue
            values = data.model_dump(mode="json")
            if existing is None:
                session.add(Client(**values))
            else:
                for k, v in values.items():
                    setattr(existing, k, v)
            accepted += 1
        session.commit()
        return ImportOut(kind="clients", accepted=accepted, rejected=rejected)

    def import_sales(session, rows, client_id):
        trend_ids = {t for t in session.scalars(select(Trend.id))}
        accepted, rejected = [], []
        for i, row in enumerate(rows, start=2):
            try:
                data = CsvImportRow.model_validate({k: (str(v).strip() if v is not None else "") for k, v in row.items()})
            except (ValidationError, AttributeError) as exc:
                rejected.append({"line": i, "reason": str(exc)[:200]})
                continue
            if data.concept_id not in trend_ids:
                rejected.append({"line": i, "reason": "concept_id inconnu"})
                continue
            accepted.append(data)
        name = None
        if accepted:
            folder = Path(config.CSV_IMPORT_DIR)
            folder.mkdir(parents=True, exist_ok=True)
            name = "client_" + re.sub(r"[^A-Za-z0-9_-]", "_", client_id) + "_" + \
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".csv"
            with open(folder / name, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["concept_id", "market", "week", "metric", "value", "source_name"])
                for d in accepted:
                    writer.writerow([d.concept_id, d.market, d.week.isoformat(), d.metric, d.value, d.source_name])
        return ImportOut(kind="sales", accepted=len(accepted), rejected=rejected, file=name)

    @app.get("/api/trackrecord", dependencies=auth)
    def trackrecord(session=Depends(get_session)):
        """Resultats du backtest stockes, libelles "retro-test sur N cas". Aucun taux de precision."""
        rows = list(session.scalars(select(BacktestResult).order_by(BacktestResult.id)))
        batches = {}
        for r in rows:
            b = batches.setdefault(r.batch, {"batch": r.batch, "cases": {}})
            case = b["cases"].setdefault((r.term, r.market, str(r.as_of)), {
                "term": r.term, "market": r.market, "as_of": r.as_of, "label_equipe": r.label})
            case[r.version] = {"durability": r.durability, "classification": r.classification,
                               "score_low": r.score_low, "score_high": r.score_high}
        out = []
        for b in batches.values():
            cases = list(b["cases"].values())
            out.append({"batch": b["batch"], "label": "rétro-test sur " + str(len(cases)) + " cas", "cases": cases})
        return {"label": "rétro-test sur " + str(sum(len(b["cases"]) for b in out)) + " cas",
                "note": "résultats descriptifs, aucun taux de précision", "batches": out}

    @app.post("/api/feedback", response_model=FeedbackOut, status_code=201, dependencies=auth)
    def feedback(body: FeedbackIn, session=Depends(get_session)):
        """RG20 : stocke le retour commercial. Les poids ne sont jamais modifies."""
        if session.get(ClientMatch, body.match_id) is None:
            raise HTTPException(status_code=404, detail="Match inconnu")
        row = Feedback(match_id=body.match_id, status=body.status, note=body.note)
        session.add(row)
        session.commit()
        return row

    @app.post("/api/whatif", dependencies=auth)
    def whatif(body: WhatIfIn, session=Depends(get_session)):
        res = pipeline.whatif(session, body.trend_id, body.client_id, body.market.upper() if body.market else None,
                              body.innovation_cycle_months)
        if res is None:
            raise HTTPException(status_code=404, detail="Tendance ou client inconnu")
        return res

    @app.get("/api/brief/{match_id}", dependencies=auth)
    def brief(match_id: int, format: str = Query("json", pattern="^(json|markdown)$"), session=Depends(get_session)):
        res = pipeline.build_brief(session, match_id)
        if res is None:
            raise HTTPException(status_code=404, detail="Match inconnu")
        data, md = res
        if format == "markdown":
            return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")
        return {**data, "markdown": md}

    @app.get("/api/discover", response_model=list[TrendCandidateOut], dependencies=auth)
    def discover(limit: int = Query(50, ge=1, le=200), session=Depends(get_session)):
        """RG28 : candidats "a valider" (calcules par python run.py discover). Jamais scores ni alertes."""
        return list(session.scalars(select(TrendCandidate).order_by(TrendCandidate.id.desc()).limit(limit)))

    return app


app = create_app()
