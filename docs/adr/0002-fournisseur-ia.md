# 0002. Le fournisseur d'IA : un modèle qui tourne chez nous, plus d'API externe

- Statut : acceptée
- Date : 2026-11-10
- Décideurs : tech-lead (Sayeh), avec l'équipe data
- Remplace : le choix de Google Gemini fait pendant le setup (5 novembre)

## Contexte

- La route `/api/ai` appelle n'importe quelle API compatible OpenAI, configurée dans `api/.env` (`AI_BASE_URL`, `AI_MODEL`, `AI_API_KEY`). Changer de fournisseur ne demande aucune ligne de code.
- Pendant le setup, j'ai testé plusieurs fournisseurs :
  - Ollama en local : écarté sur le moment, parce qu'il fallait l'installer sur chaque machine de l'équipe ;
  - Mistral : `401 Invalid API Key`, les clés ne sont pas actives sur le plan Free ;
  - Gemini : `gemini-2.5-flash` en 404 (fermé aux nouveaux comptes), `gemini-3.8-flash` en 503 (saturé), `gemini-flash-lite-latest` retenu car il répondait en ~1,5 s.
- Nouvelle contrainte des organisateurs : aucune IA externe qui pourrait réutiliser les données. Notre produit doit croiser des données internes de Givaudan (demandes d'échantillons, commandes, projets R&D) avec des signaux externes : ces données ne peuvent pas partir chez un fournisseur tiers.

## Options

- **Garder Gemini** : rapide et gratuit, mais les données sortent de l'entreprise. Contraire à la contrainte, écarté.
- **Un autre fournisseur externe (Mistral, Hugging Face)** : même problème.
- **Un petit modèle open source qui tourne chez nous** (Ollama, llama.cpp, LM Studio, MLX ou vLLM selon la machine), exposé en format compatible OpenAI, éventuellement servi par le service du data.
- **Fine-tuner un modèle dès cette semaine** : risqué en une semaine (jeu d'entraînement, GPU, évaluation). Reporté.

## Décision

L'IA tourne sur une machine de l'équipe, jamais chez un fournisseur externe. On utilise un petit modèle open source exposé en format compatible OpenAI, pour que l'API n'ait qu'à changer `AI_BASE_URL` et `AI_MODEL`.

Le modèle ne sert qu'à expliquer et à reformuler (explication d'une tendance, traduction d'une description sensorielle en profil aromatique, argumentaire). Le score buzz / tendance durable est calculé sans IA générative, par le service du data.

## Conséquences

- Positives : les données internes ne quittent pas l'équipe. Pas de clé à gérer, pas de quota, 0 €. Le choix du modèle peut évoluer sans toucher au code.
- Négatives acceptées : un petit modèle est moins bon qu'un grand modèle hébergé, et lent sur CPU (dans un conteneur sans GPU). Il faudra des réponses pré-calculées en base pour la démo, et documenter l'installation du modèle dans le README.
- Condition de révision : si Givaudan fournit un modèle interne ou un hébergement maîtrisé, on bascule dessus par configuration. Le fine-tuning sera évalué par un spike du data (gain mesuré sur un jeu de descriptions de test).
- À faire : passer l'option par défaut de `api/.env.example` et du README de Gemini au modèle local.
