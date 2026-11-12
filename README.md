# TrendPulse

**TrendPulse tells Givaudan who to call, why now, and what to say.**

Plateforme de détection de tendances pour trois secteurs : food_beverage, fragrance, beauty.
Projet groupe 20, Givaudan AI Challenge, sujet 3 (HETIC), rôle Data. Le référentiel métier reste les deux PDF de l'équipe (fiche de scoring et champs à collecter). Priorités : maintenabilité et honnêteté des données.

## Intégration front : tout ce qu'il faut

> ⚠️ Cette section contient la clé d'API de dev. **Ne pas pousser ce README sur un dépôt public.** Si la clé fuit, la changer dans `.env` (`API_KEY=`) puis `docker compose up -d api`.

### Accès

| Élément | Valeur |
|---|---|
| URL publique (ngrok) | `https://morally-poker-pout.ngrok-free.dev` (mise à jour 2026-11-12, change si ngrok redémarre) |
| URL locale (même machine) | `http://localhost:8000` |
| Doc interactive Swagger | `<URL>/docs` (schémas complets de chaque route) |
| Schéma OpenAPI (génération de client) | `<URL>/openapi.json` |
| Clé d'API | `<demander la clé à l'équipe Data (API_KEY dans .env)>` |

### En-têtes à envoyer sur chaque requête

```http
X-API-Key: <demander la clé à l'équipe Data (API_KEY dans .env)>
ngrok-skip-browser-warning: 1
Content-Type: application/json        # pour les POST
```

- Sans `X-API-Key` valide, l'API répond **401**. Seule `GET /api/health` est publique.
- `ngrok-skip-browser-warning` évite la page d'avertissement HTML que ngrok renvoie sinon à la place du JSON.
- CORS est ouvert (`allow_origins=["*"]`) : appels directs depuis le navigateur OK.

### Test rapide

```bash
export TP_URL=https://morally-poker-pout.ngrok-free.dev
export TP_KEY=<demander la clé à l'équipe Data (API_KEY dans .env)>

curl "$TP_URL/api/health" -H "ngrok-skip-browser-warning: 1"
curl "$TP_URL/api/dashboard" -H "X-API-Key: $TP_KEY" -H "ngrok-skip-browser-warning: 1"
```

```js
// Exemple fetch côté front
const API_URL = import.meta.env.VITE_API_URL;   // URL ngrok
const API_KEY = import.meta.env.VITE_API_KEY;

async function api(path, options = {}) {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "X-API-Key": API_KEY,
      "ngrok-skip-browser-warning": "1",
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

const dashboard = await api("/api/dashboard");
const reco = await api("/api/recommendations/CLI-FB-01?market=FR");
```

### Identifiants disponibles

Tendances (`trend_id`) :

| trend_id | Libellé | Secteur |
|---|---|---|
| `food_yuzu` | Yuzu | food_beverage |
| `food_matcha` | Matcha | food_beverage |
| `food_hojicha` | Hojicha | food_beverage |
| `food_dubai_chocolate` | Dubai Chocolate | food_beverage |
| `beauty_centella` | Centella asiatica (cica) | beauty |
| `beauty_rice` | Rice extract (riz) | beauty |
| `beauty_snail_mucin` | Snail mucin | beauty |
| `fragrance_yuzu_note` | Note olfactive yuzu | fragrance |

Clients de démo (`client_id`, tous `client_data_source = simule`, à afficher comme tels) : `CLI-FB-01`, `CLI-FB-02` (food_beverage), `CLI-BE-01`, `CLI-BE-02` (beauty), `CLI-FR-01`, `CLI-FR-02` (fragrance).

Marchés (`market`, ISO 2 lettres) : `FR`, `US`, `GB`, `DE`, `JP`, `KR`.

### Écrans → routes

| Écran | Route |
|---|---|
| Dashboard (KPI, top 5, alertes, état des sources) | `GET /api/dashboard` |
| Liste des tendances | `GET /api/trends` |
| Fiche tendance (historique, signaux, fenêtre de tir) | `GET /api/trends/{trend_id}` |
| Explication du score (contribution par critère) | `GET /api/trend/{trend_id}/explain` |
| Alertes | `GET /api/alerts` |
| Clients | `GET /api/clients` |
| Matches tendance × client | `GET /api/matches` |
| Recommandations d'un client | `GET /api/recommendations/{client_id}?market=FR&limit=5` |
| Pitch commercial | `GET /api/pitch/{trend_id}/{client_id}` |
| Brief exportable | `GET /api/brief/{match_id}?format=json` ou `format=markdown` |
| Chat agent | `POST /api/agent/chat` |
| Simulation what-if | `POST /api/whatif` |
| Retour commercial | `POST /api/feedback` |
| Projection (peut renvoyer **501** si les extras ML ne sont pas installés) | `GET /api/predict/{trend_id}?market=FR&horizon_weeks=8` |
| Candidats émergents | `GET /api/discover` |
| Lancer / suivre une collecte | `POST /api/runs`, `GET /api/runs/{id}` |

### Corps des POST

```jsonc
// POST /api/agent/chat
{ "session_id": "front-123", "message": "Pourquoi le yuzu monte en France ?",
  "context": { "trend_id": "food_yuzu", "client_id": "CLI-FB-01", "market": "FR" } }
// réponse : { answer, origin, tools_used, evidence_ids, warnings, ... }

// POST /api/agent/synthesis   (kind : trend_brief | client_brief | weekly_digest | dashboard_summary)
{ "kind": "trend_brief", "trend_id": "food_yuzu", "market": "FR" }

// POST /api/whatif   (rien n'est enregistré)
{ "trend_id": "food_yuzu", "client_id": "CLI-FB-01", "market": "US", "innovation_cycle_months": 12 }

// POST /api/feedback   (status : contacted | won | lost)
{ "match_id": 1, "status": "contacted", "note": "Appel prévu jeudi" }

// POST /api/runs   (tous les champs optionnels ; {} = collecte complète)
{ "trend_ids": ["food_yuzu"] }
// puis GET /api/runs/{id} jusqu'à status = SUCCESS ou FAILED
```

### À savoir pour l'affichage

- **Valeur absente ≠ 0.** Un score ou un signal `null` / « indisponible » doit s'afficher comme « non disponible », jamais comme 0.
- Les textes générés portent `origin` (`llm` ou `template`) ; afficher les `warnings` renvoyés par l'agent.
- Les données simulées (clients de démo, échantillon fragrance) doivent rester étiquetées comme telles à l'écran.
- `latest_score` est `null` tant qu'aucun run n'a abouti. Un premier run a été lancé le 2026-11-12 ; si le dashboard est vide, relancer `POST /api/runs` avec `{}`.
- Aucun taux de précision n'est fourni par l'API : ne pas en afficher.

### Côté backend : lancer l'API et le tunnel

```bash
docker compose up -d                   # db, api, worker (le .env est déjà généré)
curl localhost:8000/api/health         # doit renvoyer "status": "ok"
ngrok http 8000                        # copier l'URL "Forwarding" dans le tableau Accès ci-dessus
```

- L'URL ngrok gratuite **change à chaque redémarrage** de ngrok : la renvoyer au dev à chaque fois (ou réserver un domaine statique gratuit sur le dashboard ngrok puis `ngrok http --url=<domaine> 8000`).
- Le Mac doit rester allumé, Docker et ngrok lancés, pour que l'URL réponde.
- Sur le Wi-Fi de l'école, ngrok n'a pas réussi à se connecter (accès à `crl.ngrok-agent.com` coupé) : lancer le tunnel depuis un autre réseau, un partage de connexion par exemple.

## Démarrage rapide

### Avec Docker (recommandé)

```bash
cp .env.example .env        # puis remplacer API_KEY, POSTGRES_PASSWORD, AGENT_DB_PASSWORD
docker compose up --build   # services db, api, worker
docker compose exec api python run.py db-upgrade   # base existante : tables, colonnes, vues, rôle agent_ro
```

- API : http://localhost:8000, documentation interactive sur http://localhost:8000/docs
- Toutes les routes sauf `/api/health` exigent l'en-tête `X-API-Key`
- Le service worker lance `python run.py worker`, qui exécute les runs et embarque le scheduler
- Extras de prédiction (phase F) : `docker compose build --build-arg INSTALL_ML=true`

Lancer une collecte :

```bash
curl -X POST http://localhost:8000/api/runs -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -d '{}'
curl http://localhost:8000/api/runs/1 -H "X-API-Key: $API_KEY"
```

Exposer pour la démo : `ngrok http 8000` (la clé API reste obligatoire).

### En local sans Docker (SQLite)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # ou requirements-ml.txt pour les extras
export API_KEY=une-cle-locale
python run.py seed                       # référentiels et calendrier
python pipeline.py --trends food_yuzu    # un run immédiat
uvicorn api:app --reload                 # API
python run.py worker                     # worker et scheduler
```

### Commandes `run.py`

| Commande | Rôle |
|---|---|
| `python run.py worker` | Worker de runs, scheduler embarqué (service Docker) |
| `python run.py scheduler` | Scheduler seul |
| `python run.py seed` | Référentiels, jours fériés (bibliothèque holidays), événements sourcés |
| `python run.py backtest` | Backtest v1 avec matrice de confusion, résultats dans `backtest_results` |
| `python run.py backtest --compare v1 v2` | Classe et score v1 contre v2 pour yuzu (FR), Dubai Chocolate, K-beauty |
| `python run.py db-upgrade` | Idempotent : ajoute tables, colonnes (`ADD COLUMN IF NOT EXISTS`), vues de l'agent. Ne supprime jamais rien |
| `python run.py discover` | Candidats de concepts émergents (RG28), statut « à valider » |

### LLM local (optionnel)

```bash
ollama serve
ollama pull qwen2.5:1.5b          # LLM_MODEL (pitch, synthèse, action suggérée)
ollama pull qwen2.5:7b            # AGENT_LLM_MODEL=qwen2.5:7b (agent, à renseigner dans .env)
ollama pull nomic-embed-text      # regroupement des candidats RG28, optionnel
```

Dans Docker, l'API joint Ollama sur l'hôte via `OLLAMA_HOST=http://host.docker.internal:11434` (`extra_hosts: host-gateway` est configuré pour Linux). `LLM_ENABLED=false` force les templates. Sans Ollama, tout fonctionne : chaque texte porte `origin = template`.

## Tests

```bash
pytest                       # aucun réseau, aucun Ollama
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/test pytest -m integration
```

174 tests collectés : 170 offline, plus 4 tests d'intégration PostgreSQL ignorés sans `DATABASE_URL` PostgreSQL.

- `test_collect.py` : pipeline complet sur fixtures, pannes simulées (429, timeout, 403, 500, réponse non JSON), append-only, nouvelles sources (clé absente donne « indisponible », import CSV invalide rejeté, `source_type_count` à 5 puis 6 avec un CSV client), calendrier
- `test_scoring.py` : règles v1 (renormalisation, pénalité, seuils, Opportunity, confiance RG12, fenêtre en intervalle), algorithmes v2 (saisonnalité, pic viral, croissance régulière, série courte, bootstrap, DTW, propagation), comparaison v1 contre v2, projection et challenger
- `test_api.py` : routes, clé API, worker, db-upgrade, recommandations, explain, agent (chiffre inventé, outil inconnu, injection dans un titre), import client, feedback, what-if, brief, découverte, webhook, intégration PostgreSQL
- `conftest.py` : fixtures pytest et fausses réponses des sources (données de test générées par formule)

## Architecture

Structure plate en 9 fichiers sources et 3 fichiers de tests. Chaque fichier est découpé en sections commentées.

| Fichier | Rôle | Règles |
|---|---|---|
| `config.py` | Toutes les constantes, valeurs à valider marquées A VALIDER | toutes |
| `models.py` | Contrats Pydantic, tables SQLAlchemy (JSONB sous PostgreSQL), vues de l'agent, `create_all`, `upgrade_db` | RG01 RG03 RG14 RG17 |
| `collect.py` | Collecteurs indépendants, registre, `ENABLED_SOURCES`, normalisation vers `weekly_signals` | RG01 RG05 RG16 RG21 RG22 |
| `scoring.py` | Durability v1 et v2, pénalité, Client Fit, timing, fenêtre de tir, confiance, Opportunity, alertes, paniers | RG04 à RG13, RG24, RG25, RG29, RG33, RG35 |
| `analytics.py` | Désaisonnalisation, indicateurs v2, bootstrap, DTW, propagation, projection, découverte | RG19 RG23b à RG28 |
| `agent.py` | Pitch, synthèse, action suggérée, agent en lecture seule, contrôle RG18 | RG15 RG17 RG18 RG34 |
| `pipeline.py` | Steps `collect`, `normalize`, `score_durability`, `score_timing`, `match_clients`, `alert`, `recommend`, `narrate`, plus notifications, what-if, brief | RG14 RG32 |
| `api.py` | API FastAPI | RG30 RG31 |
| `run.py`, `data/seed/` | Commandes, worker, scheduler, seed, backtest, référentiels | RG02 RG08 RG10 RG16 |

## Sources de données

| Source | Type (RG03) | Secteurs | Statut | Remarque |
|---|---|---|---|---|
| Google Trends (pytrends) | social | tous | gratuit, **fragile** | Non officiel. Ancre fixe par lot, cache 24 h, terme sous résolution 20 redemandé seul |
| GDELT DOC 2.0 | presse_generale | tous | gratuit | `TimelineVolRaw` et `TimelineTone` vérifiés en direct le 2026-11-12. Une requête toutes les 10 s. Erreurs renvoyées en texte avec un code 200, traitées comme « réponse non JSON » |
| OpenAlex | recherche_academique | food, beauty | gratuit **avec clé** `OPENALEX_API_KEY` | Sans clé : « indisponible ». Comptage mensuel ramené au premier lundi du mois, `granularity = mensuelle`. OpenAlex ne regroupe pas par date : une requête par mois et par alias |
| Presse professionnelle RSS | presse_professionnelle | tous | gratuit, **désactivé par flux** | 10 flux vérifiés accessibles le 2026-11-12 dans `data/seed/rss_feeds.json`, tous `enabled=false` en attendant la vérification des conditions. Seuls titre tronqué, lien et date sont stockés |
| Open Food Facts | lancement_produit | food | gratuit | `created_t` est un proxy du lancement. Proxy de distribution `distribution_breadth` (pays et magasins) |
| Open Beauty Facts | lancement_produit | beauty, fragrance | gratuit, **couverture partielle** | Fragrance filtrée sur `en:perfumes` (455 produits au 2026-11-12) |
| Import CSV client | retail_ecommerce | tous | fichier fourni par le client | `data/import/*.csv`, lignes invalides rejetées avec un rapport. Jamais versionné |
| Wikipedia Pageviews | hors diversité | tous | gratuit, API officielle | Persistance seulement. Sans `PROJECT_URL`, limite de 10 requêtes par minute |
| Reddit JSON public | social | tous | **désactivé par défaut** | Répondait 403 sans OAuth au 2026-11-12 |
| fragrance_seed | lancement_produit | fragrance | **simulé** | Échantillon saisi à la main |
| X (PaidSocialConnector) | social | tous | payant, interface non implémentée | Aucune collecte |
| EFSA, FDA, Espacenet | réglementaire, brevet | tous | stubs désactivés | Conditions et clés à vérifier |

Règles communes (RG21, RG22) : User-Agent générique du projet, sans e-mail personnel (`PROJECT_URL` optionnel). Chaque source a sa pause, son backoff et `safe_collect`. Une clé absente ou une source en échec donne « indisponible » ou « error », jamais zéro. Une requête réussie qui renvoie zéro est une vraie valeur observée. Aucun scraping de site marchand ni de réseau social.

- **Google Trends est mappé sur `social`.** Google Trends et Reddit comptent donc pour un seul type dans la diversité.
- **Ton GDELT.** Le champ `tone_mean` porte le libellé « ton de la presse ». Ce n'est pas un avis de consommateurs, et il n'entre dans aucun score.
- **Flux RSS.** Un flux ne liste que ses derniers articles (2 à 10 jours). La normalisation fusionne les collectes successives et compte une semaine seulement si un flux l'a couverte entièrement.
- **Fragrance reste faible.** La couverture est faible : la recommandation l'indique et la confiance reste basse, sans compensation.

## Données réelles, simulées et estimées

| Donnée | Nature | Où le voir |
|---|---|---|
| Signaux Google Trends, GDELT, OpenAlex, RSS, Wikipedia, Open Food Facts, Open Beauty Facts | **réelles** | `sector_data.data_origin = reel` |
| Lancements et distribution Open Food Facts et Open Beauty Facts | réelles, avec **proxys** | `sector_data.proxy = true`, `distribution_breadth` |
| Ventes importées par un client | **réelles** (fournies par le client) | `source_name = csv:<source>`, `retail_ecommerce` |
| Clients importés par `/api/clients/import` | **réels** | `client_data_source = reel` |
| Clients de démonstration | **simulés** | `client_data_source = simule`, libellé distinct sur chaque match et recommandation |
| Échantillon fragrance | **simulées** | `data_origin = simule` |
| Profil des tendances (prix, cibles, claims) | **estimées** | `profile.estimated = true` |
| Jours fériés | réels, bibliothèque holidays (RG23a) | `calendar_events.kind = jour_ferie` |
| Black Friday | calculé par règle : lendemain du quatrième jeudi de novembre (US, FR, GB, DE) | `calendar_events.kind = commercial` |
| Événements mobiles (Ramadan, Nouvel An chinois, fête des mères) | **TODO** | `data/seed/calendar_events.csv`, aucune date chargée tant qu'elle n'est pas sourcée |
| Seuils et paramètres marqués A VALIDER | **estimés** | `config.py`, à valider avec les Product Owners |

## Cas comparables (`data/seed/comparable_cases.csv`)

Recherche faite le 2026-11-12. Un cas n'est `verified = true` que si ses deux dates ont été lues dans une source, au moins au mois.

| Cas | Premier signal | Mainstream | Délai | Statut |
|---|---|---|---|---|
| Oat milk US (Oatly) | 2017-01, Sprudge | 2021-03-02, FoodNavigator | 50,0 mois | vérifié |
| Impossible Burger US | 2016-07-27, FoodNavigator | 2019-08-08, CBS News | 36,4 mois | vérifié |
| Dubai Chocolate AE vers DE | 2023-12-19, non vérifié | 2024-11, The National (Lindt) | | non vérifié : sources contradictoires |
| Hard seltzer, cushion compact, BB cream | année seule | | | non vérifié |
| Matcha, yuzu, mucine d'escargot, oud, note yuzu | | | | à documenter |

Avec 2 cas vérifiés en food et 0 ailleurs, ni la fenêtre RG10 ni les analogues DTW (RG26, `MIN_ANALOGS = 3`) ne sont calculés. L'API affiche « Données comparables insuffisantes » ou « non disponible ».

## Règles de scoring

### v1 (fiche de l'équipe)

- **Durability (RG04)** : persistance 30, régularité 20, diversité 20, diffusion 15, adoption précoce 10. Somme pondérée divisée par les poids disponibles, puis pénalité RG06 (5 ou 15).
- **Donnée indisponible n'est pas zéro (RG05).**
- **Opportunity (RG11)** : 0,45 Durability + 0,40 Fit + 0,15 Timing.
- **Confidence (RG12)** : poids `config.CONFIDENCE_WEIGHTS`.
- **Alerte (RG13)** : Durability >= 60, Fit >= 65, Opportunity >= 70, Confidence >= 70.
- **Append-only (RG14)** : semaines closes seulement, aucune mise à jour, chaque score référence `run_id`, `scoring_version` et `observation_ids`.

### v2 (phase C, `scoring_version = "v2"`, utilisée en aval)

Mêmes poids et même renormalisation que la fiche. La v1 reste calculée et stockée pour comparaison.

- **RG23a** : calendrier (`calendar_events`). Les semaines du calendrier ne peuvent pas être un pic organique.
- **RG23b** : décomposition STL si la série fait au moins 104 semaines **et** si une saisonnalité est détectée : autocorrélation à 52 semaines >= 0,5 (A VALIDER), calculée sur la série détendancée (droite de Theil-Sen retirée). Une tendance linéaire sans saison ne déclenche donc pas STL. Moins de 104 semaines : `seasonality_unknown`.
- **RG24** : persistance = part des 26 dernières semaines au-dessus de (médiane de base + 1 MAD). Régularité = tau de Kendall (scipy) et pente de Theil-Sen, moins la dispersion des résidus. Rétention = semaines +4 à +12 après le pic divisées par le pic. Pénalité linéaire de 5 à 15 si le pic est isolé (z robuste > 3) et la rétention < 0,6. Début de tendance par CUSUM (PELT de ruptures en complément s'il est installé). Flags `short_history` et `peak_recent`.
- **RG25** : bootstrap par blocs de 8 semaines (200 tirages, graine fixe), `score_low` et `score_high` aux percentiles 10 et 90, `class_uncertain` si l'intervalle chevauche un seuil.
- **RG26** : analogues DTW (fenêtre 12) contre `case_series`, l'historique Google Trends des cas vérifiés.
- **RG27** : séquence observée des types de sources, affichage seulement, aucune causalité.
- **RG29** : confiance v2 sur 8 composantes (dont précision de l'intervalle), moins 5 points par flag `seasonality_unknown` ou `short_history`.

### Recommandations (phase D)

- **RG33** : « à proposer » si les 4 conditions RG13 sont remplies. « À surveiller » si une seule manque avec un écart < 10 points. « Écartées » pour les tendances populaires refusées (hausse récente dans le top 20 pour cent), avec la raison. Limites 5, 5 et 3 par client et par marché.
- **RG34** : chaque justification porte un `evidence_id` existant. Le LLM rédige seulement `suggested_action`, qui parle d'exploration commerciale, sans promesse.
- **RG35** : `counter_evidence` est calculé en code et n'est jamais vide.
- `change_vs_last_run` : nouveau, monte, stable, baisse ou sorti.

### Agent (phase E)

- **RG17** : lecture seule. Le rôle PostgreSQL `agent_ro` n'a que `SELECT` sur les vues `agent_v_*` (vérifié sur PostgreSQL 16 : lecture des tables et écriture refusées). Aucun SQL n'est généré par le LLM.
- **RG18** : tout chiffre d'une réponse doit figurer dans les sorties d'outils (hors titres externes), et tout `evidence_id` doit exister. Sinon, repli sur le template. Champs `origin`, `tools_used`, `evidence_ids`, `warnings`.
- 14 outils, choix d'outil validé par Pydantic, routeur par mots-clés en secours, mémoire de 6 échanges. Mentions obligatoires : classe incertaine, saisonnalité inconnue, historique court, données non disponibles, ton de la presse.

### Projection (phase F, optionnelle)

- **RG19** : « projection statistique » par lissage exponentiel amorti, intervalle de prévision à 80 pour cent, jamais de date unique, jamais d'alerte. Elle est « non fiable » si elle ne bat pas la baseline naïve saisonnière en backtest glissant. Challenger durable ou buzz par régression logistique, avec `n_train` et avertissement petit échantillon.

## API

| Méthode | Route | Description |
|---|---|---|
| GET | `/api/health` | État de la base, du LLM et des sources (public) |
| GET | `/api/dashboard` | KPI, top 5, alertes, sources du dernier run |
| GET | `/api/trends`, `/api/trends/{id}` | Tendances, historique v2, signaux, fenêtre de tir |
| GET | `/api/trend/{id}/explain` | RG31 : contribution de chaque critère, somme des points égale au score, indicateurs v2, flags, intervalle, analogues |
| GET | `/api/matches` | Matches, avec le libellé `client_data_source` |
| GET | `/api/alerts`, `/api/clients` | Alertes RG13, clients |
| GET | `/api/pitch/{trend_id}/{client_id}` | Who to call, why now, what to say |
| GET | `/api/recommendations/{client_id}?market=&limit=` | Paniers à proposer, à surveiller, écartées, sorties |
| GET | `/api/recommendations/{client_id}/history` | Historique append-only |
| POST | `/api/agent/chat` | `session_id`, `message`, contexte `trend_id`, `client_id`, `market` |
| POST | `/api/agent/synthesis` | `trend_brief`, `client_brief`, `weekly_digest`, `dashboard_summary` (stockées dans `narratives`) |
| GET | `/api/predict/{trend_id}?market=&horizon_weeks=8` | Projection et challenger. 501 sans extras |
| POST | `/api/clients/import?kind=clients` | Référentiel client réel, JSON (liste) ou CSV (`text/csv`) |
| POST | `/api/clients/import?kind=sales&client_id=` | Ventes client vers `data/import` (lues par `csv_import`) |
| GET | `/api/trackrecord` | « rétro-test sur N cas », sans taux de précision |
| POST | `/api/feedback` | RG20 : `contacted`, `won`, `lost`. Jamais utilisé pour modifier les poids |
| POST | `/api/whatif` | Fit, timing et opportunité recalculés, rien n'est persisté |
| GET | `/api/brief/{match_id}?format=json\|markdown` | Export du brief |
| GET | `/api/discover` | Candidats RG28 « à valider », jamais scorés ni alertés |
| POST | `/api/runs`, GET `/api/runs/{id}` | File de runs |

## Exécution

- `POST /api/runs` crée un run `QUEUED`. Le worker le réserve (`FOR UPDATE SKIP LOCKED` et verrou consultatif PostgreSQL), puis le passe en `SUCCESS` ou `FAILED`.
- Planification : sources quotidiennes à 5 h UTC, digest hebdomadaire le lundi à 7 h UTC, Reddit toutes les 6 heures s'il est activé.
- **Webhook (RG32)** : `WEBHOOK_URL` optionnel, JSON compatible Slack, `WEBHOOK_DRY_RUN=true` par défaut. Seules les alertes RG13 et le digest hebdomadaire sont envoyés. Chaque envoi ou simulation est journalisé dans `notifications`.
- La panne d'une source ne bloque pas les autres. Son état est tracé dans `runs.source_status`.

## Migration Airflow

Chaque step de `pipeline.STEPS` lit et écrit en base à partir d'un `run` et devient une `@task` de même nom. Les collecteurs retournent un dict JSON-sérialisable et n'écrivent jamais en base.

## Backtest

```bash
python run.py backtest
python run.py backtest --compare v1 v2
```

`data/validation_cases.csv` ne contient que les cas cités par l'équipe. Seul Dubai Chocolate a une date (2023-12). yuzu (FR) et K-beauty sont listés pour la comparaison mais restent sans `as_of_date`. Les séries sont arrêtées à `as_of_date`, sans fuite temporelle.

Ces résultats vérifient la cohérence du score sur un très petit échantillon. **Aucun taux de précision n'est affiché nulle part.**

## À faire par l'équipe

- Compléter `as_of_date` (et le label de K-beauty) dans `data/validation_cases.csv`
- Documenter des cas comparables : au moins 3 vérifiés par secteur pour la fenêtre RG10 et les analogues RG26
- Trancher la date de la vidéo virale Dubai Chocolate (`known_events.csv`)
- Sourcer les dates des événements mobiles dans `data/seed/calendar_events.csv`
- Vérifier les conditions de chaque flux RSS avant de passer `enabled` à `true`
- Obtenir une clé OpenAlex au nom du projet (`OPENALEX_API_KEY`)
- Valider avec les Product Owners les paramètres A VALIDER, dont ceux des phases C et D
- Confirmer la tendance fragrance « note yuzu » et les trois actifs beauty
- Vérifier les catégories Google Trends 44 (beauty) et 242 (fragrance)
