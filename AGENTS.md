# AGENTS.md

Consignes pour tout assistant de code (Copilot, Codex, Cursor…) sur ce dépôt. Le README explique le projet ; ce fichier dit comment travailler dessus.

## Lancer

- Tout se lance dans le dev container (`.devcontainer/`), jamais sur la machine hôte : `node_modules` est installé pour Linux, `npm` lancé hors du conteneur casse `esbuild` et `rolldown`.
- `npm run dev` lance l'API (`api/`, port 3000) et le front (`web/`, port 5173).
- Base : `psql postgres://app:app@db:5432/app`. Le schéma et les données de test sont dans `db/init/`, rejoués seulement si `db/data/` est vide.
- Avant de dire « fini », ces trois commandes doivent passer (ce sont celles du job CI `qualite`) : `npm run lint`, `npm run format:check`, `npm run build`.

## Règles

- Une branche `feature/<nom>` depuis `develop`, puis une PR vers `develop` avec le modèle `.github/pull_request_template.md`. `main` et `develop` refusent les push directs.
- La configuration de l'IA passe uniquement par `api/.env` (`AI_BASE_URL`, `AI_MODEL`, `AI_API_KEY`). Ne pas coder un fournisseur en dur dans `api/src/`.
- Les fichiers d'environnement (`.devcontainer/`, `.github/`, `compose.yaml`, `package.json`) sont relus par les tech-leads (`.github/CODEOWNERS`).

## Décisions

- Lire `docs/adr/` avant toute proposition d'architecture. Ne pas contredire un ADR au statut « acceptée ».
- Pour changer une décision (base, fournisseur d'IA, framework), proposer un nouvel ADR à partir de `docs/adr/0000-modele.md`, puis s'arrêter et attendre la validation.
- `0001` : stack Node/TypeScript, Express, Vite, PostgreSQL 17. `0002` : IA locale uniquement.

## À ne pas faire

- Appeler une API d'IA externe : les données internes de Givaudan ne doivent pas sortir (ADR 0002).
- Écrire une clé ou un secret ailleurs que dans `api/.env`, qui est ignoré par git.
- Contourner le hook avec `git commit --no-verify` (la CI le rattrape de toute façon).
- Collecter des données en contournant un CAPTCHA, une connexion ou `robots.txt`, ou stocker des données personnelles (pseudos, profils).
- Brouillons et plans de session : dans `.scratch/`, ignoré par git, pas dans ce fichier.
