import express from 'express';
import { Pool } from 'pg';

// En dev, lit api/.env ; en production, l'hébergeur fournit les variables
try {
  process.loadEnvFile();
} catch {
  // Pas de fichier .env : on reste sur l'environnement courant
}

const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const server = express();
server.use(express.json());
const port = Number(process.env.PORT) || 3000;

// Si Ollama ou le service RAG ne tourne pas (profil ia non lancé), fetch lève une exception :
// on renvoie une 503 lisible au lieu d'une erreur 500 et d'une stack trace dans les logs.
async function appeler(
  url: string,
  init: RequestInit,
): Promise<Response | null> {
  try {
    return await fetch(url, init);
  } catch {
    return null;
  }
}

server.get('/api/health', async (_req, res) => {
  const result = await pool.query('select version() as v');
  res.json({ status: 'ok', db: result.rows[0].v });
});

server.get('/api/questions', async (_req, res) => {
  const result = await pool.query(
    'select * from questions order by id desc limit 10',
  );
  res.json(result.rows);
});

// Route IA : appelle un fournisseur compatible OpenAI configuré dans .env
server.post('/api/ai', async (req, res) => {
  const prompt =
    typeof req.body?.prompt === 'string' ? req.body.prompt.trim() : '';
  if (!prompt) {
    res.status(400).json({ error: 'Champ "prompt" manquant' });
    return;
  }

  const { AI_BASE_URL, AI_MODEL, AI_API_KEY } = process.env;
  if (!AI_BASE_URL || !AI_MODEL) {
    res
      .status(500)
      .json({ error: 'AI_BASE_URL ou AI_MODEL absent de api/.env' });
    return;
  }

  const response = await appeler(`${AI_BASE_URL}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${AI_API_KEY ?? ''}`,
    },
    body: JSON.stringify({
      model: AI_MODEL,
      messages: [{ role: 'user', content: prompt }],
    }),
  });

  if (!response) {
    res.status(503).json({
      error:
        'IA locale injoignable : lancer docker compose --profile ia up (ou Ollama sur la machine en dev)',
    });
    return;
  }

  if (!response.ok) {
    res.status(502).json({
      error: `Fournisseur IA : ${response.status}`,
      detail: await response.text(),
    });
    return;
  }

  const data = (await response.json()) as {
    choices: { message: { content: string } }[];
  };
  res.json({ answer: data.choices[0]?.message.content ?? '' });
});

// Service data (FastAPI de l'équipe data) : URL dans api/.env (DATA_API_URL)
server.post('/api/data/ask', async (req, res) => {
  const question =
    typeof req.body?.question === 'string' ? req.body.question.trim() : '';
  if (!question) {
    res.status(400).json({ error: 'Champ "question" manquant' });
    return;
  }

  const { DATA_API_URL, DATA_API_KEY } = process.env;
  if (!DATA_API_URL) {
    res.status(500).json({ error: 'DATA_API_URL absent de api/.env' });
    return;
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    // évite la page d'avertissement quand le service passe par un tunnel ngrok
    'ngrok-skip-browser-warning': 'true',
  };
  if (DATA_API_KEY) headers.Authorization = `Bearer ${DATA_API_KEY}`;

  const response = await appeler(`${DATA_API_URL}/ask`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ question }),
    signal: AbortSignal.timeout(60_000),
  });

  if (!response) {
    res.status(503).json({
      error:
        'Service data injoignable ou trop lent : lancer docker compose --profile ia up',
    });
    return;
  }

  if (!response.ok) {
    res.status(502).json({
      error: `Service data : ${response.status}`,
      detail: await response.text(),
    });
    return;
  }

  res.json(await response.json());
});

// --- Tendances (produit) : l'API lit ce que le service data a écrit en base (ADR 0003) ---

const SECTEURS = [
  'boissons',
  'snacking',
  'confiserie',
  'laitier',
  'beaute',
  'parfum',
  'hygiene',
];

// Colonnes communes à la liste et au détail, au format du schéma Tendance de openapi.yaml
const COLONNES_TENDANCE = `
  t.id, t.nom, t.profil_aromatique, t.verdict, t.score_durabilite, t.score_opportunite,
  t.secteurs, t.pays, t.sources, t.explication, t.marche_source, t.marche_cible,
  t.donnees_simulees, t.maj_le,
  CASE WHEN t.fenetre_verdict IS NULL THEN NULL ELSE json_build_object(
    'mois_avant_arrivee', t.fenetre_mois,
    'cycle_givaudan_mois', t.cycle_mois,
    'verdict', t.fenetre_verdict
  ) END AS fenetre_tir,
  COALESCE((
    SELECT json_agg(json_build_object(
      'nom', c.nom, 'poids', c.poids, 'note', c.note, 'source', c.source
    ) ORDER BY c.ordre)
    FROM criteres c WHERE c.tendance_id = t.id
  ), '[]') AS criteres`;

server.get('/api/trends', async (req, res) => {
  const { secteur } = req.query;
  if (
    secteur !== undefined &&
    (typeof secteur !== 'string' || !SECTEURS.includes(secteur))
  ) {
    res.status(400).json({
      error: `Secteur inconnu, valeurs possibles : ${SECTEURS.join(', ')}`,
    });
    return;
  }

  const limit = req.query.limit === undefined ? 5 : Number(req.query.limit);
  if (!Number.isInteger(limit) || limit < 1 || limit > 20) {
    res.status(400).json({ error: 'limit doit être un entier entre 1 et 20' });
    return;
  }

  const result = await pool.query(
    `select ${COLONNES_TENDANCE}
     from tendances t
     where $1::text is null or $1 = any (t.secteurs)
     order by t.score_opportunite desc
     limit $2`,
    [secteur ?? null, limit],
  );
  res.json(result.rows);
});

server.get('/api/trends/:id', async (req, res) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id) || id < 1) {
    res.status(404).json({ error: 'Tendance inconnue' });
    return;
  }

  const tendance = await pool.query(
    `select ${COLONNES_TENDANCE} from tendances t where t.id = $1`,
    [id],
  );
  if (tendance.rowCount === 0) {
    res.status(404).json({ error: 'Tendance inconnue' });
    return;
  }

  // Une ligne par semaine, toutes sources confondues (schéma PointHistorique)
  const historique = await pool.query(
    `select to_char(semaine, 'YYYY-MM-DD') as semaine, sum(volume)::int as volume
     from signaux where tendance_id = $1
     group by semaine order by semaine`,
    [id],
  );
  res.json({ ...tendance.rows[0], historique: historique.rows });
});

// Sans hôte explicite, Node écoute sur toutes les interfaces
server.listen(port, () => console.log(`API sur http://localhost:${port}`));
