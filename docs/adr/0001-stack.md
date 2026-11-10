# 0001. La stack : Node 24 + TypeScript, Express, Vite et PostgreSQL 17 dans un dev container

- Statut : acceptée
- Date : 2026-11-10
- Décideurs : tech-lead (Sayeh)

## Contexte

- Le hackathon dure deux semaines, avec une tranche verticale à montrer le 13 novembre (`docker compose up`, de l'écran aux données).
- Je suis le seul développeur de l'équipe (un PM, un profil data). Tout le code applicatif passe par moi : chaque outil en plus est un outil que je dois installer, apprendre et maintenir seul.
- Le data travaillera probablement en Python, de son côté.
- Le projet doit tourner à l'identique sur des machines différentes : mon Mac (Apple Silicon), les PC des coéquipiers et la machine du prof, qui teste le projet seul avec le README.
- Budget : 0 €.
- Le produit (thème 3 du challenge Givaudan) stocke des signaux de tendances datés, calcule des scores et les affiche.

## Options

- **API en Python (FastAPI), comme le data** : une seule langue avec lui, mais deux langages côté app (Python pour l'API, TypeScript pour le front) et un outillage qualité en double. Écartée pour limiter ce que je dois maintenir seul.
- **Framework plus complet (NestJS)** : structure plus stricte, mais plus de concepts à poser pour une API de quelques routes. Écartée pour le délai.
- **React dès maintenant** : utile quand il y aura plusieurs écrans. Le front actuel se limite à afficher l'API. Reporté, pas écarté.
- **MongoDB ou SQLite** : SQLite gère mal plusieurs services qui écrivent en parallèle (API + scripts du data). MongoDB n'apporte rien pour des signaux bien structurés (source, sujet, date, pays, volume) qu'on veut agréger par période.

## Décision

On garde la stack de départ du cours, qui fonctionne déjà de bout en bout (front, API, base, appel IA) :

- Node 24 + TypeScript et Express pour l'API (`api/`)
- Vite + TypeScript pour le front (`web/`)
- PostgreSQL 17, version figée dans `compose.yaml`, schéma et données de test dans `db/init/`
- tout tourne dans le dev container (`.devcontainer/`), même image Debian trixie qu'en CI

La raison principale : un seul langage pour le front et l'API, donc un seul outillage (ESLint, Prettier, hook, CI), ce que je peux tenir seul en deux semaines.

## Conséquences

- Positives : la chaîne complète tourne déjà et la CI `qualite` la vérifie à chaque PR. Le data peut ajouter son service Python à côté dans `compose.yaml` sans toucher à l'API.
- Négatives acceptées : deux langages dans l'équipe (TypeScript et Python), donc une frontière à définir entre l'API et le service data (voir le futur ADR sur le contrat d'API). Le front sans framework deviendra lourd si les écrans se multiplient.
- Condition de révision : passer à React dès que le front dépasse deux écrans interactifs. Reconsidérer l'API si un second développeur Python rejoint l'équipe.
