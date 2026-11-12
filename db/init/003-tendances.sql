-- Tendances détectées : écrites par le service data, lues par l'API (ADR 0003).
-- Les colonnes suivent les schémas Tendance, Critere et PointHistorique de openapi.yaml.

CREATE TABLE tendances (
  id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nom               TEXT NOT NULL,
  profil_aromatique TEXT[] NOT NULL DEFAULT '{}',
  -- Étape 1 « Trancher » : buzz, émergente ou durable
  verdict           TEXT NOT NULL CHECK (verdict IN ('buzz', 'emergente', 'durable')),
  score_durabilite  INTEGER NOT NULL CHECK (score_durabilite BETWEEN 0 AND 100),
  score_opportunite INTEGER NOT NULL CHECK (score_opportunite BETWEEN 0 AND 100),
  secteurs          TEXT[] NOT NULL DEFAULT '{}',
  pays              TEXT[] NOT NULL DEFAULT '{}',
  sources           TEXT[] NOT NULL DEFAULT '{}',
  explication       TEXT,
  -- Étape 2 « Situer » : marché d'origine, marché visé et fenêtre de tir
  marche_source     TEXT,
  marche_cible      TEXT,
  fenetre_mois      INTEGER CHECK (fenetre_mois > 0),    -- délai estimé avant l'arrivée sur le marché cible
  cycle_mois        INTEGER CHECK (cycle_mois > 0),      -- temps de développement d'un produit chez Givaudan
  fenetre_verdict   TEXT CHECK (fenetre_verdict IN ('go', 'urgence', 'no_go')),
  donnees_simulees  BOOLEAN NOT NULL DEFAULT true,
  maj_le            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Score décomposé : chaque critère est visible, pondéré et sourcé (fiche produit des PM)
CREATE TABLE criteres (
  id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tendance_id INTEGER NOT NULL REFERENCES tendances (id) ON DELETE CASCADE,
  ordre       INTEGER NOT NULL,
  nom         TEXT NOT NULL,
  poids       INTEGER CHECK (poids BETWEEN 0 AND 100),  -- en %, NULL tant que l'équipe ne l'a pas fixé
  note        INTEGER CHECK (note BETWEEN 0 AND 100),   -- NULL tant que le critère n'est pas calculé
  source      TEXT,
  UNIQUE (tendance_id, ordre)
);

-- Signaux agrégés par semaine et par source (jamais de données personnelles)
CREATE TABLE signaux (
  tendance_id INTEGER NOT NULL REFERENCES tendances (id) ON DELETE CASCADE,
  semaine     DATE NOT NULL,
  source      TEXT NOT NULL,
  volume      INTEGER NOT NULL CHECK (volume >= 0),
  PRIMARY KEY (tendance_id, semaine, source)
);
