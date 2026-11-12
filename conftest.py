"""
Fixtures pytest communes. Base SQLite en memoire, aucun reseau.

Contient aussi les fausses reponses des sources (ex fixtures.py) : donnees de
TEST uniquement, generees par formule. Elles ne representent aucune tendance
reelle. Les tests y accedent par "import conftest".
"""

from datetime import date, datetime, timedelta, timezone

import pytest
import requests

import config
from models import init_db, make_engine, make_session_factory
from run import seed


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch, tmp_path):
    """Aucun test ne doit toucher le reseau ni Ollama."""
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    monkeypatch.setattr(config, "API_KEY", "test-key")
    monkeypatch.setitem(config.HTTP, "backoff_seconds", 0)
    monkeypatch.setitem(config.GOOGLE_TRENDS, "cache_dir", str(tmp_path / "gt_cache"))
    for section in (config.GOOGLE_TRENDS, config.REDDIT, config.OPEN_FOOD_FACTS,
                    config.OPEN_BEAUTY_FACTS, config.WIKI_PAGEVIEWS, config.GDELT, config.OPENALEX, config.RSS):
        monkeypatch.setitem(section, "pause_seconds", 0)
    # Les tests v1 tournent avec les sources v1, Reddit active explicitement.
    # Les tests des nouvelles sources les activent eux-memes.
    monkeypatch.setattr(config, "ENABLED_SOURCES", list(V1_SOURCES))
    monkeypatch.setitem(config.OPENALEX, "api_key", "")
    monkeypatch.setattr(config, "CSV_IMPORT_DIR", str(tmp_path / "import"))
    monkeypatch.setattr(config, "RSS_FEEDS_PATH", str(tmp_path / "rss_feeds.json"))
    # Bootstrap reduit pour la duree des tests (les tests RG25 fixent leur propre n)
    monkeypatch.setattr(config, "BOOTSTRAP_N", 20)


@pytest.fixture
def engine():
    return init_db(make_engine("sqlite://"))


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        seed(s)
        yield s


# ===========================================================================
# Section : fausses reponses des sources (ex fixtures.py)
# ===========================================================================

TODAY = date(2026, 11, 12)
V1_SOURCES = ["google_trends", "wikipedia_pageviews", "reddit", "open_food_facts", "open_beauty_facts", "fragrance_seed"]
NEW_SOURCES = ["gdelt", "openalex", "rss_trade_press", "csv_import"]


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text if text is not None else ""
        self._is_text = text is not None

    def json(self):
        if self._is_text:
            raise ValueError("pas du JSON")
        return self._payload


def _ts(d):
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).timestamp() + 3600


# Profils de test : (base, pente par semaine, semaine du pic ou None)
PROFILES = {
    "yuzu": (30.0, 0.35, None),
    "matcha": (70.0, 0.05, None),
    "hojicha": (8.0, 0.4, None),
    "dubai chocolate": (5.0, 0.0, 60),
    "centella asiatica": (40.0, 0.1, None),
    "cica cream": (20.0, 0.1, None),
    "snail mucin": (50.0, -0.1, None),
    "rice toner": (15.0, 0.2, None),
    "rice skincare": (10.0, 0.2, None),
    "yuzu perfume": (5.0, 0.05, None),
}


def term_value(term, i):
    base, slope, peak = PROFILES.get(term, (10.0, 0.0, None))
    value = base + slope * i + (i % 3)
    if peak is not None and i == peak:
        value = 100.0
    return max(value, 0.0)


def fake_google_fetch(self, keywords, geo, timeframe, cat):
    """Remplace GoogleTrendsCollector._fetch : semaines datees au dimanche comme pytrends."""
    start = date.fromisoformat(timeframe.split(" ")[0])
    first_sunday = start + timedelta(days=(6 - start.weekday()) % 7)
    rows = []
    i = 0
    d = first_sunday
    while d <= TODAY:
        values = {k: (50.0 if k in ("coffee", "moisturizer", "perfume") else term_value(k, i)) for k in keywords}
        rows.append({"date": d.isoformat(), "values": values, "is_partial": d + timedelta(days=6) > TODAY})
        d += timedelta(days=7)
        i += 1
    return rows


def _wiki(url):
    parts = url.split("/")
    article = parts[-4]
    start = datetime.strptime(parts[-2][:8], "%Y%m%d").date()
    end = datetime.strptime(parts[-1][:8], "%Y%m%d").date()
    items = []
    d = start
    i = 0
    while d <= end:
        items.append({"article": article, "timestamp": d.strftime("%Y%m%d") + "00", "views": 100 + (i // 7) % 20})
        d += timedelta(days=1)
        i += 1
    return FakeResponse(200, {"items": items})


def _reddit(params):
    term = params["q"]
    posts = []
    for k in range(12):
        d = TODAY - timedelta(days=7 * k + 2)
        posts.append({"data": {"id": term[:3] + str(k), "created_utc": _ts(d), "subreddit": "food",
                               "score": 10 + k, "num_comments": 3, "title": "Post de test sur " + term,
                               "permalink": "/r/food/test" + str(k)}})
    # Un doublon pour verifier le dedoublonnage par id
    posts.append(posts[0])
    old = TODAY - timedelta(weeks=200)
    posts.append({"data": {"id": "old", "created_utc": _ts(old), "subreddit": "food", "score": 1,
                           "num_comments": 0, "title": "ancien", "permalink": "/old"}})
    return FakeResponse(200, {"data": {"children": posts, "after": None}})


def _facts(params, beauty=False):
    term = params["search_terms"]
    products = []
    for k in range(20):
        d = TODAY - timedelta(days=10 * k + 3)
        countries = ["en:france", "en:germany", "en:united-states"] if k % 4 == 0 else ["en:france"]
        products.append({"code": term[:3] + str(k), "product_name": "Produit test " + term, "brands": "Marque test",
                         "countries_tags": countries, "categories_tags": ["en:beverages" if not beauty else "en:face-care"],
                         "labels_tags": ["en:organic"], "created_t": _ts(d)})
    old = TODAY - timedelta(weeks=300)
    products.append({"code": "old1", "product_name": "ancien", "countries_tags": ["en:france"],
                     "categories_tags": [], "labels_tags": [], "created_t": _ts(old)})
    return FakeResponse(200, {"count": len(products), "products": products})


def _gdelt(params, tone_missing=False):
    """Timeline journaliere : volume regulier, ton entre 1 et 3."""
    start = datetime.strptime(params["startdatetime"][:8], "%Y%m%d").date()
    end = datetime.strptime(params["enddatetime"][:8], "%Y%m%d").date()
    tone = params["mode"] == "TimelineTone"
    if tone and tone_missing:
        return FakeResponse(200, text="Invalid mode specified.")
    data = []
    d, i = start, 0
    while d <= end:
        value = (1.0 + (i % 3)) if tone else 3 + (i % 5)
        data.append({"date": d.strftime("%Y%m%d") + "T000000Z", "value": value})
        d += timedelta(days=1)
        i += 1
    series = "Average Tone" if tone else "Article Count"
    return FakeResponse(200, {"query_details": {}, "timeline": [{"series": series, "data": data}]})


def _openalex(params):
    month = params["filter"].split("from_publication_date:")[1][:7]
    return FakeResponse(200, {"meta": {"count": 2 + int(month[-2:]) % 4}})


def rss_xml(term="yuzu", weeks=4):
    """Flux RSS de test : deux articles par jour sur plusieurs semaines, un sur deux cite le terme."""
    items = []
    for k in range(weeks * 7 * 2):
        d = TODAY - timedelta(hours=12 * k)
        title = ("New " + term + " launch") if k % 2 == 0 else "Packaging update"
        items.append("<item><title>" + title + "</title><link>https://exemple.org/a" + str(k) + "</link>"
                     "<pubDate>" + d.strftime("%a, %d %b %Y") + " 08:00:00 +0000</pubDate>"
                     "<description>Texte complet a ne pas stocker</description></item>")
    return "<rss><channel><title>test</title>" + "".join(items) + "</channel></rss>"


def make_transport(fail=None):
    """
    Retourne un faux _transport. fail : dict source -> mode de panne
    ("429" : toujours trop de requetes, "timeout", "403", "500").
    """
    fail = fail or {}
    calls = []

    def transport(url, params, headers, timeout):
        calls.append(url)
        if "reddit.com" in url:
            source = "reddit"
        elif "openfoodfacts" in url:
            source = "open_food_facts"
        elif "openbeautyfacts" in url:
            source = "open_beauty_facts"
        elif "wikimedia" in url:
            source = "wikipedia_pageviews"
        elif "gdeltproject" in url:
            source = "gdelt"
        elif "openalex" in url:
            source = "openalex"
        elif "exemple-rss.test" in url:
            source = "rss_trade_press"
        else:
            raise AssertionError("URL inattendue : " + url)
        mode = fail.get(source)
        if mode == "429":
            return FakeResponse(429, headers={"Retry-After": "0"})
        if mode == "timeout":
            raise requests.Timeout("timeout simule")
        if mode == "403":
            return FakeResponse(403)
        if mode == "500":
            return FakeResponse(500)
        if mode == "non_json":
            return FakeResponse(200, text="The specified phrase is too short.")
        if source == "gdelt":
            return _gdelt(params, tone_missing=(mode == "tone_missing"))
        if source == "openalex":
            return _openalex(params)
        if source == "rss_trade_press":
            return FakeResponse(200, text=rss_xml())
        if source == "reddit":
            return _reddit(params)
        if source == "wikipedia_pageviews":
            return _wiki(url)
        return _facts(params, beauty=(source == "open_beauty_facts"))

    transport.calls = calls
    return transport
