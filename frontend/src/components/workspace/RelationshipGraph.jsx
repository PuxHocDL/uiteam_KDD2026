import { useEffect, useMemo, useRef, useState } from 'react';
import Modal from '../common/Modal';
import { Icon } from '../common/Icons';
import { getSessionSchema } from '../../lib/api';

// §12.2b — Live ER / relationship graph fed by GET /api/sessions/{sid}/schema
// (sqlite3 PRAGMAs, NO mocks). Supports MANY .db files in one workspace, drawing
// in-database FKs as solid lines and heuristic cross-database links (matching
// `<table>_id` → another file's `<tables>.id`) as dashed lines.
//
// Layout is a tiny force-directed simulation in JS: tables repel each other,
// FK edges pull them together, and the whole graph is recentred each tick.
// That keeps it dependency-free (no D3/dagre) and works for the 5-30-table
// graphs the studio actually produces.

const BASE_W = 1600;          // canvas for a small schema; it grows with table count below
const BASE_H = 900;
const AREA_PER_NODE = 92000;  // px² of canvas per table → spacing stays comfortable as it grows
const NODE_W = 200;
const NODE_H = 130;
const ZOOM_MIN = 0.1;         // zoom out far enough to fit large multi-DB schemas
const ZOOM_MAX = 2.5;

// Stage size scales with the number of tables so big multi-database schemas get
// room to spread instead of overlapping inside a fixed box.
function stageSize(n) {
  if (!n) return { stageW: BASE_W, stageH: BASE_H };
  const area = Math.max(BASE_W * BASE_H, n * AREA_PER_NODE);
  const aspect = BASE_W / BASE_H;
  const stageH = Math.round(Math.sqrt(area / aspect));
  return { stageW: Math.round(stageH * aspect), stageH };
}

// Distinct soft fills per source DB so the eye groups tables by file.
const DB_COLORS = [
  { fill: '#dcf8f9', stroke: '#50dce1' }, /* PC-1 Turquoise */
  { fill: '#ede9f2', stroke: '#84669c' }, /* PC-2 Purple */
  { fill: '#fff4e6', stroke: '#ffb061' },
  { fill: '#ffe9f1', stroke: '#ff7aa6' },
  { fill: '#e6f8ec', stroke: '#7fd09e' },
];

export default function RelationshipGraph({ sid, onClose }) {
  const [schema, setSchema] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [hover, setHover] = useState(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (!sid) { setError('Open a workspace session first to inspect its databases.'); setLoading(false); return; }
    let alive = true;
    setLoading(true); setError(''); setSchema(null);
    getSessionSchema(sid)
      .then((s) => { if (alive) setSchema(s); })
      .catch((e) => { if (alive) setError(String(e.message || e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [sid]);

  const dbs = schema?.databases || [];
  const crossLinks = schema?.cross_links || [];

  // Flatten tables across files into a single list of graph nodes, then run
  // a tiny force-directed simulation to position them. The simulation runs
  // ONCE per schema (not every render) and the result is memoised.
  const { nodes, edges, colorByFile, stageW, stageH } = useMemo(() => {
    if (!schema) return { nodes: [], edges: [], colorByFile: new Map(), stageW: BASE_W, stageH: BASE_H };
    const cbf = new Map();
    dbs.forEach((db, i) => cbf.set(db.file, DB_COLORS[i % DB_COLORS.length]));

    const ns = [];
    const idOf = (file, table) => `${file}::${table}`;
    dbs.forEach((db) => {
      db.tables.forEach((t) => {
        ns.push({
          id: idOf(db.file, t.name),
          file: db.file, table: t.name,
          columns: t.columns, rows: t.rows,
        });
      });
    });
    const es = [];
    dbs.forEach((db) => {
      (db.foreign_keys || []).forEach((f) => {
        es.push({
          source: idOf(db.file, f.from_table), target: idOf(db.file, f.to_table),
          label: `${f.from_column} → ${f.to_column}`, kind: 'fk',
        });
      });
    });
    crossLinks.forEach((l) => {
      es.push({
        source: `${l.from_file}::${l.from_table}`, target: `${l.to_file}::${l.to_table}`,
        label: `${l.from_column} → ${l.to_column}`, kind: 'cross',
      });
    });

    const { stageW: w, stageH: h } = stageSize(ns.length);
    layoutForceDirected(ns, es, w, h);
    return { nodes: ns, edges: es, colorByFile: cbf, stageW: w, stageH: h };
  }, [schema]); // eslint-disable-line react-hooks/exhaustive-deps

  const nodeById = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);

  // Drag-to-rearrange so the user can fix any awkward overlap from the layout.
  const dragRef = useRef(null);
  const viewportRef = useRef(null);
  const [positions, setPositions] = useState({}); // overrides for layout
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const panRef = useRef(null);
  const xyOf = (n) => positions[n.id] || { x: n.x, y: n.y };
  const onDown = (e, n) => {
    const stage = e.currentTarget.closest('.er-stage');
    if (!stage) return;
    const bounds = stage.getBoundingClientRect();
    const start = xyOf(n);
    // bounds already include the zoom transform, so 1px on screen == stageW/bounds.width
    // stage units regardless of zoom; no extra /zoom needed.
    dragRef.current = {
      id: n.id,
      ox: e.clientX, oy: e.clientY,
      sx: start.x, sy: start.y,
      scaleX: stageW / bounds.width, scaleY: stageH / bounds.height,
    };
    e.preventDefault();
    e.stopPropagation();
  };
  useEffect(() => {
    const move = (e) => {
      const d = dragRef.current;
      if (d) {
        const dx = (e.clientX - d.ox) * d.scaleX;
        const dy = (e.clientY - d.oy) * d.scaleY;
        setPositions((p) => ({ ...p, [d.id]: { x: d.sx + dx, y: d.sy + dy } }));
        return;
      }
      const pn = panRef.current;
      if (pn) {
        setPan({ x: pn.sx + (e.clientX - pn.ox), y: pn.sy + (e.clientY - pn.oy) });
      }
    };
    const up = () => { dragRef.current = null; panRef.current = null; };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
  }, []);

  // Reset zoom/pan/positions when the schema changes (new session opened),
  // and fit-to-viewport so the whole graph is visible at first paint.
  useEffect(() => {
    setPositions({});
    // Run on next frame so the viewport ref has its measured size.
    requestAnimationFrame(() => fitView());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema]);

  const zoomBy = (factor, anchor) => {
    setZoom((z) => {
      const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * factor));
      if (anchor) {
        // Keep the point under the cursor stable as we zoom.
        const k = next / z;
        setPan((p) => ({ x: anchor.x - (anchor.x - p.x) * k, y: anchor.y - (anchor.y - p.y) * k }));
      }
      return next;
    });
  };
  const resetView = () => { setZoom(1); setPan({ x: 0, y: 0 }); setPositions({}); };
  const fitView = () => {
    const el = viewportRef.current;
    if (!el) { setZoom(1); setPan({ x: 0, y: 0 }); return; }
    const padding = 24;
    const w = el.clientWidth - padding * 2;
    const h = el.clientHeight - padding * 2;
    const z = Math.min(1, 0.9 * Math.min(w / stageW, h / stageH));
    const next = Math.max(ZOOM_MIN, z);
    setZoom(next);
    setPan({ x: (el.clientWidth - stageW * next) / 2, y: (el.clientHeight - stageH * next) / 2 });
  };

  // Wheel = zoom (Ctrl+wheel for browser zoom is left alone); empty-area drag = pan.
  const onStageWheel = (e) => {
    if (e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    zoomBy(e.deltaY < 0 ? 1.1 : 1 / 1.1, { x: e.clientX - rect.left, y: e.clientY - rect.top });
  };
  const onStageDown = (e) => {
    // Only pan when clicking the empty stage (not a node).
    if (e.target.closest('.er-node')) return;
    panRef.current = { ox: e.clientX, oy: e.clientY, sx: pan.x, sy: pan.y };
    e.preventDefault();
  };

  const q = search.trim().toLowerCase();
  const matches = (n) => !q || n.table.toLowerCase().includes(q) || n.file.toLowerCase().includes(q);
  const edgeHot = (e) => hover && (e.source === hover || e.target === hover);
  const edgeDim = (e) => (hover && !edgeHot(e));
  const dimmed = (n) => (hover && hover !== n.id && !edgeTouches(edges, hover, n.id)) || (q && !matches(n));

  const totalTables = nodes.length;

  return (
    <Modal size="xl" onClose={onClose} title={
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Icon.network width={16} height={16} /> Data relationships
        <span className="dim-note">
          {dbs.length} database{dbs.length === 1 ? '' : 's'} · {totalTables} tables · {edges.length} links
        </span>
      </span>
    }>
      <div className="er-toolbar">
        <div className="mini-search">
          <Icon.search width={14} height={14} />
          <input placeholder="Find a table…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="er-legend-inline">
          {dbs.map((db) => {
            const c = colorByFile.get(db.file) || DB_COLORS[0];
            return (
              <span key={db.file} className="er-legend-chip" title={`${db.tables.length} tables`}>
                <span className="er-legend-swatch" style={{ background: c.fill, borderColor: c.stroke }} />
                {db.file}
              </span>
            );
          })}
          {crossLinks.length > 0 && <span className="er-legend-chip"><span className="er-legend-dash" /> cross-DB join</span>}
        </div>
        <div className="er-zoom" role="group" aria-label="Zoom controls">
          <button className="icon-btn" title="Zoom out" onClick={() => zoomBy(1 / 1.2)} disabled={zoom <= ZOOM_MIN + 0.001}>−</button>
          <button className="icon-btn" title="Reset view" onClick={resetView}>{Math.round(zoom * 100)}%</button>
          <button className="icon-btn" title="Zoom in" onClick={() => zoomBy(1.2)} disabled={zoom >= ZOOM_MAX - 0.001}>+</button>
          <button className="icon-btn" title="Fit" onClick={fitView}><Icon.expand width={13} height={13} /></button>
        </div>
      </div>

      {loading && <div className="dd-muted dd-center" style={{ padding: 30 }}>Inspecting your databases…</div>}
      {error && <div className="dd-error"><Icon.alert width={15} height={15} /> {error}</div>}
      {!loading && !error && dbs.length === 0 && (
        <div className="dd-empty">
          <Icon.network width={30} height={30} />
          <div>No SQLite (.db) files in this session. Upload a <b>.db</b> or <b>.sqlite</b> file to see its tables and foreign-key links.</div>
        </div>
      )}

      {!loading && !error && dbs.length > 0 && (
        <div className="er-viewport" ref={viewportRef} onWheel={onStageWheel} onMouseDown={onStageDown}>
          <div className="er-stage er-stage-live"
            style={{
              width: stageW, height: stageH,
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: '0 0',
            }}
          >
            <svg className="er-edges" viewBox={`0 0 ${stageW} ${stageH}`} preserveAspectRatio="none" style={{ width: stageW, height: stageH }}>
              {edges.map((e, i) => {
                const a = xyOf(nodeById[e.source] || {});
                const b = xyOf(nodeById[e.target] || {});
                if (a.x == null || b.x == null) return null;
                return (
                  <g key={i}>
                    <line x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                      className={`er-line ${e.kind === 'cross' ? 'cross' : ''} ${edgeHot(e) ? 'hot' : ''} ${edgeDim(e) ? 'dim' : ''}`}
                      vectorEffect="non-scaling-stroke" />
                    {edgeHot(e) && (
                      <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4}
                        className="er-edge-label-svg" textAnchor="middle">{e.label}</text>
                    )}
                  </g>
                );
              })}
            </svg>

            {nodes.map((n) => {
              const c = colorByFile.get(n.file) || DB_COLORS[0];
              const pos = xyOf(n);
              return (
                <div key={n.id}
                  className={`er-node ${hover === n.id ? 'hot' : ''} ${dimmed(n) ? 'dim' : ''} ${matches(n) && q ? 'match' : ''}`}
                  style={{
                    left: pos.x, top: pos.y,
                    background: c.fill, borderColor: c.stroke,
                  }}
                  onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
                  onMouseDown={(e) => onDown(e, n)}
                >
                  <div className="ern-head">
                    <Icon.data width={13} height={13} />
                    <span className="ern-name">{n.table}</span>
                    <span className="ern-src sqlite" title={n.file}>{n.file}</span>
                  </div>
                  <div className="ern-cols">
                    {n.columns.slice(0, 8).map((col) => (
                      <div key={col.name} className={`ern-col ${col.pk ? 'pk' : col.fk ? 'fk' : ''}`}>
                        {col.pk && <span className="key-badge pk"><Icon.key width={9} height={9} /></span>}
                        {col.fk && !col.pk && <span className="key-badge fk"><Icon.key width={9} height={9} /></span>}
                        {col.name}<span className="ern-col-type">{col.type}</span>
                      </div>
                    ))}
                    {n.columns.length > 8 && <div className="ern-col more">+{n.columns.length - 8} more</div>}
                    {n.rows != null && <div className="ern-rows">{n.rows.toLocaleString()} rows</div>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="er-legend">
        <span><span className="key-badge pk"><Icon.key width={9} height={9} /></span> primary key</span>
        <span><span className="key-badge fk"><Icon.key width={9} height={9} /></span> foreign key</span>
        <span className="dim-note">Drag a table to rearrange · scroll to zoom · drag empty area to pan.</span>
      </div>
    </Modal>
  );
}

// Returns true if the edge list has an edge between `id` and `other`.
function edgeTouches(edges, id, other) {
  if (id === other) return true;
  for (const e of edges) {
    if ((e.source === id && e.target === other) || (e.source === other && e.target === id)) return true;
  }
  return false;
}

// Deterministic [-0.5, 0.5) jitter from an index — keeps the same schema mapping
// to the same layout (no Math.random flicker) while breaking up the seed grid.
function jitter(i, salt) {
  const s = Math.sin((i + 1) * salt) * 43758.5453;
  return s - Math.floor(s) - 0.5;
}

// Fruchterman–Reingold layout with scattered seeding so large multi-DB schemas
// spread across the canvas instead of collapsing into one clump. Deterministic →
// same schema, same layout. Writes n.x / n.y in place.
function layoutForceDirected(nodes, edges, w, h) {
  const n = nodes.length;
  if (n === 0) return;
  // Seed on a jittered grid over the whole stage (a single seed ring piles up
  // for many tables); the simulation then relaxes from a spread starting point.
  const cols = Math.max(1, Math.round(Math.sqrt(n * (w / h))));
  const cellW = w / cols;
  const cellH = h / Math.ceil(n / cols);
  nodes.forEach((node, i) => {
    node.x = ((i % cols) + 0.5) * cellW + jitter(i, 12.9898) * cellW * 0.7;
    node.y = (Math.floor(i / cols) + 0.5) * cellH + jitter(i, 78.233) * cellH * 0.7;
  });
  const k = Math.sqrt((w * h) / n);
  const repulse = k * k * 1.2;
  const iter = Math.min(500, 260 + n * 2);
  let temp = Math.max(w, h) / 6;
  const cool = temp / iter;
  const idx = Object.fromEntries(nodes.map((nd, i) => [nd.id, i]));

  for (let it = 0; it < iter; it++) {
    const disp = nodes.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = nodes[i].x - nodes[j].x;
        const dy = nodes[i].y - nodes[j].y;
        let dist = Math.hypot(dx, dy) || 0.01;
        if (dist < NODE_W * 0.9) dist = NODE_W * 0.9;
        const f = repulse / dist;
        const fx = (dx / dist) * f; const fy = (dy / dist) * f;
        disp[i].x += fx; disp[i].y += fy;
        disp[j].x -= fx; disp[j].y -= fy;
      }
    }
    edges.forEach((e) => {
      const i = idx[e.source]; const j = idx[e.target];
      if (i == null || j == null) return;
      const dx = nodes[i].x - nodes[j].x;
      const dy = nodes[i].y - nodes[j].y;
      const dist = Math.hypot(dx, dy) || 0.01;
      const strength = e.kind === 'cross' ? 0.6 : 1.0;
      const f = Math.min((dist * dist) / k, dist * 6) * strength; // capped pull
      const fx = (dx / dist) * f; const fy = (dy / dist) * f;
      disp[i].x -= fx; disp[i].y -= fy;
      disp[j].x += fx; disp[j].y += fy;
    });
    nodes.forEach((node, i) => {
      const d = disp[i];
      const m = Math.hypot(d.x, d.y) || 0.01;
      const step = Math.min(m, temp);
      node.x += (d.x / m) * step;
      node.y += (d.y / m) * step;
      const padX = NODE_W / 2 + 12;
      const padY = NODE_H / 2 + 12;
      node.x = Math.min(w - padX, Math.max(padX, node.x));
      node.y = Math.min(h - padY, Math.max(padY, node.y));
    });
    temp = Math.max(1, temp - cool);
  }

  // Final de-overlap: separate any tables whose cards still intersect, pushing
  // along the axis of least penetration so no two cards stack on top of each other.
  const mw = NODE_W + 16; const mh = NODE_H + 14;
  for (let pass = 0; pass < 16; pass++) {
    let moved = false;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = nodes[i]; const b = nodes[j];
        const dx = b.x - a.x; const dy = b.y - a.y;
        const ox = mw - Math.abs(dx); const oy = mh - Math.abs(dy);
        if (ox > 0 && oy > 0) {
          moved = true;
          if (ox < oy) { const s = (ox / 2) * (dx < 0 ? -1 : 1); a.x -= s; b.x += s; }
          else { const s = (oy / 2) * (dy < 0 ? -1 : 1); a.y -= s; b.y += s; }
        }
      }
    }
    if (!moved) break;
  }
}
