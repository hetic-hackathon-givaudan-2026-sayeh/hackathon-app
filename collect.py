"""
Collecte et normalisation : collecteurs independants (RG16) et passage en weekly_signals (RG01, RG05).
"""

import csv
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import requests
from pydantic import ValidationError

import config
from models import (
    CollectResult, CsvImportRow, DataOrigin, Sector, SECTOR_SIGNAL_SCHEMAS, SourceStatus, SourceType, WeeklySignalIn,
)


# ===========================================================================
# Section : ex collectors.py
# Collecteurs generiques et registre des sources.
#
# Interface Collector : name, source_type, sectors, collect(trend, market, since).
# Regles communes :
#   - sans etat entre deux appels, n'ecrit jamais en base
#   - retry avec backoff, timeout, pause entre requetes (limite de debit)
#   - retourne toujours la structure standard CollectResult, jamais d'exception
#   - chaque source est independante (RG16) et desactivable par ENABLED_SOURCES
#
# Chaque collect() deviendra une task Airflow sans reecriture.
# ===========================================================================

logger = logging.getLogger("trendpulse.collectors")

ALL_SECTORS = (Sector.food_beverage.value, Sector.beauty.value, Sector.fragrance.value)


# ---------------------------------------------------------------------------
# HTTP commun
# ---------------------------------------------------------------------------


class SourceBlocked(Exception):
    """La source refuse l'acces (403). Pas de nouvel essai."""


class SourceNotFound(Exception):
    """La ressource n'existe pas (404)."""


class SourceUnavailable(Exception):
    """Echec apres tous les essais (429, 5xx, timeout, reseau)."""


def _sleep(seconds):
    # Isole pour que les tests puissent supprimer les pauses
    if seconds > 0:
        time.sleep(seconds)


def _transport(url, params, headers, timeout):
    # Point unique d'acces reseau, remplace par des fixtures dans les tests
    return requests.get(url, params=params, headers=headers, timeout=timeout)


def _http_get(url, params=None, headers=None):
    """
    GET avec retry et backoff exponentiel (RG16, RG21).
    429 : respecte Retry-After (plafonne). 5xx, timeout, erreur reseau : backoff.
    403 : SourceBlocked immediat. 404 : SourceNotFound immediat.
    Retourne (reponse, nombre_de_requetes).
    """
    h = {"User-Agent": config.user_agent()}
    h.update(headers or {})
    retries = config.HTTP["retries"]
    backoff = config.HTTP["backoff_seconds"]
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = _transport(url, params, h, config.HTTP["timeout_seconds"])
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = type(exc).__name__ + " " + str(exc)[:200]
            _sleep(backoff * (2 ** attempt))
            continue
        status = resp.status_code
        if status == 403:
            raise SourceBlocked("403 acces refuse par la source")
        if status == 404:
            raise SourceNotFound("404 ressource introuvable")
        if status == 429:
            retry_after = resp.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else backoff * (2 ** attempt)
            last_error = "429 trop de requetes"
            _sleep(min(wait, config.HTTP["max_retry_after_seconds"]))
            continue
        if status >= 500:
            last_error = str(status) + " erreur serveur"
            _sleep(backoff * (2 ** attempt))
            continue
        if status >= 400:
            raise SourceUnavailable(str(status) + " erreur client")
        return resp, attempt + 1
    raise SourceUnavailable(last_error or "echec inconnu")


def http_get_json(url, params=None, headers=None):
    """GET JSON. Une reponse 200 qui n'est pas du JSON (GDELT renvoie ses erreurs en texte) leve SourceUnavailable."""
    resp, n = _http_get(url, params, headers)
    try:
        return resp.json(), n
    except ValueError:
        raise SourceUnavailable("reponse non JSON : " + str(getattr(resp, "text", ""))[:120])


def http_get_text(url, params=None, headers=None):
    """GET texte (flux RSS)."""
    resp, n = _http_get(url, params, headers)
    return resp.text, n


def content_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class Collector:
    name = ""
    source_type = SourceType.social
    sectors = ALL_SECTORS
    # True : un appel par marche. False : appel global, les marches sont dans le payload.
    geo_capable = False
    # Statut d'acces documente dans le README
    access = "gratuit"
    source_url = None

    def collect(self, trend, market, since, until=None):
        raise NotImplementedError

    def result(self, trend, market, status, payload=None, error=None, request_count=0, source_url=None):
        return CollectResult(
            source_name=self.name,
            source_type=self.source_type,
            trend_id=trend["id"],
            market=market or config.GLOBAL_MARKET,
            collected_at=datetime.now(timezone.utc),
            status=status,
            error=error,
            source_url=source_url or self.source_url,
            request_count=request_count,
            payload=payload or {},
        ).model_dump(mode="json")

    def terms(self, trend):
        return (trend.get("query_terms") or {}).get(self.name) or []

    def safe_collect(self, trend, market, since, until=None):
        """Filet de securite RG16 : une exception devient un statut error."""
        if trend["sector"] not in self.sectors:
            return self.result(trend, market, SourceStatus.not_applicable, error="secteur non couvert")
        try:
            return self.collect(trend, market, since, until)
        except Exception as exc:
            logger.warning("%s | %s | %s | erreur : %s", self.name, trend["id"], market, exc)
            return self.result(trend, market, SourceStatus.error, error=type(exc).__name__ + " " + str(exc)[:300])


def _today():
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# Google Trends (pytrends, non officiel donc desactivable)
# ---------------------------------------------------------------------------


class GoogleTrendsCollector(Collector):
    name = "google_trends"
    source_type = SourceType.social  # mappe sur social par le referentiel (RG03)
    geo_capable = True
    access = "gratuit, non officiel, fragile"
    source_url = "https://trends.google.com/trends/"

    def _fetch(self, keywords, geo, timeframe, cat):
        """Appel pytrends. TrendReq cree a chaque appel : aucun etat partage."""
        from pytrends.request import TrendReq

        client = TrendReq(hl=config.GOOGLE_TRENDS["hl"], tz=config.GOOGLE_TRENDS["tz"], timeout=(10, 25))
        client.build_payload(keywords, cat=cat, timeframe=timeframe, geo=geo)
        df = client.interest_over_time()
        rows = []
        if df is None or df.empty:
            return rows
        for idx, row in df.iterrows():
            rows.append({
                "date": idx.date().isoformat(),
                "values": {k: float(row[k]) for k in keywords if k in row},
                "is_partial": bool(row.get("isPartial", False)),
            })
        return rows

    def _cache_path(self, key):
        return Path(config.GOOGLE_TRENDS["cache_dir"]) / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def _read_cache(self, key):
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(data["cached_at"])
            if datetime.now(timezone.utc) - cached_at < timedelta(hours=config.GOOGLE_TRENDS["cache_ttl_hours"]):
                return data["payload"]
        except Exception:
            return None
        return None

    def _write_cache(self, key, payload):
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"cached_at": datetime.now(timezone.utc).isoformat(), "payload": payload}), encoding="utf-8")

    def _fetch_with_retry(self, keywords, geo, timeframe, cat):
        backoff = config.HTTP["backoff_seconds"]
        last = None
        for attempt in range(config.HTTP["retries"] + 1):
            try:
                return self._fetch(keywords, geo, timeframe, cat), attempt + 1
            except Exception as exc:
                last = exc
                # Google limite fort : backoff plus long que pour les API classiques
                _sleep(backoff * 5 * (2 ** attempt))
        raise SourceUnavailable("pytrends " + type(last).__name__ + " " + str(last)[:200])

    def collect(self, trend, market, since, until=None):
        terms = self.terms(trend)
        if not terms:
            return self.result(trend, market, SourceStatus.not_applicable, error="aucun query_term google_trends")
        until = until or _today()
        p = config.GOOGLE_TRENDS
        sector = trend["sector"]
        anchor = p["anchor_by_sector"][sector]
        cat = p["category_by_sector"].get(sector, 0)
        geo = market or ""
        timeframe = since.isoformat() + " " + until.isoformat()
        # Cache : pas plus d'une collecte par jour pour la meme requete
        cache_key = "|".join([geo, timeframe, str(cat), anchor] + sorted(terms))
        cached = self._read_cache(cache_key)
        if cached is not None:
            cached["from_cache"] = True
            return self.result(trend, market, SourceStatus.success, payload=cached)

        size = p["max_terms_per_batch"]
        series = {}
        scales = {}
        partial_dates = set()
        low_resolution = []
        requests_done = 0
        for i in range(0, len(terms), size):
            batch = terms[i:i + size]
            rows, n = self._fetch_with_retry([anchor] + batch, geo, timeframe, cat)
            requests_done += n
            anchor_vals = [r["values"].get(anchor, 0.0) for r in rows]
            anchor_mean = sum(anchor_vals) / len(anchor_vals) if anchor_vals else 0.0
            # Remise a l'echelle de l'ancre : comparable entre lots
            scale = p["anchor_reference"] / anchor_mean if anchor_mean > 0 else None
            for t in batch:
                scales[t] = scale
                series[t] = [
                    {"date": r["date"], "raw": r["values"].get(t, 0.0),
                     "rescaled": (r["values"].get(t, 0.0) * scale) if scale else None}
                    for r in rows
                ]
            partial_dates.update(r["date"] for r in rows if r["is_partial"])
            # Google renvoie des entiers relatifs au maximum du lot : face a l'ancre, un
            # terme de niche tombe a 0 par arrondi. Ce zero n'est pas une absence (RG05),
            # on redemande donc le terme seul, en pleine echelle, sans remise a l'ancre.
            for t in batch:
                if max((pt["raw"] for pt in series[t]), default=0) < p["min_raw_resolution"]:
                    _sleep(p["pause_seconds"])
                    alone, n = self._fetch_with_retry([t], geo, timeframe, cat)
                    requests_done += n
                    series[t] = [{"date": r["date"], "raw": r["values"].get(t, 0.0), "rescaled": None} for r in alone]
                    scales[t] = None
                    low_resolution.append(t)
                    partial_dates.update(r["date"] for r in alone if r["is_partial"])
            if i + size < len(terms):
                _sleep(p["pause_seconds"])

        payload = {
            "anchor": anchor, "geo": geo, "timeframe": timeframe, "category": cat,
            "series": series, "scales": scales, "partial_dates": sorted(partial_dates),
            "rescaled": all(s is not None for s in scales.values()), "from_cache": False,
            "low_resolution_refetched": low_resolution,
        }
        self._write_cache(cache_key, payload)
        _sleep(p["pause_seconds"])
        logger.info("google_trends | %s | %s | %s semaines", trend["id"], market, len(next(iter(series.values()), [])))
        return self.result(trend, market, SourceStatus.success, payload=payload, request_count=requests_done)


# ---------------------------------------------------------------------------
# Wikipedia Pageviews (persistance seulement, hors diversite)
# ---------------------------------------------------------------------------


class WikipediaPageviewsCollector(Collector):
    name = "wikipedia_pageviews"
    source_type = SourceType.reference_persistance
    access = "gratuit, API officielle Wikimedia"
    source_url = "https://wikimedia.org/api/rest_v1/"

    def collect(self, trend, market, since, until=None):
        items = self.terms(trend)
        if not items:
            return self.result(trend, market, SourceStatus.not_applicable, error="aucune page wikipedia")
        until = until or _today()
        # Sans contact (PROJECT_URL) dans le User-Agent, Wikimedia limite a 10 requetes par minute
        pause = config.WIKI_PAGEVIEWS["pause_seconds"] if config.PROJECT_URL else 6.5
        series = []
        errors = []
        requests_done = 0
        for item in items:
            project, article = item["project"], item["article"]
            url = (config.WIKI_PAGEVIEWS["url"] + "/" + project + "/all-access/user/"
                   + quote(article.replace(" ", "_"), safe="") + "/daily/"
                   + since.strftime("%Y%m%d") + "00/" + until.strftime("%Y%m%d") + "00")
            try:
                data, n = http_get_json(url)
                requests_done += n
                points = [{"date": datetime.strptime(it["timestamp"][:8], "%Y%m%d").date().isoformat(),
                           "views": int(it.get("views", 0))} for it in data.get("items", [])]
                series.append({
                    "project": project, "article": article,
                    "market": config.WIKI_PAGEVIEWS["project_market"].get(project, config.GLOBAL_MARKET),
                    "points": points,
                })
            except SourceNotFound:
                errors.append(project + "/" + article + " introuvable")
            except (SourceBlocked, SourceUnavailable) as exc:
                errors.append(project + "/" + article + " " + str(exc))
            _sleep(pause)
        if not series:
            return self.result(trend, market, SourceStatus.error, error=" | ".join(errors) or "aucune serie",
                               request_count=requests_done)
        return self.result(trend, market, SourceStatus.success,
                           payload={"series": series, "partial_errors": errors}, request_count=requests_done)


# ---------------------------------------------------------------------------
# Reddit (JSON public)
# ---------------------------------------------------------------------------


class RedditCollector(Collector):
    name = "reddit"
    source_type = SourceType.social
    access = "gratuit, JSON public, fragile (403 frequents sans OAuth)"
    source_url = "https://www.reddit.com/"

    def collect(self, trend, market, since, until=None):
        terms = self.terms(trend)
        if not terms:
            return self.result(trend, market, SourceStatus.not_applicable, error="aucun query_term reddit")
        p = config.REDDIT
        until = until or _today()
        subs = p["subreddits_by_sector"].get(trend["sector"], [])
        url = p["base_url"] + "/r/" + "+".join(subs) + "/search.json"
        since_ts = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        until_ts = datetime.combine(until, datetime.max.time(), tzinfo=timezone.utc).timestamp()
        posts = {}
        truncated = False
        requests_done = 0
        for term in terms:
            after = None
            for page in range(p["max_pages"]):
                params = {"q": term, "restrict_sr": 1, "sort": "new", "t": "all", "limit": p["limit"]}
                if after:
                    params["after"] = after
                data, n = http_get_json(url, params=params)  # 403 et 429 geres par http_get_json
                requests_done += n
                children = data.get("data", {}).get("children", [])
                reached_since = False
                for child in children:
                    d = child.get("data", {})
                    created = float(d.get("created_utc", 0))
                    if created < since_ts:
                        reached_since = True
                        continue
                    if created > until_ts:
                        continue
                    # Dedoublonnage par id de post
                    posts[d["id"]] = {
                        "id": d["id"], "created_utc": created, "subreddit": d.get("subreddit"),
                        "score": int(d.get("score", 0)), "num_comments": int(d.get("num_comments", 0)),
                        "title": (d.get("title") or "")[:200], "permalink": d.get("permalink"),
                    }
                after = data.get("data", {}).get("after")
                _sleep(p["pause_seconds"])
                if reached_since or not after:
                    break
            else:
                # Nombre de pages borne atteint sans couvrir toute la periode
                truncated = True
        coverage_start = since
        if truncated and posts:
            oldest = min(pp["created_utc"] for pp in posts.values())
            coverage_start = datetime.fromtimestamp(oldest, tz=timezone.utc).date()
        return self.result(trend, market, SourceStatus.success, request_count=requests_done, payload={
            "posts": sorted(posts.values(), key=lambda pp: pp["created_utc"]),
            "subreddits": subs, "coverage_start": coverage_start.isoformat(), "coverage_end": until.isoformat(),
            "truncated": truncated,
        })


# ---------------------------------------------------------------------------
# Stubs desactives par defaut (verifier CGU et robots avant activation)
# ---------------------------------------------------------------------------


class StubCollector(Collector):
    def __init__(self, name, source_type, sectors, access, note):
        self.name = name
        self.source_type = source_type
        self.sectors = sectors
        self.access = access
        self.note = note

    def collect(self, trend, market, since, until=None):
        return self.result(trend, market, SourceStatus.disabled, error="stub non implemente : " + self.note)


STUBS = [
    StubCollector("efsa", SourceType.institutionnel_reglementaire, ALL_SECTORS,
                  "gratuit, CGU a verifier", "EFSA, verifier conditions de reutilisation"),
    StubCollector("fda", SourceType.institutionnel_reglementaire, ALL_SECTORS,
                  "gratuit, API openFDA", "openFDA, verifier limites d'usage"),
    StubCollector("espacenet", SourceType.brevet, ALL_SECTORS,
                  "gratuit avec inscription (OPS)", "Espacenet OPS demande une inscription et une cle"),
]


# ---------------------------------------------------------------------------
# Registre
# ---------------------------------------------------------------------------


def build_registry():
    """Toutes les sources connues, actives ou non."""

    instances = [
        GoogleTrendsCollector(), WikipediaPageviewsCollector(), RedditCollector(),
        OpenFoodFactsCollector(), OpenBeautyFactsCollector(), FragranceSeedCollector(),
        GdeltCollector(), OpenAlexCollector(), RssTradePressCollector(), CsvImportCollector(),
        PaidSocialConnector(),
    ] + STUBS
    return {c.name: c for c in instances}


def enabled_collectors(names=None):
    """Sources actives (ENABLED_SOURCES), eventuellement filtrees par la demande du run."""
    registry = build_registry()
    wanted = names or config.ENABLED_SOURCES
    return [registry[n] for n in wanted if n in registry and n in config.ENABLED_SOURCES]


def disabled_source_names(names=None):
    registry = build_registry()
    wanted = set(names or registry)
    return sorted(n for n in registry if n in wanted and n not in config.ENABLED_SOURCES)


def week_start(d):
    """Lundi de la semaine de d (RG01)."""
    if isinstance(d, datetime):
        d = d.date()
    return d - timedelta(days=d.weekday())


def last_closed_week(today):
    """Lundi de la derniere semaine terminee. La semaine en cours n'est jamais ecrite."""
    return week_start(today) - timedelta(days=7)


def as_date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])



# ===========================================================================
# Section : ex collectors_food.py
# Collecteurs Food & Beverage.
#
# Open Food Facts : produits recents tries par created_t.
# Attention, deux proxys explicites (marques "proxy" dans le payload) :
#   - created_t est la date d'ajout dans la base, pas la date de lancement reelle
#   - marque referente = produit distribue dans au moins N pays
# ===========================================================================

class FactsCollector(Collector):
    """Base commune Open Food Facts et Open Beauty Facts (meme famille d'API)."""
    source_type = SourceType.lancement_produit
    settings_name = ""
    access = "gratuit, API ouverte"
    # Open Beauty Facts : base beaucoup plus petite, couverture partielle signalee
    partial_coverage = False

    @property
    def settings(self):
        return getattr(config, self.settings_name)

    def collect(self, trend, market, since, until=None):
        terms = self.terms(trend)
        if not terms:
            return self.result(trend, market, SourceStatus.not_applicable, error="aucun query_term " + self.name)
        p = self.settings
        until = until or datetime.now(timezone.utc).date()
        since_ts = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        until_ts = datetime.combine(until, datetime.max.time(), tzinfo=timezone.utc).timestamp()
        products = {}
        truncated = False
        requests_done = 0
        for term in terms:
            for page in range(1, p["max_pages"] + 1):
                params = {
                    "search_terms": term, "search_simple": 1, "action": "process", "json": 1,
                    "page_size": p["page_size"], "page": page, "sort_by": "created_t",
                    "fields": "code,product_name,brands,countries_tags,stores_tags,categories_tags,labels_tags,created_t",
                }
                category = (p.get("category_by_sector") or {}).get(trend["sector"])
                if category:
                    params.update({"tagtype_0": "categories", "tag_contains_0": "contains", "tag_0": category})
                data, n = http_get_json(p["search_url"], params=params)
                requests_done += n
                batch = data.get("products", [])
                reached_since = False
                for prod in batch:
                    created = float(prod.get("created_t") or 0)
                    if created < since_ts:
                        reached_since = True
                        continue
                    if created > until_ts or not prod.get("code"):
                        continue
                    # Dedoublonnage par code produit
                    products[prod["code"]] = {
                        "code": prod["code"],
                        "product_name": (prod.get("product_name") or "")[:200],
                        "brands": (prod.get("brands") or "")[:120],
                        "countries_tags": prod.get("countries_tags") or [],
                        "stores_tags": (prod.get("stores_tags") or [])[:20],
                        "categories_tags": (prod.get("categories_tags") or [])[:15],
                        "labels_tags": (prod.get("labels_tags") or [])[:15],
                        "created_t": created,
                    }
                _sleep(p["pause_seconds"])
                if reached_since or len(batch) < p["page_size"]:
                    break
            else:
                truncated = True
        coverage_start = since
        if truncated and products:
            oldest = min(pr["created_t"] for pr in products.values())
            coverage_start = datetime.fromtimestamp(oldest, tz=timezone.utc).date()
        return self.result(trend, market, SourceStatus.success, request_count=requests_done, payload={
            "products": sorted(products.values(), key=lambda pr: pr["created_t"]),
            "coverage_start": coverage_start.isoformat(),
            "coverage_end": until.isoformat(),
            "truncated": truncated,
            "coverage": "partielle" if self.partial_coverage else "standard",
            "proxy": {
                "launch_date": "created_t = date d'ajout dans la base, proxy du lancement",
                "reference_brand": "produit present dans au moins "
                                   + str(config.REFERENCE_BRAND_MIN_COUNTRIES) + " pays, proxy de marque referente",
                "distribution_breadth": "pays et magasins distincts declares dans la base, proxy de distribution",
            },
        })


class OpenFoodFactsCollector(FactsCollector):
    name = "open_food_facts"
    sectors = ("food_beverage",)
    settings_name = "OPEN_FOOD_FACTS"
    source_url = "https://world.openfoodfacts.org/"



# ===========================================================================
# Section : ex collectors_beauty.py
# Collecteurs Beauty.
#
# Open Beauty Facts : meme famille d'API qu'Open Food Facts. Verifie le
# 2026-11-12 : search.pl repond en JSON avec created_t et countries_tags.
# Base beaucoup plus petite qu'OFF, donc signal faible a interpreter avec prudence.
# ===========================================================================

class OpenBeautyFactsCollector(FactsCollector):
    # B2 : Beauty et Fragrance (categorie en:perfumes pour Fragrance)
    name = "open_beauty_facts"
    sectors = ("beauty", "fragrance")
    partial_coverage = True
    settings_name = "OPEN_BEAUTY_FACTS"
    source_url = "https://world.openbeautyfacts.org/"



# ===========================================================================
# Section : ex collectors_fragrance.py
# Collecteurs Fragrance.
#
# Aucune source gratuite fiable n'a ete identifiee pour les lancements parfum.
# fragrance_seed lit un echantillon SAISI A LA MAIN et marque "simule", pour
# que le secteur fonctionne de bout en bout. A remplacer des que l'equipe
# dispose d'une vraie source.
# ===========================================================================

class FragranceSeedCollector(Collector):
    name = "fragrance_seed"
    source_type = SourceType.lancement_produit
    sectors = ("fragrance",)
    access = "simule, saisie manuelle"
    source_url = "file://data/seed/fragrance_seed.json"

    def collect(self, trend, market, since, until=None):
        path = Path(config.FRAGRANCE_SEED_PATH)
        if not path.exists():
            return self.result(trend, market, SourceStatus.error, error="fichier seed fragrance absent")
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = [r for r in data.get("rows", []) if r.get("trend_id") == trend["id"]
                and r.get("week", "") >= since.isoformat()
                and (until is None or r.get("week", "") <= until.isoformat())]
        return self.result(trend, market, SourceStatus.success, payload={
            "rows": rows, "data_origin": "simule", "warning": data.get("warning"),
        })


# ===========================================================================
# Section : nouvelles sources (phase B, RG21 et RG22)
# Chaque source a son collecteur independant, le User-Agent generique du
# projet, une pause entre requetes, le backoff de _http_get et safe_collect.
# Cle absente ou rien a lire : statut "indisponible", jamais zero (RG05).
# Aucun scraping de site marchand ni de reseau social (RG22).
# ===========================================================================


def _month_starts(since, until):
    """Premiers jours des mois entierement termines entre since et until."""
    first = date(since.year, since.month, 1)
    if first < since:
        first = date(first.year + (first.month // 12), first.month % 12 + 1, 1)
    out = []
    m = first
    while True:
        nxt = date(m.year + (m.month // 12), m.month % 12 + 1, 1)
        if nxt - timedelta(days=1) >= until:
            break
        out.append(m)
        m = nxt
    return out


def first_monday(month_start):
    """Premier lundi du mois : semaine de rattachement d'un signal mensuel (RG01)."""
    return month_start + timedelta(days=(7 - month_start.weekday()) % 7)


class GdeltCollector(Collector):
    """B4 : GDELT DOC 2.0, volume d'articles (TimelineVolRaw) et ton moyen (TimelineTone)."""
    name = "gdelt"
    source_type = SourceType.presse_generale
    access = "gratuit, API ouverte, 1 requete toutes les 5 secondes"
    source_url = "https://api.gdeltproject.org/api/v2/doc/doc"

    def _timeline(self, query, mode, since, until):
        p = config.GDELT
        params = {"query": query, "mode": mode, "format": "json",
                  "startdatetime": since.strftime("%Y%m%d") + "000000",
                  "enddatetime": until.strftime("%Y%m%d") + "235959"}
        data, n = http_get_json(p["url"], params=params)
        _sleep(p["pause_seconds"])
        timeline = data.get("timeline") or []
        if not timeline:
            return None, n
        points = {}
        for pt in timeline[0].get("data", []):
            d = datetime.strptime(pt["date"][:8], "%Y%m%d").date().isoformat()
            points[d] = pt.get("value")
        return {"series": timeline[0].get("series"), "points": points}, n

    def collect(self, trend, market, since, until=None):
        terms = self.terms(trend)
        if not terms:
            return self.result(trend, market, SourceStatus.not_applicable, error="aucun query_term gdelt")
        until = until or _today()
        # GDELT refuse les expressions trop courtes entre guillemets : guillemets seulement sur les termes composes
        query = " OR ".join(('"' + t + '"') if " " in t else t for t in terms)
        if len(terms) > 1:
            query = "(" + query + ")"
        volume, n1 = self._timeline(query, config.GDELT["volume_mode"], since, until)
        if volume is None:
            return self.result(trend, market, SourceStatus.error, error="timeline GDELT vide", request_count=n1)
        tone, n2, tone_status = None, 0, "desactive"
        if config.GDELT["tone_enabled"]:
            # Verifie que le mode existe vraiment avant d'utiliser le ton
            try:
                tone, n2 = self._timeline(query, config.GDELT["tone_mode"], since, until)
                tone_status = "ok" if tone and tone.get("series") == "Average Tone" else "indisponible"
            except (SourceBlocked, SourceNotFound, SourceUnavailable) as exc:
                tone_status = "indisponible : " + str(exc)[:80]
        return self.result(trend, market, SourceStatus.success, request_count=n1 + n2, payload={
            "query": query, "volume": volume["points"],
            "tone": tone["points"] if tone_status == "ok" else None, "tone_status": tone_status,
            "tone_label": config.TONE_LABEL,
        })


class OpenAlexCollector(Collector):
    """B1 : OpenAlex, publications par mois contenant le concept ou un alias (titre et resume)."""
    name = "openalex"
    source_type = SourceType.recherche_academique
    access = "gratuit avec cle (OPENALEX_API_KEY)"
    source_url = "https://api.openalex.org/works"

    @property
    def sectors(self):
        return tuple(config.OPENALEX["sectors"])

    def collect(self, trend, market, since, until=None):
        p = config.OPENALEX
        if not p["api_key"]:
            return self.result(trend, market, SourceStatus.indisponible, error="OPENALEX_API_KEY absente")
        terms = self.terms(trend)
        if not terms:
            return self.result(trend, market, SourceStatus.not_applicable, error="aucun query_term openalex")
        until = until or _today()
        start = max(since, date(until.year - p["lookback_months"] // 12, until.month, 1))
        months = []
        requests_done = 0
        for m in _month_starts(start, until):
            end = date(m.year + (m.month // 12), m.month % 12 + 1, 1) - timedelta(days=1)
            total = 0
            for term in terms:
                params = {"filter": "title_and_abstract.search:" + term + ",from_publication_date:" + m.isoformat()
                          + ",to_publication_date:" + end.isoformat(),
                          "per_page": 1, "select": "id", "api_key": p["api_key"]}
                data, n = http_get_json(p["url"], params=params)
                requests_done += n
                total += int((data.get("meta") or {}).get("count") or 0)
                _sleep(p["pause_seconds"])
            months.append({"month": m.isoformat()[:7], "count": total})
        return self.result(trend, market, SourceStatus.success, request_count=requests_done, payload={
            "months": months, "terms": terms, "granularity": "mensuelle",
            "note": "somme par alias, une publication citant deux alias peut compter deux fois",
        })


def _parse_feed_date(text_value):
    if not text_value:
        return None
    try:
        return parsedate_to_datetime(text_value).date()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def term_pattern(terms):
    """Expression insensible a la casse, sur mots entiers, pour le concept et ses alias."""
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b", re.IGNORECASE)


def parse_feed(xml_text):
    """Items d'un flux RSS ou Atom : (titre, lien, date, resume). Le resume sert au filtre, jamais stocke."""
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        items.append((it.findtext("title") or "", it.findtext("link") or it.findtext("guid") or "",
                      _parse_feed_date(it.findtext("pubDate")), it.findtext("description") or ""))
    atom = "{http://www.w3.org/2005/Atom}"
    for it in root.iter(atom + "entry"):
        link = it.find(atom + "link")
        items.append((it.findtext(atom + "title") or "", link.get("href") if link is not None else "",
                      _parse_feed_date(it.findtext(atom + "updated") or it.findtext(atom + "published")),
                      it.findtext(atom + "summary") or ""))
    return items


def load_feeds(path=None):
    path = Path(path or config.RSS_FEEDS_PATH)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("feeds", [])


class RssTradePressCollector(Collector):
    """
    B3 : presse professionnelle par flux RSS (data/seed/rss_feeds.json).
    Compte les articles dont le titre ou le resume contient le concept ou un alias.
    Ne stocke que titre tronque, lien et date, jamais le texte complet.
    """
    name = "rss_trade_press"
    source_type = SourceType.presse_professionnelle
    access = "gratuit, flux RSS, conditions a verifier par flux"
    source_url = None

    def collect(self, trend, market, since, until=None):
        feeds = [f for f in load_feeds() if f.get("enabled") and f.get("sector") in (trend["sector"], "all")]
        if not feeds:
            return self.result(trend, market, SourceStatus.indisponible,
                               error="aucun flux active pour ce secteur (conditions a verifier)")
        terms = self.terms(trend)
        if not terms:
            return self.result(trend, market, SourceStatus.not_applicable, error="aucun query_term rss_trade_press")
        until = until or _today()
        pattern = term_pattern(terms)
        max_chars = config.RSS["title_max_chars"]
        items, coverage, errors = [], {}, []
        requests_done = 0
        for feed in feeds:
            try:
                text_value, n = http_get_text(feed["url"])
                requests_done += n
                parsed = parse_feed(text_value)
            except (SourceBlocked, SourceNotFound, SourceUnavailable, ET.ParseError) as exc:
                errors.append(feed["name"] + " " + str(exc)[:120])
                _sleep(config.RSS["pause_seconds"])
                continue
            dates = [d for _t, _l, d, _s in parsed if d is not None]
            if dates:
                # Le flux liste les derniers articles : couverture du plus ancien a la date de collecte
                coverage[feed["name"]] = [[min(dates).isoformat(), until.isoformat()]]
            for title, link, published, summary in parsed:
                if published is None or published < since or published > until:
                    continue
                items.append({"feed": feed["name"], "title": title.strip()[:max_chars], "link": link,
                              "published": published.isoformat(),
                              "matched": bool(pattern.search(title) or pattern.search(summary))})
            _sleep(config.RSS["pause_seconds"])
        if not coverage and errors:
            return self.result(trend, market, SourceStatus.error, error=" | ".join(errors), request_count=requests_done)
        return self.result(trend, market, SourceStatus.success, request_count=requests_done, payload={
            "feeds": coverage, "items": items, "terms": terms, "partial_errors": errors,
        })


def gdelt_titles(query, since, until):
    """RG28 : titres GDELT (mode ArtList), date et source. Aucun texte complet stocke."""
    p = config.GDELT
    params = {"query": query, "mode": "ArtList", "format": "json", "maxrecords": config.DISCOVER["gdelt_max_records"],
              "startdatetime": since.strftime("%Y%m%d") + "000000", "enddatetime": until.strftime("%Y%m%d") + "235959"}
    data, _n = http_get_json(p["url"], params=params)
    _sleep(p["pause_seconds"])
    out = []
    for art in data.get("articles", []):
        seen = art.get("seendate") or ""
        try:
            d = datetime.strptime(seen[:8], "%Y%m%d").date()
        except ValueError:
            continue
        out.append((d, (art.get("title") or "")[:config.RSS["title_max_chars"]], "gdelt"))
    return out


def post_json(url, payload):
    """RG32 : envoi webhook (JSON compatible Slack). Retourne le code HTTP."""
    resp = requests.post(url, json=payload, headers={"User-Agent": config.user_agent()},
                         timeout=config.HTTP["timeout_seconds"])
    return resp.status_code


def merge_rss_payloads(payloads):
    """
    Fusionne les payloads RSS successifs d'une tendance (les flux ne gardent que
    les derniers articles). Le collecteur reste sans etat : la fusion se fait a
    la normalisation, a partir de source_observations (append-only).
    """
    feeds = defaultdict(list)
    items = {}
    for p in payloads:
        for name, intervals in (p.get("feeds") or {}).items():
            feeds[name].extend(intervals)
        for it in p.get("items", []):
            items[(it["feed"], it.get("link") or it["title"])] = it
    merged = {}
    for name, intervals in feeds.items():
        out = []
        for start, end in sorted(intervals):
            if out and start <= (as_date(out[-1][1]) + timedelta(days=1)).isoformat():
                out[-1][1] = max(out[-1][1], end)
            else:
                out.append([start, end])
        merged[name] = out
    return {"feeds": merged, "items": sorted(items.values(), key=lambda i: i["published"])}


class CsvImportCollector(Collector):
    """
    B5 : import CSV fourni par le client (retail_ecommerce), data/import/*.csv.
    Colonnes : concept_id, market, week, metric (units, availability_pct, rank), value, source_name.
    Chaque ligne est validee. Les lignes invalides sont rejetees avec leur raison.
    """
    name = "csv_import"
    source_type = SourceType.retail_ecommerce
    access = "fichier fourni par le client"
    source_url = None
    columns = ["concept_id", "market", "week", "metric", "value", "source_name"]

    def read_rows(self, until):
        folder = Path(config.CSV_IMPORT_DIR)
        files = sorted(folder.glob("*.csv")) if folder.exists() else []
        accepted, rejected, seen = [], [], set()
        last = last_closed_week(until + timedelta(days=1))
        for path in files:
            with open(path, encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                if sorted(reader.fieldnames or []) != sorted(self.columns):
                    rejected.append({"file": path.name, "line": 1, "reason": "colonnes attendues : " + ", ".join(self.columns)})
                    continue
                for line, raw in enumerate(reader, start=2):
                    try:
                        row = CsvImportRow.model_validate({k: (v or "").strip() for k, v in raw.items()})
                    except ValidationError as exc:
                        rejected.append({"file": path.name, "line": line,
                                         "reason": " | ".join(e["msg"] for e in exc.errors())[:200]})
                        continue
                    if row.week > last:
                        rejected.append({"file": path.name, "line": line, "reason": "semaine non close"})
                        continue
                    key = (row.concept_id, row.market, row.week, row.metric, row.source_name)
                    if key in seen:
                        rejected.append({"file": path.name, "line": line, "reason": "ligne en double"})
                        continue
                    seen.add(key)
                    accepted.append({**row.model_dump(mode="json"), "file": path.name})
        return files, accepted, rejected

    def collect(self, trend, market, since, until=None):
        until = until or _today()
        files, accepted, rejected = self.read_rows(until)
        if not files:
            return self.result(trend, market, SourceStatus.indisponible, error="aucun fichier dans data/import")
        rows = [r for r in accepted if r["concept_id"] == trend["id"] and r["week"] >= since.isoformat()]
        return self.result(trend, market, SourceStatus.success,
                           error=(str(len(rejected)) + " ligne(s) rejetee(s)") if rejected else None,
                           payload={"rows": rows, "rejected": rejected, "files": [f.name for f in files]})


class PaidSocialConnector(Collector):
    """B7 : interface reseau social payant (X). Desactivee et non implementee (RG22)."""
    name = "paid_social_x"
    source_type = SourceType.social
    access = "payant, non souscrit"
    source_url = None

    def collect(self, trend, market, since, until=None):
        return self.result(trend, market, SourceStatus.disabled, error="connecteur payant non implemente")



# ===========================================================================
# Section : ex normalize.py
# Normalisation : observations brutes vers weekly_signals valides (RG01).
#
# Regles :
#   - semaine = lundi, seules les semaines terminees sont produites (decision RG14)
#   - chaque signal est rattache a un code pays ISO, ZZ si la source n'a pas de pays
#   - un zero n'est produit que si la source a repondu avec succes et n'a rien
#     trouve sur une semaine qu'elle couvrait vraiment (RG05)
#   - sector_data est valide par le schema du secteur
# Fonctions pures : aucune ecriture en base ici.
# ===========================================================================

def covered_weeks(coverage_start, coverage_end, today):
    """Lundis des semaines entierement couvertes par la collecte et terminees."""
    start = week_start(coverage_start)
    if start < coverage_start:
        start += timedelta(days=7)
    end = min(last_closed_week(today), week_start(coverage_end + timedelta(days=1)) - timedelta(days=7))
    weeks = []
    w = start
    while w <= end:
        weeks.append(w)
        w += timedelta(days=7)
    return weeks


def _link_event(market, week, events):
    window = timedelta(days=config.EVENT_PENALTY["event_match_days"])
    for ev in events or []:
        ev_market = ev.get("market")
        if ev_market not in (None, "", market) and market != config.GLOBAL_MARKET:
            continue
        ev_date = as_date(ev["event_date"])
        if week - window <= ev_date <= week + timedelta(days=6) + window:
            return True, ev.get("event_label")
    return False, None


def _sector_data(sector, data):
    schema = SECTOR_SIGNAL_SCHEMAS[Sector(sector)]
    return schema.model_validate(data).model_dump(mode="json", exclude_none=True)


def _clean_tag(tag):
    return tag.split(":", 1)[1] if ":" in tag else tag


# ---------------------------------------------------------------------------
# Par source : retourne une liste de dicts (market, week, volume, source_type, sector_data)
# ---------------------------------------------------------------------------


def _norm_google(payload, obs, today):
    last = last_closed_week(today)
    partial = set(payload.get("partial_dates", []))
    weekly = defaultdict(float)
    for term, points in (payload.get("series") or {}).items():
        for pt in points:
            if pt["date"] in partial:
                continue
            d = as_date(pt["date"])
            # pytrends date les semaines au dimanche : on rattache au lundi suivant
            monday = d + timedelta(days=1) if d.weekday() == 6 else week_start(d)
            if monday > last:
                continue
            value = pt["rescaled"] if pt.get("rescaled") is not None else pt.get("raw", 0.0)
            weekly[monday] += float(value)
    extra = {"anchor": payload.get("anchor"), "rescaled": payload.get("rescaled")}
    return [{"market": obs["market"], "week": w, "volume": v, "source_type": SourceType.social,
             "data": {"data_origin": DataOrigin.reel.value, "extra": extra}} for w, v in sorted(weekly.items())]


def _norm_wikipedia(payload, obs, today):
    last = last_closed_week(today)
    rows = []
    for serie in payload.get("series", []):
        by_week = defaultdict(list)
        for pt in serie.get("points", []):
            d = as_date(pt["date"])
            by_week[week_start(d)].append(int(pt["views"]))
        for w, views in sorted(by_week.items()):
            # Semaine complete uniquement (7 jours de donnees)
            if w > last or len(views) < 7:
                continue
            rows.append({"market": serie.get("market") or config.GLOBAL_MARKET, "week": w, "volume": float(sum(views)),
                         "source_type": SourceType.reference_persistance,
                         "data": {"data_origin": DataOrigin.reel.value,
                                  "extra": {"project": serie.get("project"), "article": serie.get("article")}}})
    return rows


def _norm_reddit(payload, obs, today):
    weeks = covered_weeks(as_date(payload["coverage_start"]), as_date(payload["coverage_end"]), today)
    counts = Counter()
    engagement = Counter()
    for post in payload.get("posts", []):
        w = week_start(datetime.fromtimestamp(post["created_utc"], tz=timezone.utc).date())
        counts[w] += 1
        engagement[w] += post.get("score", 0) + post.get("num_comments", 0)
    return [{"market": config.GLOBAL_MARKET, "week": w, "volume": float(counts.get(w, 0)),
             "source_type": SourceType.social,
             "data": {"data_origin": DataOrigin.reel.value, "extra": {"engagement": engagement.get(w, 0)}}}
            for w in weeks]


def _product_markets(product):
    markets = set()
    for tag in product.get("countries_tags", []):
        iso = config.FACTS_COUNTRY_TO_ISO.get(_clean_tag(tag))
        markets.add(iso or config.GLOBAL_MARKET)
    return markets or {config.GLOBAL_MARKET}


def _norm_facts(payload, obs, today, sector):
    weeks = covered_weeks(as_date(payload["coverage_start"]), as_date(payload["coverage_end"]), today)
    week_set = set(weeks)
    counts = Counter()
    reference = defaultdict(bool)
    labels = defaultdict(Counter)
    cats = defaultdict(Counter)
    # B6 proxy de distribution : pays et magasins distincts des produits crees cette semaine
    countries = defaultdict(set)
    stores = defaultdict(set)
    for prod in payload.get("products", []):
        w = week_start(datetime.fromtimestamp(prod["created_t"], tz=timezone.utc).date())
        if w not in week_set:
            continue
        is_ref = len(prod.get("countries_tags", [])) >= config.REFERENCE_BRAND_MIN_COUNTRIES
        for m in _product_markets(prod):
            counts[(m, w)] += 1
            reference[(m, w)] |= is_ref
            labels[(m, w)].update(_clean_tag(t) for t in prod.get("labels_tags", []))
            cats[(m, w)].update(_clean_tag(t) for t in prod.get("categories_tags", []))
            countries[(m, w)].update(_clean_tag(t) for t in prod.get("countries_tags", []))
            stores[(m, w)].update(_clean_tag(t) for t in prod.get("stores_tags", []))
    # Zero explicite pour les marches suivis sur les semaines couvertes
    keys = set(counts) | {(m, w) for m in config.MARKETS for w in weeks}
    rows = []
    for (m, w) in sorted(keys, key=lambda k: (k[1], k[0])):
        n = counts.get((m, w), 0)
        tier = None if n == 0 else ("reference_proxy" if reference[(m, w)] else "standard_proxy")
        data = {"data_origin": DataOrigin.reel.value, "proxy": True,
                "categories": [c for c, _ in cats[(m, w)].most_common(5)] or None,
                "distribution_breadth": {"countries": len(countries[(m, w)]), "stores": len(stores[(m, w)])}}
        if sector == Sector.food_beverage.value:
            data["product_launch_count"] = n
            data["launch_brand_tier"] = tier
            data["claim_type"] = labels[(m, w)].most_common(1)[0][0] if labels[(m, w)] else None
        elif sector == Sector.beauty.value:
            data["sku_count"] = n
            data["launch_brand_tier"] = tier
        else:
            # Fragrance : Open Beauty Facts ne distingue pas fine et mass, compte dans extra
            data["extra"] = {"product_count": n, "launch_brand_tier": tier, "coverage": payload.get("coverage")}
        rows.append({"market": m, "week": w, "volume": float(n), "source_type": SourceType.lancement_produit, "data": data})
    return rows


def _norm_seed(payload, obs, today):
    last = last_closed_week(today)
    rows = []
    for r in payload.get("rows", []):
        w = as_date(r["week"])
        if w > last:
            continue
        data = dict(r.get("sector_data") or {})
        data["data_origin"] = DataOrigin.simule.value
        rows.append({"market": r["market"], "week": w, "volume": float(r["signal_volume"]),
                     "source_type": SourceType(r.get("source_type", "lancement_produit")), "data": data})
    return rows


def _complete_weeks(daily, last):
    """Regroupe des valeurs journalieres par semaine. Seules les semaines de 7 jours et closes sont gardees."""
    by_week = defaultdict(dict)
    for d, v in daily.items():
        by_week[week_start(as_date(d))][d] = v
    return {w: days for w, days in sorted(by_week.items()) if len(days) == 7 and w <= last}


def _norm_gdelt(payload, obs, today):
    last = last_closed_week(today)
    tone = payload.get("tone") or {}
    rows = []
    for w, days in _complete_weeks(payload.get("volume") or {}, last).items():
        count = float(sum(v or 0 for v in days.values()))
        # Ton moyen pondere par le nombre d'articles du jour. "ton de la presse", hors score.
        weighted = [(tone[d], v) for d, v in days.items() if v and tone.get(d) is not None]
        tone_mean = (sum(t * v for t, v in weighted) / sum(v for _t, v in weighted)) if weighted else None
        rows.append({"market": config.GLOBAL_MARKET, "week": w, "volume": count, "source_type": SourceType.presse_generale,
                     "tone": round(tone_mean, 4) if tone_mean is not None else None,
                     "data": {"data_origin": DataOrigin.reel.value,
                              "extra": {"query": payload.get("query"), "tone_status": payload.get("tone_status")}}})
    return rows


def _norm_openalex(payload, obs, today):
    last = last_closed_week(today)
    rows = []
    for m in payload.get("months", []):
        w = first_monday(as_date(m["month"] + "-01"))
        if w > last:
            continue
        rows.append({"market": config.GLOBAL_MARKET, "week": w, "volume": float(m["count"]),
                     "source_type": SourceType.recherche_academique, "granularity": "mensuelle",
                     "data": {"data_origin": DataOrigin.reel.value, "extra": {"month": m["month"]}}})
    return rows


def _norm_rss(payload, obs, today):
    """Une semaine est produite si au moins un flux la couvre entierement. Seuls ces flux sont comptes."""
    covering = defaultdict(set)
    for feed, intervals in (payload.get("feeds") or {}).items():
        for start, end in intervals:
            for w in covered_weeks(as_date(start), as_date(end), today):
                covering[w].add(feed)
    counts = Counter()
    for it in payload.get("items", []):
        w = week_start(as_date(it["published"]))
        if it.get("matched") and it["feed"] in covering.get(w, ()):
            counts[w] += 1
    return [{"market": config.GLOBAL_MARKET, "week": w, "volume": float(counts.get(w, 0)),
             "source_type": SourceType.presse_professionnelle,
             "data": {"data_origin": DataOrigin.reel.value, "extra": {"feeds_covering": sorted(feeds)}}}
            for w, feeds in sorted(covering.items())]


def _norm_csv(payload, obs, today):
    """Une ligne par marche, semaine et source client. Volume = units, sinon availability_pct, sinon 1 (rang seul)."""
    last = last_closed_week(today)
    grouped = defaultdict(dict)
    for r in payload.get("rows", []):
        grouped[(r["market"], as_date(r["week"]), r["source_name"])][r["metric"]] = r["value"]
    rows = []
    for (m, w, src), metrics in sorted(grouped.items()):
        if w > last:
            continue
        if "units" in metrics:
            volume, kind = metrics["units"], "units"
        elif "availability_pct" in metrics:
            volume, kind = metrics["availability_pct"], "availability_pct"
        else:
            volume, kind = 1.0, "presence_classee"
        rows.append({"market": m, "week": w, "volume": float(volume), "source_type": SourceType.retail_ecommerce,
                     "source_name": ("csv:" + src)[:60],
                     "data": {"data_origin": DataOrigin.reel.value, "extra": {"metrics": metrics, "volume_kind": kind}}})
    return rows


def normalize_observation(obs, trend, events, today=None):
    """
    obs : dict (id, source_name, source_url, market, status, raw_payload).
    trend : dict (id, sector). events : liste de known_events de la tendance.
    Retourne une liste de WeeklySignalIn valides.
    """
    if obs["status"] != "success":
        return []
    today = today or datetime.now(timezone.utc).date()
    payload = obs.get("raw_payload") or {}
    name = obs["source_name"]
    if name == "google_trends":
        rows = _norm_google(payload, obs, today)
    elif name == "wikipedia_pageviews":
        rows = _norm_wikipedia(payload, obs, today)
    elif name == "reddit":
        rows = _norm_reddit(payload, obs, today)
    elif name in ("open_food_facts", "open_beauty_facts"):
        rows = _norm_facts(payload, obs, today, trend["sector"])
    elif name == "fragrance_seed":
        rows = _norm_seed(payload, obs, today)
    elif name == "gdelt":
        rows = _norm_gdelt(payload, obs, today)
    elif name == "openalex":
        rows = _norm_openalex(payload, obs, today)
    elif name == "rss_trade_press":
        rows = _norm_rss(payload, obs, today)
    elif name == "csv_import":
        rows = _norm_csv(payload, obs, today)
    else:
        return []

    signals = []
    for r in rows:
        linked, label = _link_event(r["market"], r["week"], events)
        signals.append(WeeklySignalIn(
            observation_id=obs["id"], trend_id=trend["id"], market=r["market"], week=r["week"],
            source_type=r["source_type"], source_name=r.get("source_name", name), source_url=obs.get("source_url"),
            signal_volume=r["volume"], event_linked=linked, event_label=label,
            sector_data=_sector_data(trend["sector"], r["data"]),
            granularity=r.get("granularity", "hebdomadaire"), tone_mean=r.get("tone"),
        ))
    return signals
