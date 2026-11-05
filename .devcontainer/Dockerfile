# Même base que l'image de production : Node 24 sur Debian trixie
FROM mcr.microsoft.com/devcontainers/typescript-node:24-trixie

# Switch package repositories to a reliable French mirror
RUN sed -i 's|deb.debian.org|ftp.fr.debian.org|g' /etc/apt/sources.list.d/debian.sources

# Client PostgreSQL (le serveur tourne dans le service db)
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
