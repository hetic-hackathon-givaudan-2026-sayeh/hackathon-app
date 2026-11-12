# 0005. Le front passe à React et Tailwind, à partir du prototype de l'équipe

- Statut : acceptée
- Date : 2026-11-12
- Décideurs : tech-lead (Sayeh), avec les product owners
- Remplace : la partie front de l'ADR 0001 (« Vite en TypeScript, sans framework pour l'instant »)

## Contexte

- Le front du jalon m2 est une page en TypeScript sans framework : un écran « Signaux » et un détail de tendance. Il valide la tranche verticale, mais couvre une seule étape du parcours.
- Les product owners ont produit avec Figma Make un prototype des cinq écrans de la charte (opportunités, Live Trend Radar, preuves, matching client, pitch). Figma Make exporte un projet React 19, Tailwind 4 et Vite, en TypeScript.
- Le prototype affichait des données inventées, dont des noms de clients proches de vraies marques. Nous l'avons branché sur notre API (`/api/trends`) et sur l'API du data (`/data-api`, en dev), avec un jeu de démonstration fictif en dernier recours.
- La V1 doit ajouter des écrans interactifs (matching, pitch généré, filtres) : l'état à gérer augmente.

## Options

- **Garder le front sans framework et y reporter le design du prototype** : aucune nouvelle dépendance, mais environ 1 200 lignes d'interface à réécrire en manipulant le DOM, et le travail du prototype jeté.
- **Adopter le prototype tel qu'exporté** : rapide, mais il embarque des plugins propres à Figma Make, ses propres fichiers d'instructions et des données inventées.
- **Adopter le prototype, nettoyé et branché sur nos API** : React et Tailwind dans `web/`, plugins Figma retirés, un seul fichier d'instructions (`AGENTS.md`), données réelles ou fictives signalées comme telles.

## Décision

On adopte le prototype nettoyé : React 19, Tailwind 4 et Vite, toujours en TypeScript. Le langage ne change pas, on ajoute une bibliothèque d'interface. Les règles d'honnêteté restent : une donnée absente s'affiche « non disponible », jamais 0 ; clients et données simulés sont étiquetés.

## Conséquences

- Positives : les cinq écrans de la charte sont en place et branchés. Le design de l'équipe et le code ne divergent plus. Le front lit dans l'ordre l'API du data, notre API, puis le jeu fictif `web/public/donnees-demo.json`, ce qui permet une version en ligne statique.
- Négatives acceptées : une dépendance de plus (React) et un bundle plus lourd (environ 400 Ko, 137 Ko compressés). `App.tsx` fait encore plus de 1 000 lignes : il faudra le découper par écran en V1. La provenance de certaines photos du prototype reste à confirmer (`web/public/assets/CREDITS.md`).
- Sécurité : la clé de l'API du data n'est jamais dans le code du navigateur. En dev, le proxy de Vite l'ajoute côté serveur (`web/.env.local`, ignoré par git). En production, ce sera notre API Node qui relaiera, comme pour `/api/data/ask`.
- Condition de révision : si le front doit être rendu côté serveur (référencement, lien public riche), on évaluera un framework dédié.
