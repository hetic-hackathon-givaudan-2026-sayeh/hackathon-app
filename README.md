# hackathon-app

Environnement de développement de notre équipe pour le hackathon HETIC × Givaudan (novembre 2026) : une API, un front, une base PostgreSQL et un appel à une IA, le tout lancé dans un dev container identique sur Mac, Windows et Linux. Le produit sera décrit ici dès que le sujet sera fixé avec le product et le data.

## Stack

- **API** : Node 24 + Express en TypeScript (`api/`)
- **Front** : Vite en TypeScript, sans framework pour l'instant (`web/`)
- **Base** : PostgreSQL 17, schéma et données de test dans `db/init/`
- **IA** : n'importe quelle API compatible OpenAI, configurée dans `api/.env`. Par défaut Google Gemini (`gemini-flash-lite-latest`) ; Mistral, Hugging Face et Ollama sont prêts en commentaire dans `api/.env.example`

Les choix sont expliqués dans `docs/adr/`.

## Prérequis

À installer une seule fois, tout le reste vient du dépôt :

- **Docker Desktop** (Mac, Windows) ou Docker Engine (Linux), lancé
- **VS Code** avec l'extension **Dev Containers** (`ms-vscode-remote.remote-containers`)
- **Windows** : activer le moteur WSL 2 dans Docker Desktop et cloner le projet **dans WSL** (par exemple `~/projets`), pas dans `C:\`

## Démarrer en 3 étapes

1. **Cloner et ouvrir**

   ```bash
   git clone https://github.com/hetic-hackathon-givaudan-2026-sayeh/hackathon-app.git
   cd hackathon-app && code .
   ```

   Si `code` n'est pas reconnu : VS Code → File → Open Folder.

2. **Lancer le conteneur** : `F1` (ou `Cmd+Shift+P`) → **Dev Containers: Reopen in Container**. Le premier build prend quelques minutes : il installe Node, démarre PostgreSQL, lance `npm ci` et crée `api/.env` à partir de `api/.env.example`. C'est prêt quand la barre en bas à gauche affiche **Dev Container: Hackathon**.

3. **Démarrer l'API et le front**, dans le terminal de VS Code (prompt `node ➜ /workspace`) :

   ```bash
   npm run dev
   ```

   Ouvrir http://localhost:5173.

## Activer l'IA

Une clé n'est jamais commitée : chacun utilise la sienne.

1. Créer une clé gratuite (sans carte bancaire) sur https://aistudio.google.com/app/apikey
2. La coller dans `api/.env` : `AI_API_KEY=ta_cle`
3. Relancer : `Ctrl+C` puis `npm run dev` (le `.env` n'est lu qu'au démarrage)

## Vérifier que tout marche

Sur http://localhost:5173, on doit voir :

- `API OK · PostgreSQL 17…`
- les 2 questions de test
- une réponse quand on pose une question dans le formulaire IA

Depuis le terminal du conteneur :

```bash
curl localhost:3000/api/health
curl localhost:3000/api/questions
curl -X POST localhost:3000/api/ai -H 'Content-Type: application/json' -d '{"prompt":"Dis bonjour"}'
psql postgres://app:app@db:5432/app -c "SELECT id, question FROM questions"
```

Réinitialiser la base : **Dev Containers: Reopen Folder Locally**, supprimer `db/data`, puis **Reopen in Container**.

## Contribuer

- Partir de `develop` à jour et créer une branche `feature/<nom>` (une tâche, un à deux jours maximum)
- Committer **depuis le terminal du conteneur** : le hook pre-commit (husky + lint-staged) lance ESLint et Prettier et refuse le commit en cas d'erreur
- Pousser la branche, ouvrir une pull request vers `develop` et remplir le modèle
- `main` et `develop` sont protégées : pas de push direct, la CI `qualite` (lint, format, build) doit être verte pour fusionner
- Avant de pousser : `npm run lint`, `npm run format:check`, `npm run build`

## Dépannage

Problèmes rencontrés en montant l'environnement, avec la solution testée.

- **`ssh: connect to host github.com port 22: Connection refused`** au `git clone` : le réseau (celui de l'école par exemple) bloque le port 22. Le repo est public, clone-le en HTTPS : `git clone https://github.com/hetic-hackathon-givaudan-2026-sayeh/hackathon-app.git`. Pour garder SSH, passer par le port 443 : `ssh://git@ssh.github.com:443/hetic-hackathon-givaudan-2026-sayeh/hackathon-app.git`.
- **« An error occurred setting up the container »** sur Mac : lancer `docker compose up -d --build` dans le dossier du projet pour voir la vraie erreur. Si c'est `docker-credential-desktop: executable file not found in $PATH`, faire `sudo ln -sf /Applications/Docker.app/Contents/Resources/bin/docker-credential-desktop /usr/local/bin/docker-credential-desktop`, quitter VS Code (`Cmd+Q`) et refaire **Reopen in Container**.
- **`no configuration file provided`** : la commande `docker compose` a été lancée hors du dossier du projet.
- **Erreurs `esbuild` ou `rolldown` (`Cannot find native binding`) au `npm run dev`** : la commande a été lancée dans le terminal de la machine, pas dans celui du conteneur. `node_modules` est installé pour Linux. Toujours utiliser le terminal de VS Code, dont le prompt commence par `node ➜ /workspace`. Ne jamais lancer `npm install` hors du conteneur.
- **L'IA répond encore avec l'ancienne config** après une modification de `api/.env` : le fichier n'est lu qu'au démarrage. Arrêter avec `Ctrl+C` puis relancer `npm run dev`.
- **`Fournisseur IA : 503` « high demand »** : le modèle Gemini est saturé. Réessayer plus tard, ou changer `AI_MODEL` dans `api/.env` (`gemini-flash-lite-latest` répondait en ~1,5 s quand les autres modèles étaient saturés), puis relancer `npm run dev`.

Note : le Dockerfile utilise le miroir Debian `ftp.fr.debian.org`, car `deb.debian.org` était bloqué sur le réseau de l'école.
