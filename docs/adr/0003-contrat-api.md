# 0003. Le contrat d'API : `openapi.yaml` côté front, PostgreSQL entre l'API et le service data

- Statut : proposée (à valider avec le data)
- Date : 2026-11-10
- Décideurs : tech-lead (Sayeh), data

## Contexte

- Deux personnes codent en parallèle, dans deux langages : moi l'API et le front en TypeScript (ADR 0001), le data le calcul des scores en Python.
- La démo du 13 novembre doit aller de l'écran aux données. Si on découvre la veille que nos formats ne collent pas, la tranche verticale tombe.
- Juger « buzz ou tendance durable » demande un historique de signaux : la collecte et le calcul tournent en tâche de fond, pas à chaque clic (on stocke en base, puis on propose).
- Le modèle local est lent sur CPU (ADR 0002) : rien de lourd ne doit dépendre de lui dans une requête de l'utilisateur, sauf la génération d'argumentaire.

## Options

- **Le service data expose sa propre API HTTP**, appelée par l'API Node à chaque requête : deux contrats à maintenir, et une panne du service Python casse l'écran.
- **Le front appelle directement le service data** : deux points d'entrée pour le front, et des règles (secteurs, erreurs) en double.
- **Le service data écrit ses résultats dans PostgreSQL, l'API Node les lit et les expose au front** : une seule API pour le front, et le service data peut tourner, planter ou être relancé sans casser l'écran.

## Décision

- Le contrat entre le front et l'API est décrit dans `openapi.yaml`, à la racine du dépôt. Toute nouvelle route ou tout changement de format passe d'abord par ce fichier, dans la même PR que le code.
- Le service data ne parle pas au front : il écrit les tendances, leurs scores et l'historique des signaux dans PostgreSQL. Les colonnes suivent les schémas `Tendance` et `PointHistorique` de `openapi.yaml`, pour que l'API n'ait presque rien à transformer.
- L'API Node ne calcule aucun score : elle lit, filtre (`secteur`, `limit`) et appelle le modèle local uniquement pour `POST /api/trends/{id}/argumentaire`.
- Chaque tendance porte `donnees_simulees` : la démo annonce ce qui vient du jeu de démonstration.

## Conséquences

- Positives : le data et moi travaillons en parallèle sur une base commune. L'écran reste rapide (lecture en base). Le contrat est relu en PR comme le code.
- Négatives acceptées : le schéma de la base devient lui aussi un contrat, à faire évoluer avec soin (migration dans `db/init/`). Les résultats ne sont pas en temps réel, ils ont l'âge du dernier calcul (`maj_le`).
- Condition de révision : si le calcul doit réagir à une demande de l'utilisateur (ex : analyser un mot-clé saisi), on ajoutera une route au service data et un ADR pour la décrire.
