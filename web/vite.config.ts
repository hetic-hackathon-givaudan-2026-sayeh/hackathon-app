import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv, type ProxyOptions } from 'vite';

export default defineConfig(({ mode }) => {
  // API du data (optionnelle, en dev) : URL et clé lues dans web/.env.local, SANS préfixe VITE_,
  // pour que la clé ne parte jamais dans le navigateur. Le serveur de dev l'ajoute lui-même.
  const env = loadEnv(mode, process.cwd(), '');
  const proxyData: Record<string, ProxyOptions> = env.DATA_API_URL
    ? {
        '/data-api': {
          target: env.DATA_API_URL,
          changeOrigin: true,
          rewrite: (chemin) => chemin.replace(/^\/data-api/, '/api'),
          headers: {
            'X-API-Key': env.DATA_API_KEY ?? '',
            'ngrok-skip-browser-warning': '1',
          },
        },
      }
    : {};

  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: true, // écoute hors du localhost du conteneur
      proxy: { '/api': 'http://localhost:3000', ...proxyData }, // /api vers notre API Node
    },
    build: {
      // deux pages : l'application (index.html) et la vérification de l'environnement (debug.html)
      rollupOptions: {
        input: { main: 'index.html', debug: 'debug.html' },
      },
    },
  };
});
