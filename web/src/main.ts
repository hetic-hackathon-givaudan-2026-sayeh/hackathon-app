import './style.css';

// Types alignés sur les schémas de openapi.yaml (Tendance, Critere, FenetreTir, PointHistorique)
type Verdict = 'buzz' | 'emergente' | 'durable';
type VerdictFenetre = 'go' | 'urgence' | 'no_go';

type Critere = {
  nom: string;
  poids: number | null;
  note: number | null;
  source: string | null;
};

type FenetreTir = {
  verdict: VerdictFenetre;
  mois_avant_arrivee: number | null;
  cycle_givaudan_mois: number | null;
};

type Tendance = {
  id: number;
  nom: string;
  profil_aromatique: string[];
  verdict: Verdict;
  score_durabilite: number;
  score_opportunite: number;
  explication: string | null;
  marche_source: string | null;
  marche_cible: string | null;
  fenetre_tir: FenetreTir | null;
  criteres: Critere[];
  donnees_simulees: boolean;
};

type Point = { semaine: string; volume: number };
type TendanceDetail = Tendance & { historique: Point[] };

const LIBELLE_VERDICT: Record<Verdict, string> = {
  buzz: 'Buzz',
  emergente: 'Émergente',
  durable: 'Tendance durable',
};

const LIBELLE_FENETRE: Record<VerdictFenetre, string> = {
  go: 'GO',
  urgence: 'URGENCE',
  no_go: 'NO-GO',
};

const listeEl = document.querySelector<HTMLTableSectionElement>('#liste')!;
const etatEl = document.querySelector<HTMLParagraphElement>('#etat-liste')!;
const kpisEl = document.querySelector<HTMLElement>('#kpis')!;
const detailEl = document.querySelector<HTMLElement>('#detail')!;
const bandeauEl = document.querySelector<HTMLElement>('#bandeau-simule')!;
const secteurEl = document.querySelector<HTMLSelectElement>('#secteur')!;

// Les détails (dont l'historique) sont gardés pour ne pas les redemander à chaque clic
const details = new Map<number, TendanceDetail>();
let selection: number | null = null;

// Toujours textContent, jamais innerHTML : les textes viennent de la base
function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  classe?: string,
  texte?: string,
): HTMLElementTagNameMap[K] {
  const noeud = document.createElement(tag);
  if (classe) noeud.className = classe;
  if (texte !== undefined) noeud.textContent = texte;
  return noeud;
}

function badge(classe: string, texte: string) {
  return el('span', `badge ${classe}`, texte);
}

function barre(valeur: number | null, large = false) {
  const piste = el('div', `piste${large ? ' large' : ''}`);
  if (valeur === null) {
    piste.classList.add('a-definir');
    return piste;
  }
  const remplie = el('div', 'remplie');
  remplie.style.width = `${valeur}%`;
  piste.append(remplie);
  return piste;
}

function marches(t: Tendance) {
  return t.marche_source && t.marche_cible
    ? `${t.marche_source} → ${t.marche_cible}`
    : '—';
}

function moisAnnee(iso: string) {
  return new Date(iso).toLocaleDateString('fr-FR', {
    month: 'short',
    year: 'numeric',
  });
}

// Courbe SVG : une sparkline dans le tableau, une courbe pleine dans le détail
function courbe(
  points: Point[],
  largeur: number,
  hauteur: number,
  aire: boolean,
) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${largeur} ${hauteur}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', "Évolution de l'intérêt dans le temps");
  if (points.length < 2) return svg;

  const max = Math.max(...points.map((p) => p.volume), 1);
  const marge = 3;
  const coords = points.map((p, i) => {
    const x = (i / (points.length - 1)) * largeur;
    const y = hauteur - marge - (p.volume / max) * (hauteur - 2 * marge);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  if (aire) {
    const fond = document.createElementNS(ns, 'polygon');
    fond.setAttribute(
      'points',
      `0,${hauteur} ${coords.join(' ')} ${largeur},${hauteur}`,
    );
    fond.setAttribute('fill', '#eaf6e4');
    svg.append(fond);
  }
  const ligne = document.createElementNS(ns, 'polyline');
  ligne.setAttribute('points', coords.join(' '));
  ligne.setAttribute('fill', 'none');
  ligne.setAttribute('stroke', '#5bbf3e');
  ligne.setAttribute('stroke-width', aire ? '2.5' : '2');
  ligne.setAttribute('vector-effect', 'non-scaling-stroke');
  svg.append(ligne);
  return svg;
}

async function lireJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  const corps = await res.json();
  if (!res.ok) throw new Error(corps.error ?? `Erreur ${res.status}`);
  return corps as T;
}

async function chargerDetail(id: number) {
  const connu = details.get(id);
  if (connu) return connu;
  const detail = await lireJson<TendanceDetail>(`/api/trends/${id}`);
  details.set(id, detail);
  return detail;
}

function afficherKpis(tendances: Tendance[]) {
  const durables = tendances.filter((t) => t.verdict === 'durable').length;
  const alertes = tendances.filter(
    (t) => t.fenetre_tir?.verdict === 'urgence',
  ).length;
  const kpis: [string, number, string][] = [
    ['Signaux détectés', tendances.length, 'sur la période'],
    ['Tendances durables', durables, 'à ne pas confondre avec un buzz'],
    ['Alertes urgentes', alertes, 'fenêtre de tir qui se referme'],
  ];
  kpisEl.replaceChildren(
    ...kpis.map(([titre, valeur, legende]) => {
      const carte = el('div', 'carte kpi');
      carte.append(
        el('p', 'legende', titre),
        el('p', 'valeur', String(valeur)),
        el('p', 'legende', legende),
      );
      return carte;
    }),
  );
}

function ligneTendance(t: Tendance) {
  const tr = el('tr');
  tr.tabIndex = 0;
  tr.dataset.id = String(t.id);
  tr.setAttribute('aria-selected', String(t.id === selection));

  const nom = el('td');
  nom.append(
    el('div', 'nom', t.nom),
    el('div', 'legende', t.profil_aromatique.slice(0, 3).join(' · ')),
  );

  const tendanceCourbe = el('td');
  tendanceCourbe.dataset.sparkline = String(t.id);

  const score = el('td');
  const blocScore = el('div', 'score');
  blocScore.append(
    barre(t.score_opportunite),
    el('span', '', String(t.score_opportunite)),
  );
  score.append(blocScore);

  const fenetre = el('td');
  if (t.fenetre_tir) {
    fenetre.append(
      badge(t.fenetre_tir.verdict, LIBELLE_FENETRE[t.fenetre_tir.verdict]),
    );
  }

  const nature = el('td');
  nature.append(badge(t.verdict, LIBELLE_VERDICT[t.verdict]));

  tr.append(
    nom,
    el('td', '', marches(t)),
    tendanceCourbe,
    nature,
    score,
    fenetre,
  );
  tr.addEventListener('click', () => selectionner(t.id, true));
  tr.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      selectionner(t.id, true);
    }
  });
  return tr;
}

function phraseFenetre(f: FenetreTir) {
  if (f.verdict === 'no_go' || f.mois_avant_arrivee === null) {
    return "Pas de fenêtre de tir : ce n'est pas une tendance à suivre pour l'instant.";
  }
  const marge = f.mois_avant_arrivee - (f.cycle_givaudan_mois ?? 0);
  if (f.verdict === 'urgence') {
    return `Il reste environ ${marge} mois de marge : il faut briefer les clients maintenant.`;
  }
  return `Environ ${marge} mois de marge : le temps de développer le produit sereinement.`;
}

function blocFenetre(f: FenetreTir | null) {
  const carte = el('div', 'carte');
  carte.append(el('h2', '', 'Fenêtre de tir'));
  if (!f) {
    carte.append(el('p', 'etat', 'Pas encore évaluée.'));
    return carte;
  }
  if (f.mois_avant_arrivee !== null && f.cycle_givaudan_mois !== null) {
    const max = Math.max(f.mois_avant_arrivee, f.cycle_givaudan_mois);
    const frise = el('div', 'frise');
    const lignes: [string, number, string][] = [
      ['Arrivée sur le marché cible', f.mois_avant_arrivee, 'var(--vert-vif)'],
      ['Temps de cycle Givaudan', f.cycle_givaudan_mois, 'var(--vert-fonce)'],
    ];
    for (const [libelle, mois, couleur] of lignes) {
      const ligne = el('div', 'frise-ligne');
      const b = el('div', 'barre');
      b.style.width = `${(mois / max) * 100}%`;
      b.style.background = couleur;
      const conteneur = el('div');
      conteneur.append(b);
      ligne.append(
        el('span', 'legende', libelle),
        conteneur,
        el('strong', '', `${mois} mois`),
      );
      frise.append(ligne);
    }
    carte.append(frise);
  }
  carte.append(
    el('div', `verdict-geant ${f.verdict}`, LIBELLE_FENETRE[f.verdict]),
    el('p', '', phraseFenetre(f)),
  );
  return carte;
}

function blocCriteres(criteres: Critere[]) {
  const carte = el('div', 'carte');
  carte.append(el('h2', '', 'Pourquoi ce score'));
  for (const c of criteres) {
    const aDefinir = c.poids === null;
    const bloc = el('div', `critere${aDefinir ? ' a-definir' : ''}`);
    const haut = el('div', 'critere-haut');
    haut.append(
      el('span', '', `${c.nom}${c.poids !== null ? ` · ${c.poids} %` : ''}`),
      el('span', '', c.note !== null ? String(c.note) : '—'),
    );
    const bas = el('div', 'critere-bas');
    bas.append(
      el(
        'span',
        '',
        aDefinir ? 'poids et calcul en cours de validation' : 'note sur 100',
      ),
      el('span', '', c.source ?? 'source à définir'),
    );
    bloc.append(haut, barre(c.note, true), bas);
    carte.append(bloc);
  }
  return carte;
}

function afficherDetail(t: TendanceDetail) {
  const entete = el('div', 'detail-entete');
  const titre = el('div');
  titre.append(
    el('p', 'legende', `${marches(t)} · ${t.profil_aromatique.join(', ')}`),
    el('h1', '', t.nom),
    badge(t.verdict, LIBELLE_VERDICT[t.verdict]),
  );
  const score = el('div');
  const geant = el('div', 'score-geant', String(t.score_durabilite));
  geant.append(el('small', '', '/100'));
  score.append(
    geant,
    el('p', 'legende', 'score de durabilité'),
    el(
      'p',
      'legende',
      `opportunité pour Givaudan : ${t.score_opportunite}/100`,
    ),
  );
  entete.append(titre, score);

  const carteCourbe = el('div', 'carte');
  const premier = t.historique[0];
  const dernier = t.historique[t.historique.length - 1];
  carteCourbe.append(
    el('h2', '', `Intérêt sur ${Math.round(t.historique.length / 52)} ans`),
    courbe(t.historique, 600, 220, true),
  );
  if (premier && dernier) {
    const axe = el('div', 'critere-bas');
    axe.append(
      el('span', '', moisAnnee(premier.semaine)),
      el('span', '', moisAnnee(dernier.semaine)),
    );
    carteCourbe.append(axe);
  }
  carteCourbe.querySelector('svg')?.classList.add('courbe');

  const grille = el('div', 'grille');
  grille.append(carteCourbe, blocCriteres(t.criteres));

  const blocs: HTMLElement[] = [entete, grille, blocFenetre(t.fenetre_tir)];
  if (t.explication) {
    const explication = el('div', 'explication');
    explication.append(
      el('strong', '', 'Explication'),
      el('p', '', t.explication),
    );
    if (t.donnees_simulees)
      explication.append(
        el('p', 'legende', "Texte d'exemple (données simulées)."),
      );
    blocs.push(explication);
  }
  detailEl.replaceChildren(...blocs);
  detailEl.hidden = false;
}

async function selectionner(id: number, defiler = false) {
  selection = id;
  for (const tr of listeEl.querySelectorAll('tr')) {
    tr.setAttribute('aria-selected', String(tr.dataset.id === String(id)));
  }
  try {
    afficherDetail(await chargerDetail(id));
    if (defiler)
      detailEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    detailEl.hidden = false;
    detailEl.replaceChildren(
      el('p', 'etat', `Détail indisponible : ${(err as Error).message}`),
    );
  }
}

async function chargerListe() {
  etatEl.hidden = false;
  etatEl.textContent = 'Chargement des tendances…';
  const secteur = secteurEl.value;
  const params = new URLSearchParams({ limit: '20' });
  if (secteur) params.set('secteur', secteur);

  try {
    const tendances = await lireJson<Tendance[]>(`/api/trends?${params}`);
    bandeauEl.hidden = !tendances.some((t) => t.donnees_simulees);
    afficherKpis(tendances);
    if (tendances.length === 0) {
      listeEl.replaceChildren();
      etatEl.textContent = 'Aucune tendance pour ce secteur.';
      detailEl.hidden = true;
      return;
    }
    etatEl.hidden = true;
    if (!tendances.some((t) => t.id === selection)) selection = tendances[0].id;
    listeEl.replaceChildren(...tendances.map(ligneTendance));
    await selectionner(selection!);

    // Sparklines : 52 dernières semaines de chaque tendance
    await Promise.all(
      tendances.map(async (t) => {
        const detail = await chargerDetail(t.id);
        const cellule = listeEl.querySelector(`[data-sparkline="${t.id}"]`);
        cellule?.replaceChildren(
          courbe(detail.historique.slice(-52), 130, 32, false),
        );
      }),
    );
  } catch (err) {
    etatEl.hidden = false;
    etatEl.textContent = `Impossible de charger les tendances : ${(err as Error).message}`;
  }
}

secteurEl.addEventListener('change', chargerListe);
chargerListe();
