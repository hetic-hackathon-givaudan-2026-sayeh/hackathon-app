// Adaptateur pour l'API du data (FastAPI, schémas de models.py), joignable via /data-api.
// Le proxy de Vite ajoute la clé côté serveur : elle n'apparaît jamais dans le navigateur.
// On traduit ses réponses vers le format que les écrans connaissent déjà (TendanceApi).
import type { Critere, Fenetre, TendanceApi } from './donnees';

// --- Schémas de l'API data (sous-ensemble utile, noms identiques à models.py) ---
type CritereData = {
  value: number | null;
  weight: number;
  available: boolean;
  source?: string | null;
};
type ScoreData = {
  durability: number | null;
  classification: string | null;
  criteria: Record<string, CritereData | Record<string, unknown>>;
  as_of_week: string;
  flags?: string[] | null;
};
type TrendData = {
  id: string;
  label: string;
  sector: string;
  category: string;
  profile: Record<string, unknown>;
  first_seen_market?: string | null;
};
type ListItem = { trend: TrendData; latest_score: ScoreData | null };
type Detail = {
  trend: TrendData;
  score_history: ScoreData[];
  weekly_signals: {
    week: string;
    market: string;
    source_name: string;
    signal_volume: number;
    data_origin?: string | null;
  }[];
  timing: {
    estimate: {
      market: string;
      analog_delay_low: number | null;
      analog_delay_high: number | null;
      window_message: string | null;
      confidence_level: string;
    };
  }[];
  narrative: { content: Record<string, unknown> } | null;
  data_origins: Record<string, number>;
};
export type ClientData = {
  id: string;
  label: string;
  sector: string;
  brand_positioning?: string | null;
  active_markets: string[];
  innovation_cycle_months?: number | null;
  client_data_source: string;
};
export type MatchData = {
  id: number;
  trend_id: string;
  client_id: string;
  market: string | null;
  fit_score: number | null;
  fit_level: string | null;
  fit_criteria: Record<string, { value?: number | null } | number | null>;
  opportunity_score: number | null;
  client_data_source: string | null;
};
export type PitchData = {
  narrative: {
    origin: 'llm' | 'template';
    content: {
      who_to_call?: string;
      why_now?: string;
      what_to_say?: string;
      next_step?: string;
    };
  };
};

const BASE = '/data-api';

async function lire<T>(chemin: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${chemin}`, init);
  if (!r.ok || !r.headers.get('content-type')?.includes('json'))
    throw new Error(`API data : ${r.status} sur ${chemin}`);
  return (await r.json()) as T;
}

// Classes du score de durabilité (RG04) vers nos verdicts
const VERDICT: Record<string, string> = {
  'Noise/Buzz': 'buzz',
  'Weak Signal': 'a_surveiller',
  'Emerging Trend': 'emergente',
  'Validated Trend': 'durable',
  'Accelerating Trend': 'durable',
};
const SECTEURS: Record<string, string> = {
  food_beverage: 'food',
  fragrance: 'parfum',
  beauty: 'beaute',
};
const CRITERES: Record<string, string> = {
  persistance: 'Persistance',
  regularite: 'Régularité',
  diversite: 'Diversité des sources',
  diffusion: 'Diffusion',
  adoption_precoce: 'Adoption précoce',
};

function versCriteres(criteria: ScoreData['criteria']): Critere[] {
  return Object.entries(criteria)
    .filter(([cle]) => !cle.startsWith('_'))
    .map(([cle, brut]) => {
      const c = brut as CritereData;
      const poids =
        typeof c.weight === 'number'
          ? c.weight <= 1
            ? c.weight * 100
            : c.weight
          : null;
      return {
        nom: CRITERES[cle] ?? cle,
        poids: poids === null ? null : Math.round(poids),
        // RG05 : une valeur absente reste absente, jamais 0
        note:
          c.available && typeof c.value === 'number'
            ? Math.round(c.value)
            : null,
        source: c.source ?? null,
        disponible: Boolean(c.available),
      };
    });
}

function versFenetre(d: Detail, verdict: string): Fenetre {
  const t = d.timing.find((x) => x.estimate.market === 'FR') ?? d.timing[0];
  const e = t?.estimate;
  const intervalle =
    e && e.analog_delay_low !== null && e.analog_delay_high !== null
      ? `${Math.round(e.analog_delay_low)} à ${Math.round(e.analog_delay_high)} mois (confiance ${e.confidence_level})`
      : null;
  return {
    verdict: verdict === 'buzz' ? 'no_go' : 'go',
    mois_avant_arrivee: null, // RG10 : toujours un intervalle, jamais une date unique
    cycle_givaudan_mois: null,
    libelle:
      e?.window_message ?? intervalle ?? 'Données comparables insuffisantes',
  };
}

function texteNarratif(n: Detail['narrative']): string | null {
  const c = n?.content ?? {};
  const texte = [c.summary, c.durability_explanation]
    .filter((x) => typeof x === 'string')
    .join(' ');
  return texte || null;
}

export async function chargerDepuisData(): Promise<{
  tendances: TendanceApi[];
  clients: ClientData[];
  matches: MatchData[];
  cles: Record<number, string>;
}> {
  const liste = await lire<ListItem[]>('/trends');
  // Une tendance sans score (aucun run abouti) n'est pas affichée avec un faux 0 (RG05)
  const notees = liste.filter(
    (i) => i.latest_score && typeof i.latest_score.durability === 'number',
  );
  const [details, clients, matches] = await Promise.all([
    Promise.all(
      notees.map((i) =>
        lire<Detail>(`/trends/${encodeURIComponent(i.trend.id)}`).catch(
          () => null,
        ),
      ),
    ),
    lire<ClientData[]>('/clients').catch(() => []),
    lire<MatchData[]>('/matches?limit=500').catch(() => []),
  ]);
  const cles: Record<number, string> = {};
  const tendances = notees.map((item, index): TendanceApi => {
    const id = index + 1;
    cles[id] = item.trend.id;
    const s = item.latest_score!;
    const d = details[index];
    const verdict = VERDICT[s.classification ?? ''] ?? 'a_surveiller';
    const signaux = d?.weekly_signals ?? [];
    const parSemaine = new Map<string, number>();
    const parMarche = new Map<string, number>();
    for (const p of signaux) {
      parSemaine.set(p.week, (parSemaine.get(p.week) ?? 0) + p.signal_volume);
      parMarche.set(p.market, (parMarche.get(p.market) ?? 0) + p.signal_volume);
    }
    const opportunites = matches
      .filter((m) => m.trend_id === item.trend.id)
      .map((m) => m.opportunity_score ?? 0);
    const reel = (d?.data_origins.reel ?? 0) > 0;
    return {
      id,
      nom: item.trend.label,
      profil_aromatique: Array.isArray(item.trend.profile.claim_types)
        ? (item.trend.profile.claim_types as string[])
        : [],
      verdict,
      score_durabilite: Math.round(s.durability!),
      score_opportunite: Math.round(
        opportunites.length ? Math.max(...opportunites) : s.durability!,
      ),
      secteurs: [SECTEURS[item.trend.sector] ?? item.trend.sector],
      sources: [...new Set(signaux.map((p) => p.source_name))],
      explication: texteNarratif(d?.narrative ?? null),
      marche_source: item.trend.first_seen_market ?? null,
      marche_cible: d?.timing.find((x) => x.estimate.market === 'FR')
        ? 'FR'
        : (d?.timing[0]?.estimate.market ?? null),
      fenetre_tir: d ? versFenetre(d, verdict) : null,
      criteres: versCriteres(s.criteria),
      pays_en_hausse: [...parMarche]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([m]) => m),
      donnees_simulees: !reel,
      maj_le: s.as_of_week,
      historique: [...parSemaine]
        .sort()
        .map(([semaine, volume]) => ({ semaine, volume })),
    };
  });
  return { tendances, clients, matches, cles };
}

export function genererPitchData(trendId: string, clientId: string) {
  return lire<PitchData>(
    `/pitch/${encodeURIComponent(trendId)}/${encodeURIComponent(clientId)}`,
  );
}
