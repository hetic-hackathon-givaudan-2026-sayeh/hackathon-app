# 0004. Une seule commande lance toute l'application : un compose.yaml à la racine, avec des profils

- Statut : acceptée
- Date : 2026-11-11
- Décideurs : tech-lead (Sayeh), avec l'équipe data

## Contexte

- Le jalon m2 (vendredi 13 novembre) est jugé en direct : `docker compose up` doit lancer une tranche complète, de l'interface jusqu'aux données. Notre `compose.yaml` ne le permettait pas : le service `dev` attend avec `sleep infinity`, l'API et le front ne tournaient qu'avec `npm run dev` dans le dev container.
- L'équipe data a livré son service RAG (FastAPI + ChromaDB + Ollama, dossier `rag/` sur la branche `data_ia`) avec son propre `rag/docker-compose.yml`. Résultat : deux fichiers compose dans le dépôt, et un membre data qui a lancé ce compose depuis le terminal du dev container, donc Docker dans Docker, ce qui échoue.
- Ce second compose publiait open-webui sur le port 3000, déjà pris par notre API, et exposait le RAG via un tunnel ngrok dont l'URL change à chaque redémarrage (il a fallu la redemander pour chaque test).
- Les membres data codent en Python sur leur machine, sans Node : le hook pre-commit (`npx lint-staged`) bloquait leurs commits.
- Le premier lancement d'Ollama télécharge environ 1,3 Go de modèles (`qwen2.5:1.5b` et `nomic-embed-text`).

## Options

- **Garder deux fichiers compose (racine + `rag/`)** : chaque équipe reste autonome, mais il faut deux commandes, les ports se marchent dessus, et l'API ne peut joindre le RAG que par ngrok ou par l'IP de la machine. Écarté.
- **`include:` du compose de `rag/` dans celui de la racine** : une seule commande, mais l'équipe data garde la main sur des services (open-webui, ngrok) qui entrent en conflit avec les nôtres, et une erreur dans `rag/` casse tout le démarrage. Écarté.
- **Un seul `compose.yaml` à la racine, avec des profils** : `db`, `api` et `web` démarrent toujours ; Ollama et le RAG sont dans le profil `ia` ; open-webui et ngrok dans le profil `outils` ; le dev container dans le profil `dev`. L'API et le front ont chacun un Dockerfile (`api/Dockerfile`, `web/Dockerfile` avec nginx).

## Décision

Un seul `compose.yaml` à la racine, avec des profils. `docker compose up --build` lance la tranche interface → API → PostgreSQL sur http://localhost:8080 ; `docker compose --profile ia up --build` ajoute Ollama et le service RAG, joint par l'API sur `http://rag:8000` sans tunnel.

Le RAG reste dans un profil tant que `rag/` n'est pas fusionné dans `develop` et testé : sans ce dossier, son build ferait échouer toute la commande, et la démo du m2 avec.

Le modèle par défaut est `qwen2.5:1.5b`, celui du service data, pour n'en télécharger qu'un. Il se change avec la variable `LLM_MODEL`, sans toucher au code.

## Conséquences

- Positives : une commande pour le jury comme pour un nouvel arrivant. L'équipe data n'a plus besoin du dev container ni de Node : `docker compose --profile ia up rag` depuis le terminal de sa machine. Plus de ngrok en local, plus de conflit de port (open-webui passe sur 3001, Ollama n'est pas publié pour ne pas gêner un Ollama déjà installé).
- Négatives acceptées : le `compose.yaml` devient plus long et mélange dev et démo. L'image `api` et l'image `web` refont chacune un `npm ci` (builds plus longs). Le premier lancement avec le profil `ia` prend plusieurs minutes (images + 1,3 Go de modèles). Les images `ollama/ollama`, `open-webui` et `ngrok` sont en `latest`, donc non figées. Le hook pre-commit est sauté sur une machine sans Node : seule la CI vérifie ces commits.
- Condition de révision : quand le RAG est fusionné et a tourné sans erreur sur deux machines, on le sort du profil `ia` pour qu'il démarre par défaut. Si les images deviennent trop lentes à construire, on passera à une image de base commune pour `api` et `web`.
