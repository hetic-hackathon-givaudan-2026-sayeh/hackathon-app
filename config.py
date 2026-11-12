"""
Configuration centrale de TrendPulse.

Toutes les constantes metier sont ici. Les valeurs marquees "A VALIDER" sont
des choix par defaut proposes par l'equipe Data, a confirmer avec les Product
Owners ou calibrer (python run.py backtest). Les modules lisent config.X au moment de l'appel pour que les
tests puissent modifier une valeur.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SEED_DIR = DATA_DIR / "seed"


def _env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name, default):
    value = os.getenv(name)
    if not value:
        return list(default)
    return [v.strip() for v in value.split(",") if v.strip()]


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///" + str(DATA_DIR / "trendpulse.db"))

# Aucun secret en dur. Sans API_KEY, les routes protegees refusent tout.
API_KEY = os.getenv("API_KEY", "")
# RG21 : User-Agent generique du projet, sans e-mail personnel. PROJECT_URL est
# l'adresse publique du projet (depot ou page), jamais une adresse personnelle.
PROJECT_URL = os.getenv("PROJECT_URL", "")


def user_agent():
    """User-Agent generique des sources publiques (RG21)."""
    contact = ", +" + PROJECT_URL if PROJECT_URL else ""
    return "TrendPulse/1.0 (projet etudiant HETIC, Givaudan AI Challenge" + contact + ")"


SCORING_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Sources (RG16)
# ---------------------------------------------------------------------------

# Reddit reste desactive par defaut (RG16, RG21). Les sources a cle (OpenAlex)
# repondent "indisponible" tant que la cle est absente, jamais zero (RG05).
DEFAULT_ENABLED_SOURCES = [
    "google_trends",
    "wikipedia_pageviews",
    "gdelt",
    "open_food_facts",
    "open_beauty_facts",
    "fragrance_seed",
    "openalex",
    "rss_trade_press",
    "csv_import",
]
ENABLED_SOURCES = _env_list("ENABLED_SOURCES", DEFAULT_ENABLED_SOURCES)

# Marches suivis (RG01). ZZ = code ISO reserve "inconnu" pour les sources sans pays.
MARKETS = _env_list("MARKETS", ["FR", "US", "GB", "DE", "JP", "KR"])
GLOBAL_MARKET = "ZZ"

# Profondeur de collecte
COLLECT_LOOKBACK_WEEKS = 104

HTTP = {
    "timeout_seconds": 20,
    "retries": 3,
    "backoff_seconds": 2,
    "max_retry_after_seconds": 60,
}

GOOGLE_TRENDS = {
    "hl": "en-US",
    "tz": 360,
    # Categories Google Trends par secteur. 71 = Food & Drink.
    # 44 et 242 sont a verifier dans l'interface Google Trends (A VALIDER).
    "category_by_sector": {"food_beverage": 71, "beauty": 44, "fragrance": 242},
    # Mot-cle d'ancrage fixe ajoute a chaque lot (A VALIDER)
    "anchor_by_sector": {"food_beverage": "coffee", "beauty": "moisturizer", "fragrance": "perfume"},
    "anchor_reference": 100.0,
    # Sous ce maximum brut face a l'ancre, le terme est redemande seul (A VALIDER)
    "min_raw_resolution": 20,
    "max_terms_per_batch": 4,
    "pause_seconds": 5,
    "cache_dir": str(DATA_DIR / "cache" / "google_trends"),
    "cache_ttl_hours": 24,
}

WIKI_PAGEVIEWS = {
    "url": "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article",
    # Projet de langue vers marche. Les langues multi-pays vont en ZZ.
    "project_market": {"ja.wikipedia": "JP", "ko.wikipedia": "KR", "fr.wikipedia": "FR", "de.wikipedia": "DE"},
    "pause_seconds": 1,
}

REDDIT = {
    "base_url": "https://www.reddit.com",
    "subreddits_by_sector": {
        "food_beverage": ["food", "Cooking", "AskCulinary", "recipes", "tea", "Baking", "KoreanFood", "JapaneseFood"],
        "beauty": ["SkincareAddiction", "AsianBeauty", "30PlusSkinCare", "KoreanBeauty"],
        "fragrance": ["fragrance", "Perfumes", "FemFragLab"],
    },
    "limit": 100,
    "max_pages": 3,
    "pause_seconds": 2,
}

OPEN_FOOD_FACTS = {
    "search_url": "https://world.openfoodfacts.org/cgi/search.pl",
    "page_size": 50,
    "max_pages": 2,
    "pause_seconds": 1,
}

OPEN_BEAUTY_FACTS = {
    "search_url": "https://world.openbeautyfacts.org/cgi/search.pl",
    "page_size": 50,
    "max_pages": 2,
    "pause_seconds": 1,
    # Filtre de categorie par secteur. en:perfumes verifie le 2026-11-12 (455 produits)
    "category_by_sector": {"fragrance": "en:perfumes"},
}

# B1 OpenAlex (recherche_academique). Cle lue dans l'environnement.
OPENALEX = {
    "url": "https://api.openalex.org/works",
    "api_key": os.getenv("OPENALEX_API_KEY", ""),
    # Desactive pour Fragrance (consigne equipe)
    "sectors": ["food_beverage", "beauty"],
    "lookback_months": 24,
    "pause_seconds": 1,
}

# B4 GDELT DOC 2.0 (presse_generale). Une requete toutes les 5 secondes au plus.
# TimelineVolRaw et TimelineTone verifies en direct le 2026-11-12 (resolution jour).
GDELT = {
    "url": "https://api.gdeltproject.org/api/v2/doc/doc",
    "volume_mode": "TimelineVolRaw",
    "tone_mode": "TimelineTone",
    "tone_enabled": True,
    # 6 secondes ont encore donne un 429 en direct le 2026-11-12, d'ou 10
    "pause_seconds": 10,
}
# Libelle impose pour le ton GDELT : jamais un avis de consommateurs, jamais dans un score
TONE_LABEL = "ton de la presse"

# B3 Presse professionnelle RSS. Chaque flux est enabled=false tant que l'equipe
# n'a pas verifie ses conditions d'utilisation.
RSS_FEEDS_PATH = str(SEED_DIR / "rss_feeds.json")
RSS = {"pause_seconds": 2, "title_max_chars": 200}

# B5 Import CSV client (retail_ecommerce), RG22 : aucune collecte sur sites marchands
CSV_IMPORT_DIR = str(DATA_DIR / "import")
CSV_IMPORT_METRICS = ["units", "availability_pct", "rank"]

# RG23a Calendrier : jours feries par pays (bibliotheque holidays) et evenements mobiles
CALENDAR_EVENTS_PATH = str(SEED_DIR / "calendar_events.csv")
CALENDAR = {"years_back": 3, "years_forward": 1, "holidays_source_url": "https://pypi.org/project/holidays/"}
# RG23a Black Friday calcule par regle (lendemain du quatrieme jeudi de novembre)
BLACK_FRIDAY_MARKETS = ["US", "FR", "GB", "DE"]

# Proxy marque referente : produit distribue dans au moins N pays (A VALIDER)
REFERENCE_BRAND_MIN_COUNTRIES = 3

# Pays Open Food Facts et Open Beauty Facts vers code ISO
FACTS_COUNTRY_TO_ISO = {
    "france": "FR", "united-states": "US", "united-kingdom": "GB", "germany": "DE",
    "japan": "JP", "south-korea": "KR", "spain": "ES", "italy": "IT", "belgium": "BE",
    "switzerland": "CH", "canada": "CA", "netherlands": "NL", "australia": "AU",
    "mexico": "MX", "united-arab-emirates": "AE", "austria": "AT", "portugal": "PT",
}

FRAGRANCE_SEED_PATH = str(SEED_DIR / "fragrance_seed.json")

# ---------------------------------------------------------------------------
# RG03 Diversite
# ---------------------------------------------------------------------------

DIVERSITY_SOURCE_TYPES = [
    "social", "presse_generale", "presse_professionnelle", "recherche_academique", "brevet",
    "lancement_produit", "retail_ecommerce", "salon_professionnel", "institutionnel_reglementaire",
]
# Sources dont les marches sont de vrais pays (diffusion RG04). Les imports CSV
# client (retail_ecommerce) sont aussi geolocalises.
GEO_SOURCES = ["google_trends", "open_food_facts", "open_beauty_facts", "fragrance_seed"]
# Sources continues utilisees pour la persistance et la regularite
PERSISTENCE_SOURCES = ["google_trends", "wikipedia_pageviews"]

# ---------------------------------------------------------------------------
# RG04 Trend Durability Score
# ---------------------------------------------------------------------------

DURABILITY_WEIGHTS = {
    "persistance": 30,
    "regularite": 20,
    "diversite": 20,
    "diffusion": 15,
    "adoption_precoce": 10,
}

DURABILITY = {
    "window_weeks": 52,
    # Une semaine compte si l'activite normalisee (0 a 100) depasse ce seuil (A VALIDER)
    "persistence_threshold": 25.0,
    "persistence_target_weeks": 26,
    "regularity_min_weeks": 12,
    "regularity_max_weeks": 36,
    # Pente relative (% par semaine) donnant la note maximale (A VALIDER)
    "regularity_slope_scale_pct": 2.0,
    "volatility_factor": 0.5,
    "volatility_max_penalty": 0.5,
    "diversity_target_types": 4,
    "diffusion_target_markets": 5,
    "diffusion_target_categories": 5,
    "early_adoption_types": ["recherche_academique", "brevet"],
    "reference_brand_tiers": ["reference", "reference_proxy"],
}

DURABILITY_CLASSES = [
    (90, "Accelerating Trend"),
    (75, "Validated Trend"),
    (60, "Emerging Trend"),
    (40, "Weak Signal"),
    (0, "Noise/Buzz"),
]

# ---------------------------------------------------------------------------
# RG06 Penalite evenementielle
# ---------------------------------------------------------------------------

EVENT_PENALTY = {
    "spike_k": 3.0,
    "retention_weeks": 4,
    "retention_max": 0.3,
    "event_match_days": 21,
    "min_weeks": 8,
    "with_known_event": 15,
    "without_known_event": 5,
}

# ---------------------------------------------------------------------------
# Phase C algorithmes v2 (RG23b a RG29). Valeurs par defaut proposees,
# A VALIDER avec les Product Owners. Les poids de la fiche ne changent pas.
# ---------------------------------------------------------------------------

SCORING_VERSION_V2 = "v2"
# Version utilisee par timing, matching, alertes et recommandations. La v1 reste
# calculee et stockee pour comparaison (RG14, append-only).
SCORING_PRIMARY = SCORING_VERSION_V2

SEASONAL_PERIOD = 52
MIN_WEEKS_SEASONAL = 104
# RG23b : STL n'est appliquee que si la saisonnalite est detectee : autocorrelation a
# 52 semaines de la serie detendancee (droite de Theil-Sen retiree) >= ce seuil. Avec seulement deux cycles,
# STL attribuerait sinon le bruit et les pics a la saison (A VALIDER).
SEASONAL_ACF_MIN = 0.5
# Plancher de la MAD dans le seuil de persistance, en points sur 0 a 100 (A VALIDER)
PERSISTENCE_MAD_FLOOR = 1.0
SMOOTH_WEEKS = 4
PEAK_Z = 3.0
RETENTION_LOW = 0.35
RETENTION_MID = 0.6
BOOTSTRAP_N = 200
BLOCK_LEN = 8
BOOTSTRAP_SEED = 20261112
DTW_WINDOW = 12
MIN_ANALOGS = 3

V2 = {
    "window_weeks": 52,
    "base_weeks": 26,
    "recent_weeks": 26,
    # Retention : semaines +4 a +12 apres le pic
    "retention_from": 4,
    "retention_to": 12,
    # CUSUM maison : seuils en ecarts robustes (A VALIDER)
    "cusum_k": 0.5,
    "cusum_h": 4.0,
    # Lag maximal de la correlation decalee (RG27)
    "max_lag_weeks": 12,
    "top_analogs": 3,
}

# RG29 Confidence v2 (somme 100). A VALIDER.
CONFIDENCE_V2_WEIGHTS = {
    "types_sources": 20,
    "profondeur": 15,
    "fraicheur": 10,
    "couverture_marche": 10,
    "analogues": 10,
    "source_client": 10,
    "criteres_calculables": 15,
    "precision_intervalle": 10,
}
# Points retires si la saisonnalite est inconnue ou l'historique court (A VALIDER)
CONFIDENCE_V2_FLAG_PENALTY = {"seasonality_unknown": 5, "short_history": 5}

# ---------------------------------------------------------------------------
# RG07 Client Fit Score
# ---------------------------------------------------------------------------

FIT_WEIGHTS = {
    "alignement_marque": 25,
    "cible": 20,
    "portefeuille": 20,
    "marche": 15,
    "potentiel": 10,
    "capacite_agir": 10,
}
FIT_LEVELS = [(80, "Priority"), (65, "Strong"), (50, "Potential"), (0, "Low")]

PRICE_TIERS = ["mass", "masstige", "premium", "luxury"]
ACCOUNT_SIZE_VALUES = {"small": 0.25, "medium": 0.5, "large": 0.75, "key": 1.0}
RELATIONSHIP_VALUES = {"key_account": 1.0, "active": 1.0, "prospect": 0.5, "dormant": 0.2}
# Repere pour innovation_cycle_months (RG08), parametre reglable par client
INNOVATION_CYCLE_RANGE_MONTHS = (6, 18)
NEXT_LAUNCH_HORIZON_MONTHS = 18

# ---------------------------------------------------------------------------
# RG09 Market Timing Score
# ---------------------------------------------------------------------------

TIMING_WEIGHTS = {"maturite": 40, "croissance_locale": 30, "concurrence": 20, "cycle_client": 10}

# Points par stade de maturite (A VALIDER)
MATURITY_POINTS = {"emergent": 1.0, "croissance": 0.75, "signal_faible": 0.5, "maturite": 0.25, "declin": 0.0}

TIMING = {
    "growth_weeks": 13,
    "min_weeks": 8,
    "emergent_growth_min": 30.0,
    "emergent_max_launches": 5,
    "croissance_growth_min": 10.0,
    "maturite_growth_min": -10.0,
    "growth_floor_pct": -20.0,
    "growth_cap_pct": 50.0,
    "competition_saturation": 20,
}

# ---------------------------------------------------------------------------
# RG10 Fenetre de tir
# ---------------------------------------------------------------------------

ANALOG = {"min_cases": 3, "high_confidence_cases": 6}
INSUFFICIENT_ANALOGS_MESSAGE = "Données comparables insuffisantes"

# ---------------------------------------------------------------------------
# RG11 Opportunity
# ---------------------------------------------------------------------------

OPPORTUNITY_WEIGHTS = {"durability": 0.45, "fit": 0.40, "timing": 0.15}
OPPORTUNITY_LEVELS = [(85, "High Priority"), (70, "Recommended"), (55, "Watchlist"), (0, "Low Priority")]

# ---------------------------------------------------------------------------
# RG12 Confidence Score (formule proposee, A VALIDER)
# ---------------------------------------------------------------------------

CONFIDENCE_WEIGHTS = {
    "types_sources": 20,
    "profondeur": 15,
    "fraicheur": 10,
    "couverture_marche": 10,
    "cas_comparables": 10,
    "source_client": 10,
    "criteres_calculables": 25,
}
CONFIDENCE = {
    "types_target": 4,
    "depth_target_weeks": 36,
    "fresh_full_days": 7,
    "fresh_zero_days": 60,
    "cases_target": 5,
    "client_source_values": {"reel": 1.0, "simule": 0.6, "absent": 0.0},
}

# ---------------------------------------------------------------------------
# RG13 Alertes
# ---------------------------------------------------------------------------

ALERT_THRESHOLDS = {"durability": 60, "fit": 65, "opportunity": 70, "confidence": 70}
LOW_CONFIDENCE_MESSAGE = "Opportunité détectée, preuves insuffisantes avant contact client."

# ---------------------------------------------------------------------------
# Phase D recommandations (RG33 a RG35). Valeurs proposees, A VALIDER.
# ---------------------------------------------------------------------------

# Libelles des 4 conditions RG13
CONDITION_LABELS = {"durability": "Durability", "fit": "Client Fit", "opportunity": "Opportunity",
                    "confidence": "Confidence"}
# "a surveiller" : une seule condition manque, avec un ecart inferieur a cette marge (points)
SURVEILLER_MARGIN = 10
# "ecartees" : hausse recente du signal dans le top N pour cent des tendances du run
POPULAR_TOP_PCT = 20
# Limites par client et par marche
RECO_LIMITS = {"a_proposer": 5, "a_surveiller": 5, "ecartees": 3}
# Variation d'opportunite (points) en dessous de laquelle une recommandation est "stable"
CHANGE_TOLERANCE = 2.0
# Fragrance : couverture faible sous ce nombre de types de sources actifs
FRAGRANCE_MIN_SOURCE_TYPES = 2
NO_COUNTER_EVIDENCE = "aucun signal contraire détecté sur les vérifications effectuées"

# ---------------------------------------------------------------------------
# RG15 LLM local
# ---------------------------------------------------------------------------

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:1.5b")
LLM_ENABLED = _env_bool("LLM_ENABLED", True)
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
LLM_EXTERNAL_TEXT_MAX_CHARS = 280

# RG17 agent en lecture seule. Modele separe. AGENT_DATABASE_URL pointe vers le
# role PostgreSQL agent_ro (SELECT sur les vues agent_v_*). Vide : meme base,
# mais l'agent ne lit toujours que les vues, par des requetes ecrites en code.
AGENT_LLM_MODEL = os.getenv("AGENT_LLM_MODEL", os.getenv("LLM_MODEL", "qwen2.5:1.5b"))
AGENT_DATABASE_URL = os.getenv("AGENT_DATABASE_URL", "")
AGENT_DB_PASSWORD = os.getenv("AGENT_DB_PASSWORD", "")
AGENT_MEMORY_EXCHANGES = 6
AGENT_MAX_ROWS = 20
LLM_MAX_EVIDENCE = 12
NARRATE_MAX_ITEMS = 10

# ---------------------------------------------------------------------------
# Phase G fonctionnalites commerciales (RG20, RG28, RG30, RG32)
# ---------------------------------------------------------------------------

# RG32 webhook compatible Slack. dry_run par defaut : rien n'est envoye, tout est journalise.
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_DRY_RUN = _env_bool("WEBHOOK_DRY_RUN", True)

# RG28 decouverte de candidats. Jamais scores ni alertes sans validation humaine (A VALIDER).
DISCOVER = {
    "recent_weeks": 4,
    "baseline_weeks": 8,
    "min_recent_count": 3,
    "min_rise_ratio": 2.0,
    "max_ngram": 3,
    "max_candidates": 20,
    # Requetes GDELT ArtList par secteur (titres d'articles, aucun texte complet stocke)
    "gdelt_queries": {"food_beverage": "(flavor OR flavour OR beverage OR snack)",
                      "beauty": "(skincare OR cosmetics OR serum)",
                      "fragrance": "(fragrance OR perfume)"},
    "gdelt_max_records": 250,
    "embed_model": os.getenv("EMBED_MODEL", "nomic-embed-text"),
    "embed_similarity": 0.85,
}
STOPWORDS = set("""a an and are as at be by for from has have in into is it its new of on or that the this to with
will was were after over how why what who your our their more most than up out not no but can
le la les un une des du de et en est pour par sur dans au aux avec ce cette ces qui que plus son sa ses
nouveau nouvelle nouveaux""".split())

# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

SCHEDULE = {
    "daily_sources": ["google_trends", "open_food_facts", "open_beauty_facts", "wikipedia_pageviews", "fragrance_seed",
                      "gdelt", "openalex", "rss_trade_press", "csv_import"],
    "daily_hour": 5,
    "reddit_every_hours": 6,
}
WORKER_POLL_SECONDS = 10
RUN_STALE_MINUTES = 180
