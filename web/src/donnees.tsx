// Données de TrendPulse : lues dans notre API (/api/trends), à la place des exemples écrits en dur.
// Seuls les clients restent simulés (en attente du matching du modèle data), et c'est affiché.
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { chargerDepuisData, type ClientData, type MatchData } from './apiData';

export type Critere = {
  nom: string;
  poids: number | null;
  note: number | null;
  source: string | null;
  disponible: boolean;
};
export type Fenetre = {
  verdict: 'go' | 'urgence' | 'no_go';
  mois_avant_arrivee: number | null;
  cycle_givaudan_mois: number | null;
  libelle: string | null;
};
export type TendanceApi = {
  id: number;
  nom: string;
  profil_aromatique: string[];
  verdict: string;
  score_durabilite: number;
  score_opportunite: number;
  secteurs: string[];
  sources: string[];
  explication: string | null;
  marche_source: string | null;
  marche_cible: string | null;
  fenetre_tir: Fenetre | null;
  criteres: Critere[];
  pays_en_hausse: string[];
  donnees_simulees: boolean;
  maj_le: string;
  historique?: { semaine: string; volume: number }[];
};

export const MATURITE: Record<string, string> = {
  buzz: 'Buzz',
  a_surveiller: 'Signal précoce',
  emergente: 'Émergente',
  durable: 'Durable',
  mature: 'Mature',
  declin: 'En déclin',
};
const SECTEUR: Record<string, string> = {
  parfum: 'Parfumerie',
  beaute: 'Beauté',
  hygiene: 'Hygiène',
  food: 'Arômes',
};
const IMAGES = [
  `${import.meta.env.BASE_URL}assets/dew-leaf.jpg`,
  `${import.meta.env.BASE_URL}assets/lavender.jpg`,
  `${import.meta.env.BASE_URL}assets/botanical.jpg`,
  `${import.meta.env.BASE_URL}assets/white-flower.jpg`,
  `${import.meta.env.BASE_URL}assets/quiet-vitality.jpg`,
];
const TEINTES = ['sage', 'lilac', 'sand', 'rose'];

// Clients simulés : libellés génériques, jamais de marque réelle (règle RG08)
export type ClientVue = {
  id: string;
  name: string;
  type: string;
  country: string;
  simule: boolean;
};
export const CLIENTS: ClientVue[] = [
  {
    id: 'A',
    name: 'Client A',
    type: 'Boissons premium',
    country: 'France',
    simule: true,
  },
  {
    id: 'B',
    name: 'Client B',
    type: 'Pâtisserie industrielle',
    country: 'France',
    simule: true,
  },
  {
    id: 'C',
    name: 'Client C',
    type: 'Produits laitiers',
    country: 'Europe',
    simule: true,
  },
  {
    id: 'D',
    name: 'Client D',
    type: 'Snacking salé',
    country: 'France',
    simule: true,
  },
  {
    id: 'E',
    name: 'Client E',
    type: 'Confiserie artisanale',
    country: 'France',
    simule: true,
  },
];

// Compatibilité tendance × client : celle du modèle data (RG07) si elle existe, sinon une valeur d'exemple
// exemple : valeurs inventées pour la démo ; clientSimule : client de démonstration (RG08), même si le score est calculé
export type Compatibilite = {
  score: number;
  niveau: string;
  exemple: boolean;
  clientSimule: boolean;
  criteres: Record<string, number | null>;
};

// Score de compatibilité d'exemple, stable d'une visite à l'autre (aucun calcul métier)
export function fitExemple(tendance: string, client: string) {
  let h = 0;
  for (const c of `${tendance}-${client}`) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return 55 + (h % 40);
}

export type Tendance = {
  id: number;
  name: string;
  client: string;
  market: string;
  category: string;
  maturity: string;
  score: number;
  growth: string;
  sources: number;
  image: string;
  tint: string;
  api: TendanceApi;
};

function versVue(
  t: TendanceApi,
  index: number,
  meilleurClient: (t: TendanceApi) => string,
): Tendance {
  const croissance = t.criteres.find(
    (c) => /croissance/i.test(c.nom) && c.disponible && c.note !== null,
  );
  return {
    id: t.id,
    name: t.nom,
    client: meilleurClient(t),
    market: t.marche_cible ?? t.pays_en_hausse[0] ?? '—',
    category: SECTEUR[t.secteurs[0]] ?? 'Arômes',
    maturity: MATURITE[t.verdict] ?? t.verdict,
    score: t.score_durabilite,
    growth: croissance ? `${croissance.note}/100` : '—',
    sources: t.criteres.filter((c) => c.disponible && c.note !== null).length,
    image: IMAGES[index % IMAGES.length],
    tint: TEINTES[index % TEINTES.length],
    api: t,
  };
}

export function estAlerte(t: Tendance) {
  const f = t.api.fenetre_tir?.verdict;
  return (
    (f === 'go' || f === 'urgence') &&
    t.api.verdict !== 'buzz' &&
    t.api.verdict !== 'declin'
  );
}

export function texteFenetre(t: Tendance) {
  const f = t.api.fenetre_tir;
  if (!f) return 'À estimer';
  if (f.verdict === 'no_go') return 'Pas de fenêtre';
  if (f.mois_avant_arrivee !== null) return `${f.mois_avant_arrivee} mois`;
  return f.libelle ?? 'À estimer';
}

type Etat = {
  trends: Tendance[];
  courante: Tendance | undefined;
  choisir: (id: number) => void;
  historique: (id: number) => { semaine: string; volume: number }[];
  simulees: boolean;
  demo: boolean;
  source: 'data' | 'api' | 'demo';
  clients: ClientVue[];
  compatibilite: (t: Tendance, clientId: string) => Compatibilite;
  cleData: (t: Tendance) => string | undefined;
};
const Contexte = createContext<Etat | null>(null);

export function useDonnees() {
  const etat = useContext(Contexte);
  if (!etat)
    throw new Error('useDonnees doit être utilisé dans <DonneesProvider>');
  return etat;
}

function niveauExemple(score: number) {
  return score >= 80
    ? 'Priorité'
    : score >= 65
      ? 'Bon potentiel'
      : 'À explorer';
}

function compatibiliteExemple(nom: string, clientId: string): Compatibilite {
  const score = fitExemple(nom, clientId);
  return {
    score,
    niveau: niveauExemple(score),
    exemple: true,
    clientSimule: true,
    criteres: {
      marque: fitExemple(`${nom}-marque`, clientId),
      portefeuille: fitExemple(`${nom}-portefeuille`, clientId),
      marche: fitExemple(`${nom}-marche`, clientId),
      agir: fitExemple(`${nom}-agir`, clientId),
    },
  };
}

// Critères du Client Fit du modèle data (RG07) vers les 4 cartes de l'écran
function noteCritere(
  fit: MatchData['fit_criteria'],
  cles: string[],
): number | null {
  for (const cle of cles) {
    const v = fit[cle];
    const n =
      typeof v === 'number' ? v : v && typeof v === 'object' ? v.value : null;
    if (typeof n === 'number') return Math.round(n);
  }
  return null;
}

export function DonneesProvider({ children }: { children: ReactNode }) {
  const [trends, setTrends] = useState<Tendance[] | null>(null);
  const [erreur, setErreur] = useState('');
  const [idCourant, setIdCourant] = useState<number | null>(null);
  const [historiques, setHistoriques] = useState<
    Record<number, { semaine: string; volume: number }[]>
  >({});
  const [clientsData, setClientsData] = useState<ClientData[]>([]);
  const [matches, setMatches] = useState<MatchData[]>([]);
  const [cles, setCles] = useState<Record<number, string>>({});
  const [source, setSource] = useState<'data' | 'api' | 'demo'>('demo');
  const modeDemo = useRef(false);

  useEffect(() => {
    // Ordre : 1. l'API du data (/data-api), 2. notre API (/api), 3. le jeu fictif embarqué.
    const lireJson = async <T,>(url: string): Promise<T> => {
      const r = await fetch(url);
      if (!r.ok || !r.headers.get('content-type')?.includes('json'))
        throw new Error(`Erreur ${r.status} sur ${url}`);
      return (await r.json()) as T;
    };
    // Notre API peut ne pas encore renvoyer les champs ajoutés pour le modèle data :
    // on complète avec des valeurs neutres au lieu de planter l'écran.
    const normaliser = (t: TendanceApi): TendanceApi => ({
      ...t,
      pays_en_hausse: t.pays_en_hausse ?? [],
      criteres: (t.criteres ?? []).map((c) => ({
        ...c,
        disponible: c.disponible ?? c.note !== null,
      })),
      fenetre_tir: t.fenetre_tir
        ? { ...t.fenetre_tir, libelle: t.fenetre_tir.libelle ?? null }
        : null,
    });
    const depuisApi = async () => {
      const liste = await lireJson<TendanceApi[]>('/api/trends?limit=20');
      const details = await Promise.all(
        liste.map((t) =>
          lireJson<TendanceApi>(`/api/trends/${t.id}`).catch(() => ({
            ...t,
            historique: [],
          })),
        ),
      );
      return details.map(normaliser);
    };
    const depuisDemo = () =>
      lireJson<TendanceApi[]>(`${import.meta.env.BASE_URL}donnees-demo.json`);

    const afficher = (
      liste: TendanceApi[],
      meilleur: (t: TendanceApi) => string,
    ) => {
      const vues = liste.map((t, i) => versVue(t, i, meilleur));
      setTrends(vues);
      setIdCourant(vues[0]?.id ?? null);
      setHistoriques(
        Object.fromEntries(liste.map((t) => [t.id, t.historique ?? []])),
      );
    };
    const meilleurExemple = (t: TendanceApi) =>
      [...CLIENTS].sort(
        (a, b) => fitExemple(t.nom, b.id) - fitExemple(t.nom, a.id),
      )[0].name;

    chargerDepuisData()
      .then(({ tendances, clients, matches: m, cles: c }) => {
        if (!tendances.length) throw new Error('aucune tendance notée');
        setSource('data');
        setClientsData(clients);
        setMatches(m);
        setCles(c);
        const libelles = Object.fromEntries(
          clients.map((x) => [x.id, x.label]),
        );
        afficher(tendances, (t) => {
          const meilleur = m
            .filter((x) => x.trend_id === c[t.id])
            .sort(
              (a, b) => (b.opportunity_score ?? 0) - (a.opportunity_score ?? 0),
            )[0];
          return meilleur
            ? (libelles[meilleur.client_id] ?? meilleur.client_id)
            : '—';
        });
      })
      .catch(() =>
        depuisApi()
          .then((liste) => {
            setSource('api');
            afficher(liste, meilleurExemple);
          })
          .catch(() =>
            depuisDemo().then((liste) => {
              modeDemo.current = true;
              setSource('demo');
              afficher(liste, meilleurExemple);
            }),
          ),
      )
      .catch((e: Error) => setErreur(e.message));
  }, []);

  if (erreur) {
    return (
      <div className="chargement">
        <h1>Aucune donnée disponible</h1>
        <p>{erreur}</p>
        <p>Lancer l'API : dans le dossier hackathon-test, docker compose up.</p>
      </div>
    );
  }
  if (!trends)
    return (
      <div className="chargement">
        <p>Chargement des tendances…</p>
      </div>
    );

  const clients: ClientVue[] =
    source === 'data' && clientsData.length
      ? clientsData.map((c) => ({
          id: c.id,
          name: c.label,
          type: c.brand_positioning ?? c.sector,
          country: c.active_markets.join(', ') || '—',
          simule: c.client_data_source !== 'reel',
        }))
      : CLIENTS;

  const compatibilite = (t: Tendance, clientId: string): Compatibilite => {
    if (source !== 'data') return compatibiliteExemple(t.name, clientId);
    const m = matches
      .filter((x) => x.trend_id === cles[t.id] && x.client_id === clientId)
      .sort(
        (a, b) => (b.opportunity_score ?? 0) - (a.opportunity_score ?? 0),
      )[0];
    if (!m || m.fit_score === null)
      return {
        score: 0,
        niveau: 'Non disponible',
        exemple: false,
        clientSimule: true,
        criteres: {},
      };
    return {
      score: Math.round(m.fit_score),
      niveau: m.fit_level ?? '—',
      exemple: false,
      clientSimule: m.client_data_source !== 'reel',
      criteres: {
        marque: noteCritere(m.fit_criteria, [
          'brand_alignment',
          'alignement_marque',
          'marque',
        ]),
        portefeuille: noteCritere(m.fit_criteria, [
          'portfolio',
          'portefeuille',
        ]),
        marche: noteCritere(m.fit_criteria, ['market', 'marche']),
        agir: noteCritere(m.fit_criteria, [
          'actionability',
          'capacite_a_agir',
          'agir',
        ]),
      },
    };
  };

  const valeur: Etat = {
    trends,
    courante: trends.find((t) => t.id === idCourant) ?? trends[0],
    choisir: setIdCourant,
    historique: (id) => historiques[id] ?? [],
    simulees: trends.some((t) => t.api.donnees_simulees),
    demo: modeDemo.current,
    source,
    clients,
    compatibilite,
    cleData: (t) => cles[t.id],
  };
  return <Contexte.Provider value={valeur}>{children}</Contexte.Provider>;
}
