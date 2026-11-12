import { useMemo, useState } from 'react';
import { estAlerte, texteFenetre, useDonnees } from './donnees';
import { genererPitchData } from './apiData';
import { Globe } from './Globe';
import {
  ArrowRight,
  Bell,
  BookOpen,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Download,
  Filter,
  FlaskConical,
  FolderKanban,
  Leaf,
  LineChart,
  MapPin,
  Menu,
  MessageCircleMore,
  MoreHorizontal,
  PanelLeftClose,
  PenLine,
  Radar,
  Search,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  Users,
  X,
} from 'lucide-react';

type View = 'feed' | 'radar' | 'evidence' | 'matching' | 'pitch';

const navigation: { id: View; label: string; icon: typeof Radar }[] = [
  { id: 'feed', label: 'Opportunités', icon: Sparkles },
  { id: 'radar', label: 'Live Trend Radar', icon: Radar },
  { id: 'evidence', label: 'Preuves', icon: LineChart },
  { id: 'matching', label: 'Portefeuille clients', icon: Users },
  { id: 'pitch', label: 'Pitch Studio', icon: PenLine },
];

function BrandMark({ onClick }: { onClick: () => void }) {
  return (
    <button
      className="brand-mark"
      aria-label="Retour aux opportunités"
      onClick={onClick}
    >
      <Leaf size={23} strokeWidth={1.4} />
      <span>TRENDPULSE</span>
    </button>
  );
}

function Sidebar({
  view,
  onChange,
  collapsed,
  onCollapse,
  notify,
}: {
  view: View;
  onChange: (view: View) => void;
  collapsed: boolean;
  onCollapse: () => void;
  notify: (message: string) => void;
}) {
  return (
    <aside className={`sidebar ${collapsed ? 'is-collapsed' : ''}`}>
      <div>
        <BrandMark onClick={() => onChange('feed')} />
        <nav className="main-nav" aria-label="Navigation principale">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={view === item.id ? 'active' : ''}
                onClick={() => onChange(item.id)}
                title={item.label}
              >
                <Icon size={19} strokeWidth={1.6} />
                <span>{item.label}</span>
                {view === item.id && <i />}
              </button>
            );
          })}
        </nav>
      </div>
      <div className="side-bottom">
        <button
          className="side-icon"
          aria-label="Notifications"
          onClick={() =>
            notify('Vous avez 3 nouvelles opportunités à consulter')
          }
        >
          <Bell size={18} />
        </button>
        <div className="avatar">CF</div>
        <div className="profile-copy">
          <strong>Commercial</strong>
          <span>Givaudan France</span>
        </div>
        <button
          className="side-icon collapse-button"
          aria-label="Réduire le menu"
          onClick={onCollapse}
        >
          <PanelLeftClose size={18} />
        </button>
      </div>
    </aside>
  );
}

function Topbar({
  view,
  onMenu,
  onSearch,
  notify,
}: {
  view: View;
  onMenu: () => void;
  onSearch: () => void;
  notify: (message: string) => void;
}) {
  const { courante, trends, simulees } = useDonnees();
  const nom = courante?.name ?? '';
  const labels: Record<View, string> = {
    feed: 'Opportunity Feed',
    radar: 'Live Trend Radar',
    evidence: `${nom} · Preuves`,
    matching: `${nom} · Client Matching`,
    pitch: `${nom} · Pitch Studio`,
  };
  const maj = trends
    .map((t) => t.api.maj_le)
    .sort()
    .pop();
  const alertes = trends.filter(estAlerte).length;
  return (
    <header className="topbar">
      <div className="topbar-title">
        <button
          className="menu-button"
          onClick={onMenu}
          aria-label="Afficher ou masquer le menu"
        >
          <Menu size={19} />
        </button>
        <span className="live-dot" />
        <span>{labels[view]}</span>
        <small>
          {simulees
            ? 'Données simulées (jeu de démonstration)'
            : `Données du ${maj ? new Date(maj).toLocaleDateString('fr-FR') : '—'}`}
        </small>
      </div>
      <div className="top-actions">
        <button className="search-button" onClick={onSearch}>
          <Search size={17} />
          <span>Rechercher une tendance, un client...</span>
          <kbd>⌘ K</kbd>
        </button>
        <button
          className="icon-button"
          aria-label="Messages"
          onClick={() => notify('Aucun nouveau message')}
        >
          <MessageCircleMore size={18} />
        </button>
        <button
          className="icon-button notification"
          aria-label="Notifications"
          onClick={() =>
            notify(
              `${alertes} opportunité${alertes > 1 ? 's' : ''} avec une fenêtre GO ou URGENCE`,
            )
          }
        >
          <Bell size={18} />
          <i />
        </button>
      </div>
    </header>
  );
}

function Tag({
  children,
  tone = 'neutral',
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return <span className={`tag tag-${tone}`}>{children}</span>;
}

function Feed({
  go,
  notify,
}: {
  go: (view: View) => void;
  notify: (message: string) => void;
}) {
  const [selected, setSelected] = useState(0);
  const { trends, choisir, courante } = useDonnees();
  const pretes = trends.filter(estAlerte).length;
  const enValidation = trends.filter((t) =>
    ['a_surveiller', 'emergente'].includes(t.api.verdict),
  ).length;
  const raisons = (courante?.api.criteres ?? [])
    .filter((c) => c.disponible && c.note !== null)
    .slice(0, 3);
  const { simulees, demo } = useDonnees();
  const alertes = trends.filter(estAlerte).slice(0, 3);
  const [popupOuverte, setPopupOuverte] = useState(() => {
    try {
      return !sessionStorage.getItem('trendpulse-popup-fermee');
    } catch {
      return true;
    }
  });
  const fermerPopup = () => {
    setPopupOuverte(false);
    try {
      sessionStorage.setItem('trendpulse-popup-fermee', '1');
    } catch {
      // stockage indisponible : la pop-up reviendra à la prochaine visite
    }
  };
  const moyenne = trends.length
    ? Math.round(trends.reduce((s, t) => s + t.score, 0) / trends.length)
    : 0;
  const marchesSource = trends.flatMap(
    (t) =>
      [t.api.marche_source, ...t.api.pays_en_hausse].filter(
        Boolean,
      ) as string[],
  );
  const marchesCible = trends
    .map((t) => t.api.marche_cible)
    .filter(Boolean) as string[];
  const trajets = trends
    .filter((t) => t.api.marche_source && t.api.marche_cible)
    .map((t) => [t.api.marche_source, t.api.marche_cible] as [string, string]);
  return (
    <div className="page feed-page">
      <section className="feed-main">
        <div className="page-heading">
          <div>
            <p className="eyebrow">
              Vue du jour ·{' '}
              {new Date().toLocaleDateString('fr-FR', {
                day: 'numeric',
                month: 'long',
                year: 'numeric',
              })}
            </p>
            <h1>Les opportunités qui méritent d’avancer.</h1>
            <p className="subtitle">
              Des signaux qualifiés par l’IA, mis en regard de votre
              portefeuille client.
            </p>
          </div>
          <button
            className="outline-button"
            onClick={() =>
              notify('Les tendances sont triées par score d’opportunité')
            }
          >
            <Filter size={16} /> Personnaliser le feed
          </button>
        </div>

        {simulees && (
          <p className="bandeau-tp">
            Données fictives : jeu de démonstration, critères en cours de
            validation.
            {demo &&
              ' Version en ligne : l’IA locale n’est pas disponible, le pitch utilise un gabarit.'}
          </p>
        )}
        <div className="kpis-tp">
          <div className="kpi-tp">
            <small>Tendances suivies</small>
            <strong>{trends.length}</strong>
            <span>dans la base</span>
          </div>
          <div className="kpi-tp">
            <small>Prêtes à pitcher</small>
            <strong>{trends.filter(estAlerte).length}</strong>
            <span>fenêtre GO ou URGENCE</span>
          </div>
          <div className="kpi-tp">
            <small>Durabilité moyenne</small>
            <strong>{moyenne}</strong>
            <span>sur 100</span>
          </div>
          <div className="kpi-tp">
            <small>Meilleure tendance</small>
            <strong>{trends[0]?.name ?? '—'}</strong>
            <span>score {trends[0]?.score ?? '—'}/100</span>
          </div>
        </div>
        <div className="opportunity-list">
          {trends.map((trend, index) => (
            <article
              className={`opportunity-card ${selected === index ? 'selected' : ''}`}
              key={trend.name}
              onClick={() => {
                setSelected(index);
                choisir(trend.id);
              }}
            >
              <div className="opportunity-image">
                <img src={trend.image} alt="" />
                {index === 0 && <span className="new-label">NOUVEAU</span>}
              </div>
              <div className="opportunity-copy">
                <div className="card-title-line">
                  <div>
                    {index === 0 && (
                      <p className="confidence">
                        <Sparkles size={13} /> Score le plus élevé
                      </p>
                    )}
                    <h2>{trend.name}</h2>
                  </div>
                  <div className="momentum">
                    <TrendingUp size={14} />
                    <span>Fenêtre : {texteFenetre(trend)}</span>
                    <strong>
                      {trend.score}
                      <small>/100</small>
                    </strong>
                  </div>
                </div>
                <div className="tag-row">
                  <Tag tone={index === 0 ? 'wine' : 'lilac'}>
                    {trend.category}
                  </Tag>
                  <Tag tone="sage">{trend.maturity}</Tag>
                  <Tag>{trend.market}</Tag>
                </div>
                <div className="client-fit">
                  <div className="fit-icon">
                    <Target size={17} />
                  </div>
                  <div>
                    <strong>Client à appeler : {trend.client} (exemple)</strong>
                    <p>
                      {trend.api.explication ??
                        'Explication à venir du modèle data.'}
                    </p>
                  </div>
                  <button
                    className={
                      index === 0 ? 'primary-button compact' : 'circle-arrow'
                    }
                    onClick={(event) => {
                      event.stopPropagation();
                      choisir(trend.id);
                      go('evidence');
                    }}
                  >
                    {index === 0 ? (
                      <>
                        Étudier l’opportunité <ArrowRight size={15} />
                      </>
                    ) : (
                      <ArrowRight size={17} />
                    )}
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>

      <aside className="insight-rail">
        <div className="rail-header">
          <span className="live-dot" /> En direct · Signaux en temps réel
        </div>
        <section>
          <h3>Pulse portefeuille</h3>
          <button className="pulse-item" onClick={() => go('feed')}>
            <span className="pulse-icon wine">
              <Sparkles />
            </span>
            <strong>{trends.length}</strong>
            <p>
              Tendances suivies<small>DANS LA BASE</small>
            </p>
            <ChevronRight />
          </button>
          <button className="pulse-item" onClick={() => go('pitch')}>
            <span className="pulse-icon sage">
              <ArrowRight />
            </span>
            <strong>{pretes}</strong>
            <p>
              Prêtes à pitcher<small>FENÊTRE GO OU URGENCE</small>
            </p>
            <ChevronRight />
          </button>
          <button className="pulse-item" onClick={() => go('evidence')}>
            <span className="pulse-icon lilac">
              <Search />
            </span>
            <strong>{enValidation}</strong>
            <p>
              Signaux en validation<small>ÉMERGENTS OU À SURVEILLER</small>
            </p>
            <ChevronRight />
          </button>
        </section>
        <section className="carte-tp">
          <h3>Où ça monte</h3>
          <Globe
            sources={marchesSource}
            cibles={marchesCible}
            arcs={trajets}
            hauteur={240}
          />
        </section>
        <section>
          <h3>Pourquoi cette opportunité ?</h3>
          {raisons.map((c) => (
            <div className="reason" key={c.nom}>
              <TrendingUp />
              <p>
                <strong>
                  {c.nom} · {c.note}/100
                </strong>
                <span>Source : {c.source}</span>
              </p>
            </div>
          ))}
          <div className="reason">
            <CalendarDays />
            <p>
              <strong>Timing marché</strong>
              <span>{courante ? texteFenetre(courante) : 'À estimer'}</span>
            </p>
          </div>
        </section>
        <button className="text-link" onClick={() => go('radar')}>
          Voir comment nous priorisons <ArrowRight size={15} />
        </button>
      </aside>
      {popupOuverte && alertes.length > 0 && (
        <aside
          className="popup-tp"
          role="dialog"
          aria-label="Alertes prioritaires"
        >
          <header>
            <strong>
              <Bell size={15} /> {alertes.length} alerte
              {alertes.length > 1 ? 's' : ''} à traiter
            </strong>
            <button aria-label="Fermer" onClick={fermerPopup}>
              <X size={16} />
            </button>
          </header>
          {alertes.map((t) => (
            <button
              className="ligne"
              key={t.id}
              onClick={() => {
                choisir(t.id);
                fermerPopup();
                go('evidence');
              }}
            >
              <img src={t.image} alt="" />
              <span>
                <strong>{t.name}</strong>
                <small>
                  Durabilité {t.score}/100 · fenêtre {texteFenetre(t)}
                </small>
              </span>
              <ChevronRight size={16} />
            </button>
          ))}
        </aside>
      )}
    </div>
  );
}

function MiniSparkline({ color = '#0a6558' }: { color?: string }) {
  return (
    <svg
      className="sparkline"
      viewBox="0 0 100 34"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path
        d="M1 29 L10 24 L18 26 L27 14 L36 17 L45 8 L53 13 L62 5 L70 12 L79 7 L88 10 L99 2"
        fill="none"
        stroke={color}
        strokeWidth="2"
      />
    </svg>
  );
}

function RadarView({
  go,
  notify,
}: {
  go: (view: View) => void;
  notify: (message: string) => void;
}) {
  const { trends, choisir } = useDonnees();
  const [query, setQuery] = useState('');
  const [country, setCountry] = useState('Tous les pays');
  const [fragranceOnly, setFragranceOnly] = useState(false);
  const [maturityFilter, setMaturityFilter] = useState(false);
  const [growthSort, setGrowthSort] = useState(false);
  const [descending, setDescending] = useState(true);
  const visible = useMemo(() => {
    const filtered = trends.filter(
      (t) =>
        t.name.toLowerCase().includes(query.toLowerCase()) &&
        (country === 'Tous les pays' || t.market === country) &&
        (!fragranceOnly || t.category === 'Parfumerie') &&
        (!maturityFilter || t.maturity === 'Émergente'),
    );
    return [...filtered].sort((a, b) =>
      descending ? b.score - a.score : a.score - b.score,
    );
  }, [query, country, fragranceOnly, maturityFilter, descending]);
  return (
    <div className="page simple-page radar-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Détection temps réel</p>
          <h1>Live Trend Radar</h1>
          <p className="subtitle">
            Explorez, filtrez et priorisez les signaux qui progressent sur vos
            marchés.
          </p>
        </div>
        <button
          className="date-pill"
          onClick={() =>
            notify('Période : toutes les données disponibles dans la base')
          }
        >
          <CalendarDays size={16} /> Toutes les données{' '}
          <ChevronDown size={15} />
        </button>
      </div>
      <div className="filter-bar">
        <label className="field-search">
          <Search size={17} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Rechercher parmi ${trends.length} tendances...`}
          />
        </label>
        <label className="select-field">
          <MapPin size={16} />
          <select value={country} onChange={(e) => setCountry(e.target.value)}>
            <option>Tous les pays</option>
            {[...new Set(trends.map((t) => t.market))].map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
          <ChevronDown size={14} />
        </label>
        <button
          className={`filter-chip ${fragranceOnly ? 'active' : ''}`}
          onClick={() => setFragranceOnly(!fragranceOnly)}
        >
          Parfumerie {fragranceOnly ? <X size={13} /> : <Check size={13} />}
        </button>
        <button
          className={`filter-chip ${maturityFilter ? 'active' : ''}`}
          onClick={() => setMaturityFilter(!maturityFilter)}
        >
          {maturityFilter ? 'Maturité : émergente' : 'Maturité : toutes'}{' '}
          <ChevronDown size={13} />
        </button>
        <button
          className={`filter-chip ${growthSort ? 'active' : ''}`}
          onClick={() => {
            setGrowthSort(!growthSort);
            setDescending(!growthSort);
          }}
        >
          {growthSort ? 'Croissance : décroissante' : 'Croissance'}{' '}
          <ChevronDown size={13} />
        </button>
        <button
          className="sliders-button"
          onClick={() =>
            notify('Filtres avancés : sources, confiance et fenêtre marché')
          }
        >
          <Filter size={16} /> Filtres avancés
        </button>
      </div>
      <div className="radar-body">
        <section className="trend-table-wrap">
          <div className="table-meta">
            <span>{visible.length} tendances affichées</span>
            <button onClick={() => setDescending(!descending)}>
              Tri : score {descending ? 'décroissant' : 'croissant'}{' '}
              <ChevronDown size={13} />
            </button>
          </div>
          <div className="trend-table">
            <div className="trend-row table-head">
              <span>Tendance</span>
              <span>Pays</span>
              <span>Maturité</span>
              <span>Croissance</span>
              <span>Durabilité</span>
              <span>Sources</span>
              <span />
            </div>
            {visible.map((trend) => (
              <button
                className="trend-row"
                key={trend.name}
                onClick={() => {
                  choisir(trend.id);
                  go('evidence');
                }}
              >
                <span className="trend-name">
                  <img src={trend.image} alt="" />
                  <strong>{trend.name}</strong>
                  <small>{trend.category}</small>
                </span>
                <span>{trend.market}</span>
                <span>
                  <Tag tone={trend.maturity === 'Émergente' ? 'sage' : 'sand'}>
                    {trend.maturity}
                  </Tag>
                </span>
                <span className="growth">
                  <MiniSparkline />
                  {trend.growth}
                </span>
                <span>
                  <i
                    className="score-ring"
                    style={
                      {
                        '--score': `${trend.score * 3.6}deg`,
                      } as React.CSSProperties
                    }
                  >
                    {trend.score}
                  </i>
                </span>
                <span>{trend.sources}</span>
                <span className="view-action">
                  Voir <ArrowRight size={14} />
                </span>
              </button>
            ))}
          </div>
        </section>
        <aside className="priority-panel">
          <div className="panel-title">
            <div>
              <p className="eyebrow">Sélection IA</p>
              <h3>Signaux prioritaires</h3>
            </div>
            <Sparkles size={20} />
          </div>
          {trends.slice(0, 3).map((trend, i) => (
            <button
              className="priority-card"
              key={trend.name}
              onClick={() => {
                choisir(trend.id);
                go('evidence');
              }}
            >
              <img src={trend.image} alt="" />
              <span>
                <Tag tone={i === 0 ? 'sage' : i === 1 ? 'lilac' : 'sand'}>
                  {trend.maturity.toUpperCase()}
                </Tag>
                <strong>{trend.name}</strong>
                <small>
                  {trend.growth} · {trend.sources} sources
                </small>
                <em>
                  Voir la tendance <ArrowRight size={13} />
                </em>
              </span>
            </button>
          ))}
          <button
            className="text-link"
            onClick={() =>
              notify(
                'Toutes les tendances sont affichées, de la plus solide à la moins solide',
              )
            }
          >
            Voir tous les signaux prioritaires <ArrowRight size={15} />
          </button>
        </aside>
      </div>
    </div>
  );
}

const ETAPES_MATURITE: [string, string[]][] = [
  ['Signal faible', ['buzz', 'a_surveiller']],
  ['Émergence', ['emergente']],
  ['Accélération', ['durable']],
  ['Massification', ['mature', 'declin']],
];

// Courbe réelle de la tendance : chemin SVG calculé à partir de l'historique de l'API
function cheminCourbe(
  points: { volume: number }[],
  largeur = 650,
  hauteur = 180,
) {
  if (points.length < 2) return '';
  const max = Math.max(...points.map((p) => p.volume), 1);
  return points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * largeur;
      const y = hauteur - 10 - (p.volume / max) * (hauteur - 25);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(' ');
}

function Evidence({
  go,
  notify,
}: {
  go: (view: View) => void;
  notify: (message: string) => void;
}) {
  const { courante, historique } = useDonnees();
  if (!courante) return null;
  const t = courante.api;
  const points = historique(courante.id);
  const mesures = t.criteres.filter((c) => c.disponible && c.note !== null);
  const indisponibles = t.criteres.filter((c) => !c.disponible);
  const parSource = new Map<string, number>();
  for (const c of mesures)
    parSource.set(
      c.source ?? 'autre',
      (parSource.get(c.source ?? 'autre') ?? 0) + 1,
    );
  const couleurs = ['green', 'purple', 'sand', 'gray'];
  const etiquettes = [
    points[0]?.semaine,
    points[Math.floor(points.length / 2)]?.semaine,
    points[points.length - 1]?.semaine,
  ]
    .filter(Boolean)
    .map((d) =>
      new Date(d as string).toLocaleDateString('fr-FR', {
        month: 'short',
        year: 'numeric',
      }),
    );

  return (
    <div className="page simple-page evidence-page">
      <div className="evidence-heading">
        <div>
          <Tag tone={estAlerte(courante) ? 'wine' : 'sand'}>
            ●{' '}
            {estAlerte(courante)
              ? 'OPPORTUNITÉ À TRAITER'
              : 'TENDANCE À SURVEILLER'}
          </Tag>
          <h1>
            {courante.name} <i>×</i> {courante.client}
          </h1>
          <p>
            {[
              t.marche_source && t.marche_cible
                ? `${t.marche_source} → ${t.marche_cible}`
                : '',
              t.profil_aromatique.join(', '),
            ]
              .filter(Boolean)
              .join(' · ') || 'Profil aromatique à définir avec la R&D.'}{' '}
            {t.donnees_simulees && <em>(données simulées)</em>}
          </p>
        </div>
        <button className="primary-button" onClick={() => go('matching')}>
          Identifier le bon client <ArrowRight size={16} />
        </button>
      </div>
      <div className="evidence-grid">
        <section className="hero-evidence">
          <img src={courante.image} alt="" />
          <div className="hero-statement">
            {t.explication ??
              `${courante.name} : explication à venir du modèle data.`}
          </div>
          <span className="photo-credit">Photo d’illustration</span>
        </section>
        <section className="evidence-summary">
          <div className="summary-card">
            <span className="summary-icon sage">
              <Leaf />
            </span>
            <div>
              <small>STATUT DE LA TENDANCE</small>
              <h2>{courante.maturity}</h2>
              <div className="maturity-track">
                {ETAPES_MATURITE.map(([libelle, verdicts]) =>
                  verdicts.includes(t.verdict) ? (
                    <b key={libelle}>{libelle.toUpperCase()}</b>
                  ) : (
                    <span key={libelle}>{libelle}</span>
                  ),
                )}
              </div>
            </div>
          </div>
          <div className="summary-card">
            <span className="summary-icon lilac">
              <CalendarDays />
            </span>
            <div>
              <small>FENÊTRE D’OPPORTUNITÉ</small>
              <h2>{texteFenetre(courante)}</h2>
            </div>
          </div>
          <div className="summary-card why-now">
            <span className="summary-icon sand">
              <Clock3 />
            </span>
            <div>
              <h2>Pourquoi maintenant ?</h2>
              <ul>
                {mesures.slice(0, 3).map((c) => (
                  <li key={c.nom}>
                    <TrendingUp />
                    <span>
                      <b>
                        {c.nom} : {c.note}/100
                      </b>
                      Source : {c.source}
                    </span>
                  </li>
                ))}
                {indisponibles.map((c) => (
                  <li key={c.nom}>
                    <FlaskConical />
                    <span>
                      <b>{c.nom} : source indisponible</b>Critère exclu du
                      score.
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>
        <section className="fit-card">
          <p className="eyebrow">SCORE DE DURABILITÉ</p>
          <div className="big-score">
            {courante.score}
            <small>/100</small>
          </div>
          <strong>
            {courante.score >= 60
              ? 'Signal robuste'
              : courante.score >= 40
                ? 'Signal faible'
                : 'Signal fragile'}
          </strong>
          <div className="score-factors">
            {t.criteres.map((c) => (
              <span key={c.nom}>
                <b>{c.poids !== null ? `${c.poids}%` : '—'}</b> {c.nom}
              </span>
            ))}
          </div>
        </section>
        <section className="chart-card">
          <div className="chart-title">
            <div>
              <p className="eyebrow">SIGNAL DANS LE TEMPS</p>
              <h3>
                {points.length > 1
                  ? 'La trajectoire mesurée'
                  : 'Courbe pas encore disponible'}
              </h3>
            </div>
            <div className="legend">
              <i /> {courante.name}
            </div>
          </div>
          <svg
            className="line-chart-svg"
            viewBox="0 0 650 180"
            preserveAspectRatio="none"
            aria-label="Évolution du signal"
          >
            <g className="grid-lines">
              <line x1="0" y1="30" x2="650" y2="30" />
              <line x1="0" y1="80" x2="650" y2="80" />
              <line x1="0" y1="130" x2="650" y2="130" />
            </g>
            {points.length > 1 && (
              <path className="main-line" d={cheminCourbe(points)} />
            )}
          </svg>
          <div className="chart-axis">
            {etiquettes.map((e, i) => (
              <span key={i}>{e}</span>
            ))}
          </div>
        </section>
        <section className="source-card">
          <div className="source-total">
            <ShieldCheck />
            <div>
              <small>PREUVES</small>
              <strong>{mesures.length}</strong>
              <span>
                critère{mesures.length > 1 ? 's' : ''} mesuré
                {mesures.length > 1 ? 's' : ''}
              </span>
            </div>
          </div>
          <ul>
            {[...parSource].map(([source, n], i) => (
              <li key={source}>
                <i className={couleurs[i % couleurs.length]} />
                {n} <span>{source}</span>
              </li>
            ))}
          </ul>
          <button
            className="text-link"
            onClick={() =>
              notify(`Sources : ${t.sources.join(', ') || 'à préciser'}`)
            }
          >
            Consulter les sources <ArrowRight size={14} />
          </button>
        </section>
        <section className="carte-tp globe-preuves">
          <h3>Marchés</h3>
          <Globe
            sources={
              [t.marche_source, ...t.pays_en_hausse].filter(Boolean) as string[]
            }
            cibles={t.marche_cible ? [t.marche_cible] : []}
            arcs={
              t.marche_source && t.marche_cible
                ? [[t.marche_source, t.marche_cible]]
                : []
            }
            hauteur={220}
          />
        </section>
      </div>
    </div>
  );
}

function Matching({
  go,
  notify,
}: {
  go: (view: View) => void;
  notify: (message: string) => void;
}) {
  const [selected, setSelected] = useState(0);
  const { courante, clients: referentiel, compatibilite } = useDonnees();
  const nomTendance = courante?.name ?? '';
  // Compatibilité du modèle data (RG07) quand elle existe, sinon des valeurs d'exemple signalées
  const clients = referentiel
    .map((c) => {
      const compat = courante ? compatibilite(courante, c.id) : null;
      return {
        ...c,
        score: compat?.score ?? 0,
        status: compat?.niveau ?? '—',
        compat,
      };
    })
    .sort((x, y) => y.score - x.score);
  const client = clients[Math.min(selected, clients.length - 1)];
  const exemple = client.compat?.exemple ?? true;
  const notes = client.compat?.criteres ?? {};
  const note = (v: number | null | undefined) =>
    typeof v === 'number' ? v : 'n.d.';
  return (
    <div className="page simple-page matching-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            MATCHING CLIENT · {nomTendance.toUpperCase()}
          </p>
          <h1>Le bon client donne sa valeur à la tendance.</h1>
          <p className="subtitle">
            {exemple
              ? 'Clients et compatibilités d’exemple, en attente du matching du modèle data (règle RG07). Clients génériques, aucune marque réelle.'
              : 'Compatibilités calculées par le modèle data (règle RG07). Clients de démonstration simulés, signalés comme tels.'}
          </p>
        </div>
        <button
          className="outline-button"
          onClick={() =>
            notify(`Analyse ${client.name} préparée pour l’export`)
          }
        >
          <Download size={16} /> Exporter l’analyse
        </button>
      </div>
      <div className="matching-layout">
        <section className="client-list">
          <div className="section-head">
            <h3>Clients recommandés</h3>
            <Tag tone="sand">{exemple ? 'EXEMPLE' : 'MODÈLE DATA'}</Tag>
          </div>
          {clients.map((item, index) => (
            <button
              className={`client-row ${selected === index ? 'active' : ''}`}
              key={item.name}
              onClick={() => setSelected(index)}
            >
              <span className="client-monogram">
                {item.name
                  .replace(/[^\p{L}\s]/gu, '')
                  .split(/\s+/)
                  .filter(Boolean)
                  .slice(0, 2)
                  .map((mot) => mot[0].toUpperCase())
                  .join('')}
              </span>
              <span>
                <strong>{item.name}</strong>
                <small>
                  {item.type} · {item.country}
                </small>
                <em>{item.status}</em>
              </span>
              <b>
                {item.score}
                <small>/100</small>
              </b>
              <ChevronRight size={17} />
            </button>
          ))}
          <div className="human-note">
            <ShieldCheck size={19} />
            <p>
              <strong>Décision humaine requise</strong>Le score assiste votre
              choix. Il ne déclenche jamais de prise de contact automatique.
            </p>
          </div>
        </section>
        <section className="match-detail">
          <div className="match-hero">
            <div>
              <p className="eyebrow">MEILLEURE CORRESPONDANCE</p>
              <h2>
                {nomTendance}
                <br />
                <i>×</i> {client.name}
              </h2>
              <p>
                {client.type} · {client.country}.{' '}
                {exemple
                  ? 'Compatibilité d’exemple, à remplacer par le score du modèle data.'
                  : `Compatibilité calculée par le modèle data${client.compat?.clientSimule ? ', client de démonstration simulé' : ''}.`}
              </p>
              <div className="tag-row">
                <Tag tone="wine">{client.country}</Tag>
                <Tag tone="sage">{courante?.maturity}</Tag>
                <Tag>{courante?.category}</Tag>
              </div>
            </div>
            <div className="match-score">
              <span>AFFINITÉ</span>
              <strong>
                {client.score}
                <small>%</small>
              </strong>
              <em>{client.status}</em>
            </div>
          </div>
          <div className="criteria-grid">
            <article>
              <span>
                <Leaf />
              </span>
              <div>
                <small>{exemple ? 'EXEMPLE' : 'RG07'}</small>
                <strong>Alignement de marque</strong>
                <p>Positionnement et valeurs du client (25 % du score).</p>
              </div>
              <b>{note(notes.marque)}</b>
            </article>
            <article>
              <span>
                <FolderKanban />
              </span>
              <div>
                <small>{exemple ? 'EXEMPLE' : 'RG07'}</small>
                <strong>Portefeuille produits</strong>
                <p>Catégories et lancements récents (20 % du score).</p>
              </div>
              <b>{note(notes.portefeuille)}</b>
            </article>
            <article>
              <span>
                <MapPin />
              </span>
              <div>
                <small>{exemple ? 'EXEMPLE' : 'RG07'}</small>
                <strong>Marché</strong>
                <p>Marchés actifs du client (15 % du score).</p>
              </div>
              <b>{note(notes.marche)}</b>
            </article>
            <article>
              <span>
                <Clock3 />
              </span>
              <div>
                <small>{exemple ? 'EXEMPLE' : 'RG07'}</small>
                <strong>Capacité à agir</strong>
                <p>
                  Cycle d’innovation et prochaine fenêtre de lancement (10 % du
                  score).
                </p>
              </div>
              <b>{note(notes.agir)}</b>
            </article>
          </div>
          <div className="business-angle">
            <div>
              <p className="eyebrow">ANGLE BUSINESS RECOMMANDÉ</p>
              <h3>Présenter la tendance avant l’appel d’offres.</h3>
              <p>
                Préparer un dossier avec les preuves mesurées et un concept
                adapté au client.
              </p>
            </div>
            <button className="primary-button" onClick={() => go('pitch')}>
              Créer la proposition <ArrowRight size={16} />
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}

function Pitch({
  notify,
  go,
}: {
  notify: (message: string) => void;
  go: (view: View) => void;
}) {
  const [step, setStep] = useState(0);
  const { trends, courante, choisir, clients, compatibilite, source, cleData } =
    useDonnees();
  // Textes rédigés par l'agent du modèle data ou par l'IA locale ; sans IA, un gabarit construit sur les données
  const [textesIA, setTextesIA] = useState<string[] | null>(null);
  const [origine, setOrigine] = useState('GABARIT');
  const [enCours, setEnCours] = useState(false);
  if (!courante) return null;
  const t = courante.api;
  const client = clients.find((c) => c.name === courante.client) ?? clients[0];
  const compat = compatibilite(courante, client.id);
  const mesures = t.criteres.filter((c) => c.disponible && c.note !== null);
  const steps = [
    'Le changement',
    'Les preuves',
    `Pourquoi ${client.name}`,
    'Le territoire',
    'La prochaine étape',
  ];
  const gabarits = [
    {
      title: `${courante.name} : ${courante.maturity.toLowerCase()}.`,
      kicker: 'LE CHANGEMENT',
      text:
        t.explication ?? 'Le modèle data n’a pas encore fourni d’explication.',
    },
    {
      title: `${mesures.length} critère${mesures.length > 1 ? 's' : ''} mesuré${mesures.length > 1 ? 's' : ''}.`,
      kicker: 'LA PREUVE',
      text:
        mesures
          .map((c) => `${c.nom} ${c.note}/100 (${c.source})`)
          .join(' · ') || 'Mesures à venir.',
    },
    {
      title: `Une piste pour ${client.name}.`,
      kicker: 'L’AFFINITÉ',
      text: `${client.type}, ${client.country}. Compatibilité de ${compat.score}/100${compat.exemple ? ' (exemple, en attente du matching du modèle data)' : ' (modèle data, client simulé)'}.`,
    },
    {
      title: 'Le territoire aromatique.',
      kicker: 'LE TERRITOIRE',
      text: t.profil_aromatique.length
        ? t.profil_aromatique.join(', ')
        : 'Profil aromatique à définir avec la R&D.',
    },
    {
      title: 'Créer avant le brief.',
      kicker: 'LA PROCHAINE ÉTAPE',
      text: `Fenêtre : ${texteFenetre(courante)}. Proposer un atelier de co-création au client.`,
    },
  ];
  const slides = gabarits.map((g, i) => ({
    ...g,
    text: textesIA?.[i] ?? g.text,
  }));
  const slide = slides[step];

  async function rediger() {
    setEnCours(true);
    // 1. L'agent du modèle data (who to call, why now, what to say), quand son API est branchée
    const cle = cleData(courante!);
    if (source === 'data' && cle) {
      try {
        const p = await genererPitchData(cle, client.id);
        const c = p.narrative.content;
        setTextesIA([
          c.why_now ?? gabarits[0].text,
          gabarits[1].text,
          c.who_to_call ?? gabarits[2].text,
          c.what_to_say ?? gabarits[3].text,
          c.next_step ?? gabarits[4].text,
        ]);
        setOrigine(
          p.narrative.origin === 'llm'
            ? 'RÉDIGÉ PAR L’AGENT DU MODÈLE DATA'
            : 'GABARIT DU MODÈLE DATA',
        );
        notify('Pitch rédigé par le modèle data, à relire avant envoi');
      } catch (erreur) {
        notify(`Modèle data indisponible : ${(erreur as Error).message}`);
      } finally {
        setEnCours(false);
      }
      return;
    }
    // 2. Sinon, l'IA locale de notre API (Ollama via /api/ai)
    const prompt = [
      'Tu es commercial chez un fabricant d’arômes. En français, écris exactement 5 paragraphes courts (2 phrases chacun),',
      'séparés par une ligne vide, sans titres ni numéros, sans inventer de chiffres :',
      '1. le changement de consommation, 2. les preuves, 3. pourquoi ce client, 4. le territoire aromatique, 5. la prochaine étape.',
      `Tendance : ${courante!.name}. Stade : ${courante!.maturity}. Score de durabilité : ${courante!.score}/100.`,
      `Mesures : ${gabarits[1].text}. Fenêtre : ${texteFenetre(courante!)}.`,
      `Profil aromatique : ${gabarits[3].text}. Client : ${client.type}, ${client.country}.`,
    ].join('\n');
    try {
      const res = await fetch('/api/ai', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      const corps = await res.json();
      if (!res.ok) throw new Error(corps.error ?? `Erreur ${res.status}`);
      const paragraphes = String(corps.answer)
        .split(/\n\s*\n/)
        .map((p) => p.replace(/^\s*\d+[.)]\s*/, '').trim())
        .filter(Boolean);
      setTextesIA(paragraphes.slice(0, 5));
      setOrigine('RÉDIGÉ PAR L’IA LOCALE');
      notify('Récit rédigé par l’IA locale, à relire avant envoi');
    } catch (erreur) {
      notify(`IA locale indisponible : ${(erreur as Error).message}`);
    } finally {
      setEnCours(false);
    }
  }

  return (
    <div className="page pitch-page">
      <aside className="pitch-opportunities">
        <p className="eyebrow">OPPORTUNITÉS</p>
        {trends.map((trend) => (
          <button
            className={trend.id === courante.id ? 'active' : ''}
            key={trend.name}
            onClick={() => {
              choisir(trend.id);
              setTextesIA(null);
              setStep(0);
              notify(`${trend.name} sélectionnée dans le Pitch Studio`);
            }}
          >
            <img src={trend.image} alt="" />
            <span>
              <strong>{trend.name}</strong>
              <small>
                {trend.client}
                <br />
                {trend.market}
              </small>
              <em>
                {estAlerte(trend) ? '● Prête à pitcher' : '● À surveiller'}
              </em>
            </span>
          </button>
        ))}
        <div className="pitch-mini-nav">
          <button onClick={() => go('evidence')}>
            <BookOpen /> Preuves
          </button>
          <button onClick={() => go('matching')}>
            <Users /> Portefeuille clients
          </button>
          <button
            className="active"
            onClick={() => notify('Vous êtes déjà dans le Pitch Studio')}
          >
            <PenLine /> Pitch Studio
          </button>
        </div>
      </aside>
      <main className="pitch-main">
        <div className="pitch-topline">
          <span>
            {courante.name} <i>/</i> {client.name} <i>/</i> {courante.market}
          </span>
          <button className="outline-button" onClick={() => window.print()}>
            <Download size={15} /> Exporter la présentation
          </button>
          <button
            className="icon-button"
            aria-label="Plus d’options"
            onClick={() =>
              notify('Options : dupliquer, partager ou archiver la proposition')
            }
          >
            <MoreHorizontal size={18} />
          </button>
        </div>
        <h1>Une histoire qu’ils voudront porter.</h1>
        <div className="story-workspace">
          <aside className="story-steps">
            <p className="eyebrow">ARCHITECTURE DU RÉCIT</p>
            {steps.map((item, index) => (
              <button
                className={step === index ? 'active' : ''}
                onClick={() => setStep(index)}
                key={item}
              >
                <b>{index + 1}</b>
                <span>{item}</span>
              </button>
            ))}
          </aside>
          <section className="story-canvas">
            <img src={courante.image} alt="" />
            <div className="story-copy">
              <p>
                {slide.kicker} · {textesIA ? origine : 'GABARIT'}
              </p>
              <h2>{slide.title}</h2>
              <i />
              <span>{slide.text}</span>
            </div>
            <div className="story-metrics">
              <span>
                <b>{compat.score}%</b>AFFINITÉ
                {compat.exemple ? ' (EXEMPLE)' : ''}
              </span>
              <span>
                <b>
                  {courante.score}
                  <small>/100</small>
                </b>
                DURABILITÉ
              </span>
              <span>
                <b>{texteFenetre(courante)}</b>FENÊTRE
              </span>
            </div>
          </section>
        </div>
        <div className="slide-strip">
          {slides.slice(0, 4).map((item, index) => (
            <button
              className={step === index ? 'active' : ''}
              onClick={() => setStep(index)}
              key={item.kicker}
            >
              <b>{index + 1}</b>
              <img
                src={
                  index % 2
                    ? `${import.meta.env.BASE_URL}assets/botanical.jpg`
                    : courante.image
                }
                alt=""
              />
              <span>{item.title}</span>
            </button>
          ))}
        </div>
        <div className="pitch-actions">
          <button
            className="primary-button"
            onClick={rediger}
            disabled={enCours}
          >
            <Sparkles size={16} />{' '}
            {enCours
              ? 'Rédaction en cours…'
              : source === 'data'
                ? 'Générer le pitch (modèle data)'
                : 'Générer le récit avec l’IA locale'}
          </button>
          <button
            className="outline-button"
            onClick={() => {
              setTextesIA(null);
              setOrigine('GABARIT');
            }}
          >
            <PenLine size={16} /> Revenir au gabarit
          </button>
          <button className="outline-button" onClick={() => go('matching')}>
            <Users size={16} /> Changer de client
          </button>
          <div className="validation-badge">
            <ShieldCheck />
            <span>
              <strong>
                {estAlerte(courante) ? 'Prête à pitcher' : 'À valider'}
              </strong>
              Tendance × client × marché
            </span>
          </div>
        </div>
      </main>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>('feed');
  const [collapsed, setCollapsed] = useState(false);
  const [toast, setToast] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [globalQuery, setGlobalQuery] = useState('');

  const { trends, choisir } = useDonnees();
  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(''), 2600);
  };

  return (
    <div className="app-shell">
      <Sidebar
        view={view}
        onChange={setView}
        collapsed={collapsed}
        onCollapse={() => setCollapsed(!collapsed)}
        notify={notify}
      />
      <div className="content-shell">
        <Topbar
          view={view}
          onMenu={() => setCollapsed(!collapsed)}
          onSearch={() => setSearchOpen(true)}
          notify={notify}
        />
        {view === 'feed' && <Feed go={setView} notify={notify} />}
        {view === 'radar' && <RadarView go={setView} notify={notify} />}
        {view === 'evidence' && <Evidence go={setView} notify={notify} />}
        {view === 'matching' && <Matching go={setView} notify={notify} />}
        {view === 'pitch' && <Pitch notify={notify} go={setView} />}
      </div>
      {searchOpen && (
        <div
          className="modal-backdrop"
          onMouseDown={() => setSearchOpen(false)}
        >
          <section
            className="search-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Recherche globale"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="search-modal-field">
              <Search size={19} />
              <input
                autoFocus
                value={globalQuery}
                onChange={(event) => setGlobalQuery(event.target.value)}
                placeholder="Rechercher une tendance ou un client..."
              />
              <button
                aria-label="Fermer la recherche"
                onClick={() => setSearchOpen(false)}
              >
                <X size={17} />
              </button>
            </div>
            <p className="eyebrow">RÉSULTATS RAPIDES</p>
            <div className="search-results">
              {trends
                .filter((trend) =>
                  `${trend.name} ${trend.client}`
                    .toLowerCase()
                    .includes(globalQuery.toLowerCase()),
                )
                .map((trend) => (
                  <button
                    key={trend.name}
                    onClick={() => {
                      choisir(trend.id);
                      setView('evidence');
                      setSearchOpen(false);
                      setGlobalQuery('');
                    }}
                  >
                    <img src={trend.image} alt="" />
                    <span>
                      <strong>{trend.name}</strong>
                      <small>
                        {trend.client} · {trend.market}
                      </small>
                    </span>
                    <ArrowRight size={16} />
                  </button>
                ))}
            </div>
            <button
              className="search-all-button"
              onClick={() => {
                setView('radar');
                setSearchOpen(false);
              }}
            >
              Voir tous les résultats dans le radar <ArrowRight size={15} />
            </button>
          </section>
        </div>
      )}
      {toast && (
        <div className="toast">
          <Check size={17} />
          {toast}
        </div>
      )}
    </div>
  );
}
