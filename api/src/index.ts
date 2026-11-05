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

  const response = await fetch(`${AI_BASE_URL}/chat/completions`, {
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

// Sans hôte explicite, Node écoute sur toutes les interfaces
server.listen(port, () => console.log(`API sur http://localhost:${port}`));
