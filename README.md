# hackathon-app

## Stack
- **API** : Node 24 + Express (TypeScript), PostgreSQL 17
- **Front** : Vite (TypeScript)
- **IA** : API compatible OpenAI (Mistral par défaut, Ollama possible), configurée dans `api/.env`

## Démarrer
1. Prérequis : Docker Desktop lancé, VS Code + extension **Dev Containers**, clé SSH ajoutée sur GitHub (Windows : cloner dans WSL, pas dans `C:\`).
2. `git clone git@github.com:ORGANISATION/hackathon-app.git`, ouvrir le dossier dans VS Code, puis `F1` → **Dev Containers: Reopen in Container** (installe tout et crée `api/.env`).
3. Dans le terminal VS Code : `npm run dev`, puis ouvrir http://localhost:5173. Pour l'IA, renseigner `AI_API_KEY` dans `api/.env`.

## Vérifs utiles
```bash
curl localhost:3000/api/health
curl localhost:3000/api/questions
curl -X POST localhost:3000/api/ai -H 'Content-Type: application/json' -d '{"prompt":"Dis bonjour"}'
psql postgres://app:app@db:5432/app -c "SELECT id, question FROM questions"
```

Réinitialiser la base : **Reopen Folder Locally**, supprimer `db/data`, puis **Reopen in Container**.
