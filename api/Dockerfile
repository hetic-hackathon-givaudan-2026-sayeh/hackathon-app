# Image de l'API pour `docker compose up` (démo m2), voir docs/adr/0004-une-commande-pour-tout-lancer.md
# Même Node 24 sur Debian trixie que le dev container et la CI.
# Contexte de build : la racine du dépôt (workspaces npm, un seul package-lock.json).

FROM node:24-trixie-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY api/package.json api/
COPY web/package.json web/
# --ignore-scripts : le script prepare (husky) n'a pas de sens sans .git
RUN npm ci --ignore-scripts
COPY api api
RUN npm run build -w api

FROM node:24-trixie-slim
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json ./
COPY api/package.json api/
COPY web/package.json web/
# Uniquement les dépendances d'exécution de l'API (express, pg)
RUN npm ci --omit=dev --ignore-scripts --workspace api && npm cache clean --force
COPY --from=build /app/api/dist api/dist
USER node
EXPOSE 3000
CMD ["node", "api/dist/index.js"]
