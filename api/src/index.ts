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

// Sans hôte explicite, Node écoute sur toutes les interfaces
server.listen(port, () => console.log(`API sur http://localhost:${port}`));
