import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: true, // écoute hors du localhost du conteneur
    proxy: { '/api': 'http://localhost:3000' }, // redirige /api vers l'API
  },
  build: {
    // deux pages : l'application (index.html) et la vérification de l'environnement (debug.html)
    rollupOptions: {
      input: { main: 'index.html', debug: 'debug.html' },
    },
  },
});
