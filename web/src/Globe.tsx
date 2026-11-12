// Globe des marchés : pays où la tendance monte, marché visé et diffusion attendue.
// Fond de carte world-atlas (Natural Earth, domaine public), embarqué : aucun appel réseau.
import {
  geoCentroid,
  geoGraticule10,
  geoInterpolate,
  geoOrthographic,
  geoPath,
} from 'd3-geo';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
import { useEffect, useRef } from 'react';
import { feature } from 'topojson-client';
import type { GeometryCollection, Topology } from 'topojson-specification';
import monde from 'world-atlas/countries-110m.json';

type Pays = Feature<Geometry, { name: string }>;
const topologie = monde as unknown as Topology<{
  countries: GeometryCollection<{ name: string }>;
}>;
const PAYS = (
  feature(topologie, topologie.objects.countries) as FeatureCollection<
    Geometry,
    { name: string }
  >
).features as Pays[];

// Codes ISO alpha-2 de nos marchés vers les identifiants numériques de world-atlas
const ISO: Record<string, string> = {
  JP: '392',
  FR: '250',
  US: '840',
  KR: '410',
  CN: '156',
  DE: '276',
  IT: '380',
  ES: '724',
  GB: '826',
  UK: '826',
  BE: '056',
  MX: '484',
  TH: '764',
  BR: '076',
  IN: '356',
  CA: '124',
  AU: '036',
  NL: '528',
};

function trouver(codeOuNom: string): Pays | undefined {
  const id = ISO[codeOuNom.toUpperCase()];
  return PAYS.find((p) =>
    id
      ? p.id === id
      : p.properties.name.toLowerCase() === codeOuNom.toLowerCase(),
  );
}

export function Globe({
  sources,
  cibles,
  arcs,
  hauteur = 300,
}: {
  sources: string[];
  cibles: string[];
  arcs: [string, string][];
  hauteur?: number;
}) {
  const conteneur = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const cle = JSON.stringify([sources, cibles, arcs]);

  useEffect(() => {
    const zone = conteneur.current;
    const toile = canvas.current;
    if (!zone || !toile) return;
    const contexte = toile.getContext('2d')!;
    const enSource = new Set(sources.map(trouver).filter(Boolean));
    const enCible = new Set(cibles.map(trouver).filter(Boolean));
    const trajets = arcs
      .map(([a, b]) => [trouver(a), trouver(b)])
      .filter((x): x is [Pays, Pays] => Boolean(x[0] && x[1]))
      .map(([a, b]) => geoInterpolate(geoCentroid(a), geoCentroid(b)));
    const premier = [...enSource][0];
    let rotation: [number, number] = premier
      ? [-geoCentroid(premier)[0], -25]
      : [-10, -30];
    const projection = geoOrthographic().clipAngle(90);
    const chemin = geoPath(projection, contexte);

    const dessiner = () => {
      const largeur = zone.clientWidth || 400;
      const ratio = window.devicePixelRatio || 1;
      toile.width = largeur * ratio;
      toile.height = hauteur * ratio;
      toile.style.height = `${hauteur}px`;
      contexte.setTransform(ratio, 0, 0, ratio, 0, 0);
      projection
        .translate([largeur / 2, hauteur / 2])
        .scale(hauteur / 2.15)
        .rotate(rotation);
      contexte.clearRect(0, 0, largeur, hauteur);
      contexte.beginPath();
      chemin({ type: 'Sphere' });
      contexte.fillStyle = '#f4f7f3';
      contexte.fill();
      contexte.beginPath();
      chemin(geoGraticule10());
      contexte.strokeStyle = 'rgba(31, 61, 43, 0.07)';
      contexte.lineWidth = 0.6;
      contexte.stroke();
      for (const p of PAYS) {
        contexte.beginPath();
        chemin(p);
        contexte.fillStyle = enCible.has(p)
          ? '#1f3d2b'
          : enSource.has(p)
            ? '#5bbf3e'
            : '#e1e5e0';
        contexte.fill();
        contexte.strokeStyle = '#ffffff';
        contexte.lineWidth = 0.5;
        contexte.stroke();
      }
      for (const trajet of trajets) {
        contexte.beginPath();
        chemin({
          type: 'LineString',
          coordinates: Array.from({ length: 41 }, (_, i) => trajet(i / 40)),
        });
        contexte.strokeStyle = '#d97706';
        contexte.lineWidth = 2;
        contexte.stroke();
      }
    };

    // Rotation lente (sauf si l'utilisateur préfère moins d'animations), glisser pour tourner
    let enMouvement = !window.matchMedia('(prefers-reduced-motion: reduce)')
      .matches;
    let depart: [number, number] | null = null;
    const appui = (e: PointerEvent) => {
      depart = [e.clientX, e.clientY];
      enMouvement = false;
      toile.setPointerCapture(e.pointerId);
    };
    const glisse = (e: PointerEvent) => {
      if (!depart) return;
      rotation = [
        rotation[0] + (e.clientX - depart[0]) * 0.4,
        Math.max(
          -60,
          Math.min(60, rotation[1] - (e.clientY - depart[1]) * 0.4),
        ),
      ];
      depart = [e.clientX, e.clientY];
      dessiner();
    };
    const relache = () => (depart = null);
    toile.addEventListener('pointerdown', appui);
    toile.addEventListener('pointermove', glisse);
    toile.addEventListener('pointerup', relache);
    let image = 0;
    const animer = () => {
      if (enMouvement) {
        rotation = [rotation[0] + 0.12, rotation[1]];
        dessiner();
      }
      image = requestAnimationFrame(animer);
    };
    dessiner();
    image = requestAnimationFrame(animer);
    return () => {
      cancelAnimationFrame(image);
      toile.removeEventListener('pointerdown', appui);
      toile.removeEventListener('pointermove', glisse);
      toile.removeEventListener('pointerup', relache);
    };
    // cle résume sources, cibles et arcs : on ne redessine que s'ils changent
  }, [cle, hauteur]);

  return (
    <div className="globe-tp" ref={conteneur}>
      <canvas
        ref={canvas}
        role="img"
        aria-label="Globe des marchés : pays où la tendance monte et marchés visés"
      />
      <div className="legende-tp">
        <span>
          <i style={{ background: '#5bbf3e' }} />
          Intérêt en hausse
        </span>
        <span>
          <i style={{ background: '#1f3d2b' }} />
          Marché cible
        </span>
        <span>
          <i style={{ background: '#d97706' }} />
          Diffusion attendue
        </span>
      </div>
    </div>
  );
}
