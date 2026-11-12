// @ts-check
import eslint from '@eslint/js';
import { defineConfig, globalIgnores } from 'eslint/config';
import prettier from 'eslint-config-prettier/flat';
import tseslint from 'typescript-eslint';

export default defineConfig(
  // db/data : fichiers PostgreSQL, illisibles hors du conteneur db
  // .venv : paquets Python du futur service data
  globalIgnores(['**/dist/', 'db/data/', '**/.venv/', '.scratch/']),
  eslint.configs.recommended,
  tseslint.configs.recommended,
  // la forme (guillemets, indentation) c'est Prettier qui s'en charge
  prettier,
);
