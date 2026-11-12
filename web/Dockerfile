# Image du front pour `docker compose up` (démo m2), voir docs/adr/0004-une-commande-pour-tout-lancer.md
# Vite construit des fichiers statiques, nginx les sert et relaie /api vers le service api.
# Contexte de build : la racine du dépôt (workspaces npm, un seul package-lock.json).

FROM node:24-trixie-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY api/package.json api/
COPY web/package.json web/
RUN npm ci --ignore-scripts
COPY web web
RUN npm run build -w web

FROM nginx:1.29-alpine
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/web/dist /usr/share/nginx/html
# nginx tourne sans être root : il doit pouvoir lire tous les fichiers, quels que soient les droits d'origine.
RUN chmod -R a+rX /usr/share/nginx/html
EXPOSE 80
