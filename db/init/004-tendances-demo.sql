-- Jeu de démonstration (donnees_simulees = true) : cas yuzu Japon → France de la fiche produit.
-- Les valeurs sont des exemples. Seul le critère « temporalité et persistance » (25 %) est fixé par l'équipe ;
-- les autres critères restent à définir et sont affichés comme tels.

INSERT INTO tendances
  (nom, profil_aromatique, verdict, score_durabilite, score_opportunite, secteurs, pays, sources, explication,
   marche_source, marche_cible, fenetre_mois, cycle_mois, fenetre_verdict)
VALUES
  ('Yuzu', '{agrume, zesté, floral, acidulé}', 'durable', 78, 82, '{boissons, confiserie}', '{JP, FR}', '{google_trends}',
   'L''intérêt pour le yuzu progresse régulièrement depuis trois ans au Japon, sans retomber après ses hausses : un profil de tendance installée, pas d''effet de mode.',
   'JP', 'FR', 11, 9, 'urgence'),
  ('Matcha salé', '{végétal, amer, umami}', 'durable', 64, 61, '{snacking, boissons}', '{JP, FR}', '{google_trends}',
   'Croissance lente mais continue sur 36 mois, portée par les snacks salés.',
   'JP', 'FR', 18, 9, 'go'),
  ('Hojicha', '{torréfié, boisé, doux}', 'emergente', 59, 55, '{boissons, laitier}', '{JP, FR}', '{google_trends}',
   'Hausse récente et régulière : à surveiller avant de trancher.',
   'JP', 'FR', 24, 9, 'go'),
  ('Sésame noir', '{grillé, noisette, amer}', 'buzz', 41, 28, '{laitier, confiserie}', '{JP, FR}', '{google_trends}',
   'Un pic de quelques semaines, puis retour au niveau de départ : un buzz.',
   'JP', 'FR', NULL, NULL, 'no_go'),
  ('Soda shiso', '{herbacé, mentholé, pétillant}', 'buzz', 35, 22, '{boissons}', '{JP, FR}', '{google_trends}',
   'Pic très court et très récent, sans base installée.',
   'JP', 'FR', NULL, NULL, 'no_go');

-- Critères du score : le premier vient de la fiche produit, les suivants attendent la décision des PM
INSERT INTO criteres (tendance_id, ordre, nom, poids, note, source)
SELECT t.id, c.ordre, c.nom, c.poids,
       CASE WHEN c.ordre = 1 THEN t.score_durabilite + 4 END,
       c.source
FROM tendances t
CROSS JOIN (VALUES
  (1, 'Temporalité et persistance', 25, 'Google Trends, recherches sur 12 à 36 mois'),
  (2, 'Critère 2 (à définir)', NULL, NULL),
  (3, 'Critère 3 (à définir)', NULL, NULL),
  (4, 'Critère 4 (à définir)', NULL, NULL)
) AS c (ordre, nom, poids, source);

-- 156 semaines de signaux simulés : croissance progressive pour une tendance, pic isolé pour un buzz
INSERT INTO signaux (tendance_id, semaine, source, volume)
SELECT t.id, DATE '2023-11-13' + s.i * 7, 'google_trends',
       GREATEST(0, round(
         CASE t.nom
           WHEN 'Yuzu'        THEN 15 + 60 * power(s.i / 155.0, 1.3) + 4 * sin(s.i / 4.0)
           WHEN 'Matcha salé' THEN 10 + 40 * (s.i / 155.0) + 3 * sin(s.i / 5.0)
           WHEN 'Hojicha'     THEN 8 + 22 * power(s.i / 155.0, 1.8) + 2 * sin(s.i / 3.0)
           WHEN 'Sésame noir' THEN 12 + 70 * exp(-power((s.i - 130) / 3.0, 2)) + 2 * sin(s.i / 4.0)
           WHEN 'Soda shiso'  THEN 9 + 55 * exp(-power((s.i - 145) / 2.5, 2)) + 2 * sin(s.i / 3.0)
         END))::int
FROM tendances t
CROSS JOIN generate_series(0, 155) AS s (i);
