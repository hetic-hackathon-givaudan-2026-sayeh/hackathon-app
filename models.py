"""
Modele de donnees : contrats Pydantic, tables SQLAlchemy et connexion.
"""

from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import (
    Boolean, create_engine, inspect, text, Date, DateTime, Enum as SAEnum, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

import config


# ===========================================================================
# Section : ex schemas.py
# Contrats de donnees (Pydantic v2).
#
# Listes fermees en Enum. Les schemas sectoriels ont tous leurs champs
# optionnels : None veut dire indisponible, jamais zero (RG05).
# ===========================================================================

# ---------------------------------------------------------------------------
# Listes fermees
# ---------------------------------------------------------------------------


class Sector(str, Enum):
    food_beverage = "food_beverage"
    fragrance = "fragrance"
    beauty = "beauty"


class SourceType(str, Enum):
    # Les 9 types comptes dans la diversite (RG03)
    social = "social"
    presse_generale = "presse_generale"
    presse_professionnelle = "presse_professionnelle"
    recherche_academique = "recherche_academique"
    brevet = "brevet"
    lancement_produit = "lancement_produit"
    retail_ecommerce = "retail_ecommerce"
    salon_professionnel = "salon_professionnel"
    institutionnel_reglementaire = "institutionnel_reglementaire"
    # Hors liste de diversite : Wikipedia Pageviews sert seulement a la persistance
    reference_persistance = "reference_persistance"


class SourceStatus(str, Enum):
    success = "success"
    error = "error"
    disabled = "disabled"
    not_applicable = "not_applicable"
    # RG21 : cle absente, aucun flux active ou aucun fichier a importer.
    # Comme error, ne produit jamais de zero (RG05).
    indisponible = "indisponible"


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class MaturityStage(str, Enum):
    signal_faible = "signal_faible"
    emergent = "emergent"
    croissance = "croissance"
    maturite = "maturite"
    declin = "declin"


class ConfidenceLevel(str, Enum):
    faible = "faible"
    moyen = "moyen"
    eleve = "élevé"


class DataOrigin(str, Enum):
    reel = "reel"
    simule = "simule"
    estime = "estime"


class ClientDataSource(str, Enum):
    reel = "reel"
    simule = "simule"
    absent = "absent"


class Origin(str, Enum):
    llm = "llm"
    template = "template"


class AlertStatus(str, Enum):
    ALERT = "ALERT"
    WATCHLIST = "WATCHLIST"


# ---------------------------------------------------------------------------
# Referentiels (seed)
# ---------------------------------------------------------------------------


class TrendProfile(BaseModel):
    """Profil estime de la tendance, sert au Client Fit (RG07)."""
    price_tier: Optional[str] = None
    target_segments: Optional[list[str]] = None
    target_age_range: Optional[list[int]] = None
    claim_types: Optional[list[str]] = None
    estimated: bool = True


class TrendIn(BaseModel):
    id: str
    label: str
    sector: Sector
    category: str
    query_terms: dict[str, Any] = Field(default_factory=dict)
    profile: TrendProfile = Field(default_factory=TrendProfile)
    to_confirm: bool = False
    notes: Optional[str] = None


class LaunchWindow(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None


class RecentLaunch(BaseModel):
    label: str
    date: Optional[str] = None
    category: Optional[str] = None
    ingredients: list[str] = Field(default_factory=list)


class ClientIn(BaseModel):
    """Client simule (RG08). Libelles generiques, jamais de marque reelle."""
    id: str
    label: str
    sector: Sector
    brand_positioning: Optional[str] = None
    price_tier: Optional[str] = None
    brand_values: list[str] = Field(default_factory=list)
    target_segments: list[str] = Field(default_factory=list)
    target_age_range: Optional[list[int]] = None
    product_categories: list[str] = Field(default_factory=list)
    recent_launches: list[RecentLaunch] = Field(default_factory=list)
    active_markets: list[str] = Field(default_factory=list)
    account_size_tier: Optional[str] = None
    strategic_flag: Optional[bool] = None
    innovation_cycle_months: Optional[int] = None
    next_launch_window: Optional[LaunchWindow] = None
    relationship_status: Optional[str] = None
    client_data_source: ClientDataSource = ClientDataSource.simule


class ComparableCaseIn(BaseModel):
    case_id: str
    case_label: str
    sector: Sector
    origin_market: Optional[str] = None
    target_market: Optional[str] = None
    first_signal_date: Optional[str] = None
    mainstream_date: Optional[str] = None
    observed_delay_months: Optional[float] = None
    source_url: Optional[str] = None
    verified: bool = False
    date_confidence: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("*", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @model_validator(mode="after")
    def _verified_requires_proof(self):
        # RG10 : un cas n'est verifie que s'il a une URL, deux dates et un delai
        if self.verified and not (self.source_url and self.first_signal_date and self.mainstream_date
                                  and self.observed_delay_months is not None):
            self.verified = False
        return self


class KnownEventIn(BaseModel):
    trend_id: str
    market: Optional[str] = None
    event_date: date
    event_label: str
    source_url: Optional[str] = None


class CalendarEventIn(BaseModel):
    """RG23a : evenement calendaire. Une date manquante (TODO) n'est jamais chargee."""
    date_start: date
    date_end: date
    name: str
    market: str = Field(min_length=2, max_length=2)
    kind: str
    source_url: Optional[str] = None


class CsvImportRow(BaseModel):
    """B5 : ligne d'import CSV client (retail_ecommerce). Toute ligne invalide est rejetee."""
    model_config = ConfigDict(extra="forbid")
    concept_id: str = Field(min_length=1)
    market: str = Field(pattern=r"^[A-Z]{2}$")
    week: date
    metric: str
    value: float = Field(ge=0)
    source_name: str = Field(min_length=1, max_length=40)

    @field_validator("week")
    @classmethod
    def _monday(cls, v):
        if v.weekday() != 0:
            raise ValueError("week doit etre un lundi")
        return v

    @field_validator("metric")
    @classmethod
    def _metric(cls, v):
        if v not in ("units", "availability_pct", "rank"):
            raise ValueError("metric doit etre units, availability_pct ou rank")
        return v

    @model_validator(mode="after")
    def _bounds(self):
        if self.metric == "availability_pct" and self.value > 100:
            raise ValueError("availability_pct doit etre entre 0 et 100")
        if self.metric == "rank" and (self.value < 1 or self.value != int(self.value)):
            raise ValueError("rank doit etre un entier superieur ou egal a 1")
        return self


# ---------------------------------------------------------------------------
# Collecte et signaux
# ---------------------------------------------------------------------------


class CollectResult(BaseModel):
    """Structure standard retournee par tout collecteur (JSON-serialisable)."""
    source_name: str
    source_type: SourceType
    trend_id: str
    market: str
    collected_at: datetime
    status: SourceStatus
    error: Optional[str] = None
    source_url: Optional[str] = None
    request_count: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class SignalData(BaseModel):
    """Champs communs a tous les signaux."""
    model_config = ConfigDict(extra="forbid")
    data_origin: DataOrigin = DataOrigin.reel
    proxy: Optional[bool] = None
    categories: Optional[list[str]] = None
    # B6 proxy de distribution Open Food Facts et Open Beauty Facts : pays et magasins distincts
    distribution_breadth: Optional[dict[str, int]] = None
    extra: Optional[dict[str, Any]] = None


class FoodSignalData(SignalData):
    menu_penetration: Optional[float] = None
    menu_channel: Optional[str] = None
    product_launch_count: Optional[int] = None
    launch_brand_tier: Optional[str] = None
    ingredient_price_index: Optional[float] = None
    supply_tension_flag: Optional[bool] = None
    claim_type: Optional[str] = None
    regulatory_status: Optional[str] = None


class FragranceSignalData(SignalData):
    olfactive_family: Optional[str] = None
    olfactive_note: Optional[str] = None
    note_position: Optional[str] = None
    launch_count_fine: Optional[int] = None
    launch_count_mass: Optional[int] = None
    launch_segment: Optional[str] = None
    press_mention_count: Optional[int] = None
    ingredient_origin: Optional[str] = None
    raw_material_price_index: Optional[float] = None


class BeautySignalData(SignalData):
    active_ingredient: Optional[str] = None
    inci_name: Optional[str] = None
    routine_context: Optional[str] = None
    benefit_claim: Optional[str] = None
    sku_count: Optional[int] = None
    launch_brand_tier: Optional[str] = None
    review_volume: Optional[int] = None
    review_sentiment: Optional[float] = None
    claim_regulatory_status: Optional[str] = None
    origin_market: Optional[str] = None


SECTOR_SIGNAL_SCHEMAS = {
    Sector.food_beverage: FoodSignalData,
    Sector.fragrance: FragranceSignalData,
    Sector.beauty: BeautySignalData,
}


class WeeklySignalIn(BaseModel):
    """Signal normalise : une ligne par tendance x marche x semaine x source (RG01)."""
    observation_id: int
    trend_id: str
    market: str = Field(min_length=2, max_length=2)
    week: date
    source_type: SourceType
    source_name: str
    source_url: Optional[str] = None
    signal_volume: float = Field(ge=0)
    event_linked: bool = False
    event_label: Optional[str] = None
    sector_data: dict[str, Any] = Field(default_factory=dict)
    # RG01 : hebdomadaire par defaut, mensuelle pour OpenAlex (ramenee au premier lundi du mois)
    granularity: str = "hebdomadaire"
    # B4 ton moyen GDELT, "ton de la presse", jamais utilise dans un score
    tone_mean: Optional[float] = None

    @field_validator("week")
    @classmethod
    def _monday(cls, v):
        if v.weekday() != 0:
            raise ValueError("week doit etre un lundi")
        return v


class Criterion(BaseModel):
    value: Optional[float] = None
    weight: float
    available: bool
    source: Optional[str] = None
    detail: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Sorties LLM (RG15)
# ---------------------------------------------------------------------------


class PitchContent(BaseModel):
    """Who to call, why now, what to say. Aucun champ de score."""
    model_config = ConfigDict(extra="forbid")
    who_to_call: str = Field(min_length=3, max_length=600)
    why_now: str = Field(min_length=3, max_length=900)
    what_to_say: str = Field(min_length=3, max_length=1200)
    next_step: str = Field(min_length=3, max_length=400)
    cited_evidence_ids: list[str] = Field(default_factory=list)


class TrendSummaryContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=3, max_length=900)
    durability_explanation: str = Field(min_length=3, max_length=900)
    cited_evidence_ids: list[str] = Field(default_factory=list)


class NarrativeOut(BaseModel):
    origin: Origin
    model: Optional[str] = None
    content: dict[str, Any]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TrendOut(ORMModel):
    id: str
    label: str
    sector: Sector
    category: str
    query_terms: dict[str, Any]
    profile: dict[str, Any]
    first_seen_date: Optional[date] = None
    first_seen_market: Optional[str] = None


class TrendScoreOut(ORMModel):
    id: int
    run_id: int
    trend_id: str
    market: Optional[str] = None
    as_of_week: date
    durability: Optional[float] = None
    confidence: Optional[float] = None
    classification: Optional[str] = None
    criteria: dict[str, Any]
    missing_criteria: list[str]
    observation_ids: list[int]
    scoring_version: str
    score_low: Optional[float] = None
    score_high: Optional[float] = None
    class_uncertain: Optional[bool] = None
    flags: Optional[list[str]] = None


class TrendListItem(BaseModel):
    trend: TrendOut
    latest_score: Optional[TrendScoreOut] = None


class WeeklyPoint(BaseModel):
    week: date
    market: str
    source_type: SourceType
    source_name: str
    signal_volume: float
    event_linked: bool
    data_origin: Optional[str] = None


class ComparableCaseOut(ORMModel):
    case_id: str
    case_label: str
    sector: Sector
    origin_market: Optional[str] = None
    target_market: Optional[str] = None
    first_signal_date: Optional[str] = None
    mainstream_date: Optional[str] = None
    observed_delay_months: Optional[float] = None
    source_url: Optional[str] = None
    verified: bool
    date_confidence: Optional[str] = None
    notes: Optional[str] = None


class TimingOut(ORMModel):
    id: int
    run_id: int
    trend_id: str
    market: str
    client_id: Optional[str] = None
    maturity_stage: Optional[MaturityStage] = None
    local_growth_rate: Optional[float] = None
    competitor_launch_count: Optional[int] = None
    competitor_first_launch_date: Optional[date] = None
    client_cycle_fit: Optional[float] = None
    channel_stage: Optional[str] = None
    analog_delay_low: Optional[float] = None
    analog_delay_high: Optional[float] = None
    analog_case_count: int
    analog_case_ids: list[str]
    confidence_level: ConfidenceLevel
    timing_score: Optional[float] = None
    criteria: dict[str, Any] = Field(default_factory=dict)
    window_message: Optional[str] = None


class TimingWithCases(BaseModel):
    estimate: TimingOut
    comparable_cases: list[ComparableCaseOut]


class NarrativeRecordOut(ORMModel):
    id: int
    entity_type: str
    entity_id: str
    origin: Origin
    model: Optional[str] = None
    content: dict[str, Any]
    created_at: datetime


class TrendDetailOut(BaseModel):
    trend: TrendOut
    score_history: list[TrendScoreOut]
    weekly_signals: list[WeeklyPoint]
    timing: list[TimingWithCases]
    narrative: Optional[NarrativeRecordOut] = None
    data_origins: dict[str, int]


class ClientOut(ORMModel):
    id: str
    label: str
    sector: Sector
    brand_positioning: Optional[str] = None
    price_tier: Optional[str] = None
    brand_values: list[str]
    target_segments: list[str]
    target_age_range: Optional[list[int]] = None
    product_categories: list[str]
    recent_launches: list[dict[str, Any]]
    active_markets: list[str]
    account_size_tier: Optional[str] = None
    strategic_flag: Optional[bool] = None
    innovation_cycle_months: Optional[int] = None
    innovation_cycle_note: str = "Paramètre réglable, repère 6 à 18 mois"
    next_launch_window: Optional[dict[str, Any]] = None
    relationship_status: Optional[str] = None
    client_data_source: ClientDataSource


class MatchOut(ORMModel):
    id: int
    run_id: int
    trend_id: str
    client_id: str
    market: Optional[str] = None
    fit_score: Optional[float] = None
    fit_level: Optional[str] = None
    fit_criteria: dict[str, Any]
    timing_estimate_id: Optional[int] = None
    durability_score: Optional[float] = None
    timing_score: Optional[float] = None
    opportunity_score: Optional[float] = None
    confidence_score: Optional[float] = None
    level: Optional[str] = None
    missing_inputs: list[str] = Field(default_factory=list)
    # RG30 : libelle distinct, donnees client reelles ou simulees jamais melangees sans libelle
    client_data_source: Optional[str] = None


class AlertOut(ORMModel):
    id: int
    match_id: int
    run_id: int
    trend_id: str
    client_id: str
    market: Optional[str] = None
    status: AlertStatus
    conditions: dict[str, Any]
    created_at: datetime


class RunCreate(BaseModel):
    requested_by: str = "api"
    sources: Optional[list[str]] = None
    trend_ids: Optional[list[str]] = None
    as_of_date: Optional[date] = None


class RunOut(ORMModel):
    id: int
    status: RunStatus
    requested_by: Optional[str] = None
    scoring_version: str
    params: dict[str, Any]
    source_status: dict[str, Any]
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None


class DashboardKpis(BaseModel):
    trends_tracked: int
    trends_validated_or_better: int
    active_alerts: int
    watchlist: int
    mean_durability: Optional[float] = None
    last_run_id: Optional[int] = None
    last_run_status: Optional[RunStatus] = None
    last_run_finished_at: Optional[datetime] = None


class DashboardOut(BaseModel):
    kpis: DashboardKpis
    top_trends: list[TrendListItem]
    alerts: list[AlertOut]
    source_status: dict[str, Any]


class PitchOut(BaseModel):
    trend_id: str
    client_id: str
    market: Optional[str] = None
    match: MatchOut
    narrative: NarrativeOut
    evidence_ids: list[str]


class HealthOut(BaseModel):
    status: str
    database: bool
    llm: dict[str, Any]
    enabled_sources: list[str]
    scoring_version: str



# ---------------------------------------------------------------------------
# Phase D : recommandations (RG31 a RG35)
# ---------------------------------------------------------------------------


class Bucket(str, Enum):
    a_proposer = "a_proposer"
    a_surveiller = "a_surveiller"
    ecartees = "ecartees"
    sorti = "sorti"


class WhyItem(BaseModel):
    """Un critere du Client Fit et ses points (valeur x poids)."""
    criterion: str
    value: Optional[float] = None
    weight: float
    points: Optional[float] = None


class BasedOnItem(BaseModel):
    """RG34 : justification tiree de la base, avec un evidence_id existant."""
    source_type: str
    summary: str
    evidence_id: str


class RecoScores(BaseModel):
    durability: Optional[float] = None
    fit: Optional[float] = None
    timing: Optional[float] = None
    opportunity: Optional[float] = None
    confidence: Optional[float] = None
    score_low: Optional[float] = None
    score_high: Optional[float] = None


class Recommendation(BaseModel):
    trend_id: str
    trend_label: Optional[str] = None
    market: str
    client_id: str
    client_data_source: Optional[str] = None
    bucket: Bucket
    rank: int
    why_for_client: list[WhyItem] = Field(default_factory=list)
    based_on: list[BasedOnItem] = Field(default_factory=list)
    analogs: Optional[dict[str, Any]] = None
    timing_window: Optional[dict[str, Any]] = None
    scores: RecoScores = Field(default_factory=RecoScores)
    caveats: list[str] = Field(default_factory=list)
    # RG35 : jamais vide
    counter_evidence: list[str] = Field(min_length=1)
    missing_condition: Optional[str] = None
    reason: Optional[str] = None
    change_vs_last_run: str
    suggested_action: str
    origin: Origin


class RecommendationsOut(BaseModel):
    client_id: str
    client_data_source: Optional[str] = None
    run_id: Optional[int] = None
    market: Optional[str] = None
    a_proposer: list[Recommendation] = Field(default_factory=list)
    a_surveiller: list[Recommendation] = Field(default_factory=list)
    ecartees: list[Recommendation] = Field(default_factory=list)
    sorties: list[dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None


class RecommendationRecordOut(ORMModel):
    id: int
    run_id: int
    client_id: str
    trend_id: str
    market: str
    bucket: str
    rank: int
    payload: dict[str, Any]
    created_at: datetime


class ContributionItem(BaseModel):
    criterion: str
    weight: Optional[float] = None
    value: Optional[float] = None
    available: bool = True
    points: float


class ExplainOut(BaseModel):
    """RG31 : la somme des points egale le score."""
    trend_id: str
    market: Optional[str] = None
    scoring_version: str
    score: Optional[float] = None
    classification: Optional[str] = None
    contributions: list[ContributionItem]
    points_total: Optional[float] = None
    indicators_v2: dict[str, Any] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    interval: dict[str, Optional[float]] = Field(default_factory=dict)
    analogs: Optional[dict[str, Any]] = None
    missing_criteria: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase E : agent en lecture seule (RG17, RG18)
# ---------------------------------------------------------------------------

AGENT_TOOLS = ["get_dashboard", "list_trends", "get_trend", "get_score_history", "get_timing_window",
               "get_matches", "get_alerts", "compare_trends", "delta_between_runs", "get_evidence",
               "get_explanation", "get_analogs", "get_propagation", "get_recommendations"]
ToolName = Enum("ToolName", {t: t for t in AGENT_TOOLS}, type=str)


class ToolCall(BaseModel):
    """Choix d'outil propose par le LLM, valide avant execution. Un outil inconnu est refuse."""
    model_config = ConfigDict(extra="forbid")
    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)


class AgentContext(BaseModel):
    trend_id: Optional[str] = None
    client_id: Optional[str] = None
    market: Optional[str] = Field(default=None, min_length=2, max_length=2)


class AgentChatIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=2000)
    context: Optional[AgentContext] = None


class AgentAnswer(BaseModel):
    answer: str
    origin: Origin
    tools_used: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentChatOut(AgentAnswer):
    session_id: str


class SynthesisKind(str, Enum):
    trend_brief = "trend_brief"
    client_brief = "client_brief"
    weekly_digest = "weekly_digest"
    dashboard_summary = "dashboard_summary"


class SynthesisIn(BaseModel):
    kind: SynthesisKind
    trend_id: Optional[str] = None
    client_id: Optional[str] = None
    market: Optional[str] = Field(default=None, min_length=2, max_length=2)


class SynthesisOut(AgentAnswer):
    kind: SynthesisKind
    narrative_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Phase G : fonctionnalites commerciales (RG20, RG28, RG30, RG32)
# ---------------------------------------------------------------------------


class FeedbackStatus(str, Enum):
    contacted = "contacted"
    won = "won"
    lost = "lost"


class FeedbackIn(BaseModel):
    """RG20 : retour commercial. Stocke, jamais utilise pour modifier les poids."""
    match_id: int
    status: FeedbackStatus
    note: Optional[str] = Field(default=None, max_length=1000)


class FeedbackOut(ORMModel):
    id: int
    match_id: int
    status: FeedbackStatus
    note: Optional[str] = None
    created_at: datetime


class WhatIfIn(BaseModel):
    """Simulation sans persistance : client, marche ou cycle d'innovation modifies."""
    trend_id: str
    client_id: str
    market: Optional[str] = Field(default=None, min_length=2, max_length=2)
    innovation_cycle_months: Optional[int] = Field(default=None, ge=1, le=60)


class ImportOut(BaseModel):
    kind: str
    accepted: int
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    client_data_source: str = "reel"
    file: Optional[str] = None


class TrendCandidateOut(ORMModel):
    id: int
    ngram: str
    sector: Optional[str] = None
    group_label: Optional[str] = None
    recent_count: int
    baseline_count: int
    rise_ratio: Optional[float] = None
    sources: list[str] = Field(default_factory=list)
    status: str
    grouping: str
    created_at: datetime


# ===========================================================================
# Section : ex tables.py
# Modele de donnees SQLAlchemy 2.
#
# Les tables d'historique (observations, signaux, scores, timing, matches,
# alertes, narratives) sont append-only (RG14) : le code n'y fait que des INSERT.
# Colonnes JSON en JSONB sous PostgreSQL et en JSON sous SQLite (tests offline).
# ===========================================================================

JSONType = JSON().with_variant(JSONB, "postgresql")


def utcnow():
    return datetime.now(timezone.utc)


def _enum(enum_cls):
    # Enum stocke en VARCHAR avec contrainte, portable SQLite et PostgreSQL
    return SAEnum(enum_cls, native_enum=False, length=40, values_callable=lambda e: [m.value for m in e])


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Referentiels
# ---------------------------------------------------------------------------


class Trend(Base):
    __tablename__ = "trends"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    sector: Mapped[Sector] = mapped_column(_enum(Sector))
    category: Mapped[str] = mapped_column(String(80))
    query_terms: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    first_seen_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    first_seen_market: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    to_confirm: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    sector: Mapped[Sector] = mapped_column(_enum(Sector))
    brand_positioning: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    price_tier: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    brand_values: Mapped[list] = mapped_column(JSONType, default=list)
    target_segments: Mapped[list] = mapped_column(JSONType, default=list)
    target_age_range: Mapped[Optional[list]] = mapped_column(JSONType, nullable=True)
    product_categories: Mapped[list] = mapped_column(JSONType, default=list)
    recent_launches: Mapped[list] = mapped_column(JSONType, default=list)
    active_markets: Mapped[list] = mapped_column(JSONType, default=list)
    account_size_tier: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    strategic_flag: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    innovation_cycle_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    next_launch_window: Mapped[Optional[dict]] = mapped_column(JSONType, nullable=True)
    relationship_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    client_data_source: Mapped[ClientDataSource] = mapped_column(_enum(ClientDataSource))


class ComparableCase(Base):
    __tablename__ = "comparable_cases"
    case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_label: Mapped[str] = mapped_column(String(200))
    sector: Mapped[Sector] = mapped_column(_enum(Sector))
    origin_market: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    target_market: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    # Dates en texte pour garder la precision reelle (AAAA, AAAA-MM ou AAAA-MM-JJ)
    first_signal_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    mainstream_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    observed_delay_months: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    date_confidence: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class KnownEvent(Base):
    __tablename__ = "known_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trend_id: Mapped[str] = mapped_column(ForeignKey("trends.id"))
    market: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    event_date: Mapped[date] = mapped_column(Date)
    event_label: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint("trend_id", "market", "event_date", "event_label"),)


class CalendarEvent(Base):
    """RG23a : jours feries (bibliotheque holidays) et evenements mobiles sources."""
    __tablename__ = "calendar_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date_start: Mapped[date] = mapped_column(Date)
    date_end: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(200))
    market: Mapped[str] = mapped_column(String(2))
    kind: Mapped[str] = mapped_column(String(40))
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint("date_start", "name", "market", name="uq_calendar_event"),)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus), default=RunStatus.QUEUED, index=True)
    requested_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    scoring_version: Mapped[str] = mapped_column(String(20))
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source_status: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Historique append-only (RG14)
# ---------------------------------------------------------------------------


class SourceObservation(Base):
    __tablename__ = "source_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    trend_id: Mapped[str] = mapped_column(ForeignKey("trends.id"))
    source_type: Mapped[SourceType] = mapped_column(_enum(SourceType))
    source_name: Mapped[str] = mapped_column(String(60))
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    market: Mapped[str] = mapped_column(String(2))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[SourceStatus] = mapped_column(_enum(SourceStatus))
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class WeeklySignal(Base):
    __tablename__ = "weekly_signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("source_observations.id"))
    trend_id: Mapped[str] = mapped_column(ForeignKey("trends.id"))
    market: Mapped[str] = mapped_column(String(2))
    week: Mapped[date] = mapped_column(Date)
    source_type: Mapped[SourceType] = mapped_column(_enum(SourceType))
    source_name: Mapped[str] = mapped_column(String(60))
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    signal_volume: Mapped[float] = mapped_column(Float)
    event_linked: Mapped[bool] = mapped_column(Boolean, default=False)
    event_label: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    sector_data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    granularity: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="hebdomadaire")
    tone_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    __table_args__ = (
        UniqueConstraint("trend_id", "market", "week", "source_name", name="uq_weekly_signal"),
        Index("ix_weekly_trend_market_week", "trend_id", "market", "week"),
    )


class TrendScore(Base):
    __tablename__ = "trend_scores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    trend_id: Mapped[str] = mapped_column(ForeignKey("trends.id"))
    market: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    as_of_week: Mapped[date] = mapped_column(Date)
    durability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    classification: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    missing_criteria: Mapped[list] = mapped_column(JSONType, default=list)
    observation_ids: Mapped[list] = mapped_column(JSONType, default=list)
    scoring_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # RG25 intervalle bootstrap (v2), RG24 flags (seasonality_unknown, short_history, peak_recent, class_uncertain)
    score_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    class_uncertain: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    flags: Mapped[Optional[list]] = mapped_column(JSONType, nullable=True)
    __table_args__ = (Index("ix_scores_trend_market_week", "trend_id", "market", "as_of_week"),)


class TimingEstimate(Base):
    __tablename__ = "timing_estimates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    trend_id: Mapped[str] = mapped_column(ForeignKey("trends.id"))
    market: Mapped[str] = mapped_column(String(2))
    # Ajout : null = estimation marche, sinon estimation avec le cycle de ce client
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"), nullable=True)
    maturity_stage: Mapped[Optional[MaturityStage]] = mapped_column(_enum(MaturityStage), nullable=True)
    local_growth_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    competitor_launch_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competitor_first_launch_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    client_cycle_fit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    channel_stage: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    analog_delay_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    analog_delay_high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    analog_case_count: Mapped[int] = mapped_column(Integer, default=0)
    analog_case_ids: Mapped[list] = mapped_column(JSONType, default=list)
    confidence_level: Mapped[ConfidenceLevel] = mapped_column(_enum(ConfidenceLevel))
    timing_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    window_message: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    scoring_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # RG26 analogues DTW (distance, semaine alignee, phase) ou statut "non disponible"
    analogs: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType, nullable=True)


class RecommendationRecord(Base):
    """Recommandations par run, append-only (RG14)."""
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    trend_id: Mapped[str] = mapped_column(ForeignKey("trends.id"))
    market: Mapped[str] = mapped_column(String(2))
    bucket: Mapped[str] = mapped_column(String(20))
    rank: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatMessage(Base):
    """Memoire de l'agent : les 6 derniers echanges d'une session sont relus."""
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    origin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tools_used: Mapped[Optional[list]] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BacktestResult(Base):
    """Resultats de backtest (run.py backtest), lus par /api/trackrecord et le challenger. Append-only."""
    __tablename__ = "backtest_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch: Mapped[str] = mapped_column(String(40), index=True)
    term: Mapped[str] = mapped_column(String(120))
    sector: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    market: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    as_of: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    version: Mapped[str] = mapped_column(String(20))
    durability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    classification: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    score_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    features: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Feedback(Base):
    """RG20 : retours commerciaux, jamais utilises pour modifier les poids."""
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("client_matches.id"), index=True)
    status: Mapped[FeedbackStatus] = mapped_column(_enum(FeedbackStatus))
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrendCandidate(Base):
    """RG28 : candidats de concepts emergents, statut "a valider", jamais scores ni alertes."""
    __tablename__ = "trend_candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ngram: Mapped[str] = mapped_column(String(120))
    sector: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    group_label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    recent_count: Mapped[int] = mapped_column(Integer)
    baseline_count: Mapped[int] = mapped_column(Integer)
    rise_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sources: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="à valider")
    grouping: Mapped[str] = mapped_column(String(20), default="ngrammes")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    """RG32 : journal des envois webhook (ou simulations en dry_run)."""
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(40))
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CaseSeries(Base):
    """RG26 : historique Google Trends des cas comparables, append-only (RG14)."""
    __tablename__ = "case_series"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("comparable_cases.case_id"), index=True)
    market: Mapped[str] = mapped_column(String(2))
    week: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    source_name: Mapped[str] = mapped_column(String(60), default="google_trends")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("case_id", "market", "week", "source_name", name="uq_case_series"),)


class ClientMatch(Base):
    __tablename__ = "client_matches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    trend_id: Mapped[str] = mapped_column(ForeignKey("trends.id"))
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    market: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    fit_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fit_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    fit_criteria: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    timing_estimate_id: Mapped[Optional[int]] = mapped_column(ForeignKey("timing_estimates.id"), nullable=True)
    trend_score_id: Mapped[Optional[int]] = mapped_column(ForeignKey("trend_scores.id"), nullable=True)
    durability_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    timing_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    opportunity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    missing_inputs: Mapped[list] = mapped_column(JSONType, default=list)
    scoring_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("client_matches.id"), index=True)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[AlertStatus] = mapped_column(_enum(AlertStatus))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Narrative(Base):
    __tablename__ = "narratives"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(80))
    origin: Mapped[Origin] = mapped_column(_enum(Origin))
    model: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_narratives_entity", "entity_type", "entity_id"),)



# ===========================================================================
# Section : ex db.py
# Connexion a la base. Les tables sont creees par create_all au demarrage
# (pas d'Alembic, choix de la stack).
# ===========================================================================

def make_engine(url=None):
    url = url or config.DATABASE_URL
    if url.startswith("sqlite"):
        if url == "sqlite://" or ":memory:" in url:
            # Base en memoire partagee entre threads (tests)
            return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Path(url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


def init_db(engine):
    Base.metadata.create_all(engine)
    create_agent_views(engine)
    return engine


# RG17 : vues agregees lues par l'agent. Aucune ecriture, aucun SQL genere par le LLM.
AGENT_VIEWS = {
    "agent_v_trends": "SELECT id, label, sector, category, to_confirm FROM trends",
    "agent_v_clients": "SELECT id, label, sector, client_data_source, active_markets FROM clients",
    "agent_v_runs": "SELECT id, status, finished_at FROM runs",
    "agent_v_scores": ("SELECT id, run_id, trend_id, market, as_of_week, durability, confidence, classification, "
                       "scoring_version, score_low, score_high, class_uncertain, flags, missing_criteria, criteria "
                       "FROM trend_scores"),
    "agent_v_timing": ("SELECT id, run_id, trend_id, market, client_id, maturity_stage, local_growth_rate, "
                       "analog_delay_low, analog_delay_high, window_message, timing_score, analogs FROM timing_estimates"),
    "agent_v_matches": ("SELECT id, run_id, trend_id, client_id, market, fit_score, fit_level, durability_score, "
                        "timing_score, opportunity_score, confidence_score, level FROM client_matches"),
    "agent_v_alerts": ("SELECT a.id, a.status, m.run_id, m.trend_id, m.client_id, m.market, m.opportunity_score "
                       "FROM alerts a JOIN client_matches m ON a.match_id = m.id"),
    "agent_v_recommendations": "SELECT id, run_id, client_id, trend_id, market, bucket, rank, payload FROM recommendations",
    "agent_v_weekly": ("SELECT trend_id, market, week, source_type, COUNT(*) AS n_sources, SUM(signal_volume) AS volume, "
                       "AVG(tone_mean) AS tone_mean FROM weekly_signals GROUP BY trend_id, market, week, source_type"),
    "agent_v_evidence": ("SELECT id, trend_id, market, week, source_type, source_name, signal_volume, tone_mean "
                         "FROM weekly_signals"),
    "agent_v_titles": ("SELECT id, trend_id, source_name, collected_at, raw_payload FROM source_observations "
                       "WHERE status = 'success' AND source_name IN ('rss_trade_press', 'reddit')"),
}


def drop_agent_views(engine):
    """Supprime les vues agent_v_* (nettoyage des tests d'integration avant drop_all)."""
    with engine.begin() as conn:
        for name in AGENT_VIEWS:
            conn.execute(text("DROP VIEW IF EXISTS " + name))


def create_agent_views(engine):
    """Cree les vues agent_v_*. Sous PostgreSQL, cree aussi le role agent_ro si AGENT_DB_PASSWORD est fourni."""
    is_pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        for name, sql in AGENT_VIEWS.items():
            if is_pg:
                conn.execute(text("DROP VIEW IF EXISTS " + name))
                conn.execute(text("CREATE VIEW " + name + " AS " + sql))
            else:
                conn.execute(text("CREATE VIEW IF NOT EXISTS " + name + " AS " + sql))
        if is_pg and config.AGENT_DB_PASSWORD:
            exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro'")).scalar()
            if not exists:
                # Mot de passe lu dans l'environnement, jamais dans le code
                pw = config.AGENT_DB_PASSWORD.replace("'", "''")
                conn.execute(text("CREATE ROLE agent_ro LOGIN PASSWORD '" + pw + "'"))
            conn.execute(text("GRANT USAGE ON SCHEMA public TO agent_ro"))
            conn.execute(text("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM agent_ro"))
            conn.execute(text("GRANT SELECT ON " + ", ".join(AGENT_VIEWS) + " TO agent_ro"))


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def upgrade_db(engine):
    """
    Commande db-upgrade, idempotente. Cree les tables manquantes puis ajoute les
    colonnes manquantes. Ne supprime jamais rien (RG14). Sous PostgreSQL :
    ALTER TABLE ... ADD COLUMN IF NOT EXISTS. Sous SQLite (tests), IF NOT EXISTS
    n'existe pas, la colonne n'est ajoutee que si l'inspection ne la trouve pas.
    Les colonnes ajoutees sont nullables car les lignes existantes n'ont pas de valeur.
    """
    Base.metadata.create_all(engine)
    is_pg = engine.dialect.name == "postgresql"
    added = []
    existing_by_table = {t: {c["name"] for c in inspect(engine).get_columns(t)}
                         for t in inspect(engine).get_table_names()}
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = existing_by_table.get(table.name, set())
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                guard = "IF NOT EXISTS " if is_pg else ""
                conn.execute(text("ALTER TABLE " + table.name + " ADD COLUMN " + guard + col.name + " " + ddl_type))
                added.append(table.name + "." + col.name)
    create_agent_views(engine)
    return added

