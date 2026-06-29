import { useEffect, useMemo, useRef, useState } from 'react';
import Modal from '../common/Modal';
import { Icon } from '../common/Icons';
import { buildTextKnowledgeGraph } from '../../lib/api';

// §12.2a — Text → Knowledge Graph viewer. Calls POST /api/sessions/{sid}/textkg,
// which returns LLM-extracted entities + relations from a PDF / Markdown / text
// file. We draw the graph with a tiny force-directed layout (same pattern as
// RelationshipGraph, but lighter: variable-width nodes, no FK ports), and a
// side panel shows the *evidence* — the verbatim quote and source page — for
// whatever node or edge the user clicks. Nothing is invented client-side; if
// the model returns no graph, we show its `note` so the user knows why.

const BASE_W = 1400;         // canvas for a small graph; it grows with entity count below
const BASE_H = 820;
const AREA_PER_NODE = 38000; // px² of canvas per entity → node density stays ~constant as it grows
const NODE_PADDING_X = 14;
const NODE_HEIGHT = 38;
const CHAR_W = 7.2;          // approximate label width for sizing
const ZOOM_MIN = 0.05;       // zoom out far enough to fit very large graphs
const ZOOM_MAX = 2.5;

// The stage is effectively unbounded: its size scales with the number of nodes so
// hundreds of entities get room to spread instead of being crushed into a fixed box.
function stageSize(n) {
  if (!n) return { stageW: BASE_W, stageH: BASE_H };
  const area = Math.max(BASE_W * BASE_H, n * AREA_PER_NODE);
  const aspect = BASE_W / BASE_H;
  const stageH = Math.round(Math.sqrt(area / aspect));
  return { stageW: Math.round(stageH * aspect), stageH };
}

// Distinct colour per entity type so the graph reads at a glance.
const TYPE_COLORS = {
  Person:        { fill: '#e7fbfa', stroke: '#3aa9a8', text: '#0a2f2e' },
  Organisation:  { fill: '#f4f1ff', stroke: '#7a5bd9', text: '#1f1140' },
  Project:       { fill: '#fff4e6', stroke: '#d97706', text: '#4a2a02' },
  Place:         { fill: '#e6f8ec', stroke: '#16a34a', text: '#0a3618' },
  Concept:       { fill: '#fff7d6', stroke: '#b58900', text: '#3d2d00' },
  Event:         { fill: '#ffe9f1', stroke: '#db2777', text: '#4a0820' },
  Date:          { fill: '#dbeafe', stroke: '#2563eb', text: '#0b2752' },
  Other:         { fill: '#e5e7eb', stroke: '#6b7280', text: '#111827' },
};
const TYPE_LIST = Object.keys(TYPE_COLORS);

export default function TextKnowledgeGraphModal({ sid, file, settings, onClose }) {
  const [graph, setGraph] = useState(null);
  const [doc, setDoc] = useState(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [hover, setHover] = useState(null);          // node id
  const [selected, setSelected] = useState(null);    // {kind:'node'|'edge'|'cluster', ref}
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState(null);
  // Hierarchical view state: when grouping by cluster, collapsed clusters render
  // as a single super-node so big graphs (hundreds of entities) stay readable.
  const [groupMode, setGroupMode] = useState('cluster'); // 'cluster' | 'none'
  const [collapsed, setCollapsed] = useState(() => new Set());

  // ---- fetch ---------------------------------------------------------------
  useEffect(() => {
    if (!sid || !file) return;
    const creds = {
      model: settings?.model || '',
      api_base: settings?.endpoint || '',
      api_key: settings?.apiKey || '',
      api_version: settings?.apiVersion || '',
    };
    let alive = true;
    setLoading(true); setError(''); setGraph(null); setNote(''); setSelected(null);
    buildTextKnowledgeGraph(sid, file.name, creds)
      .then((g) => { if (!alive) return; setGraph(g); setDoc(g.doc); setNote(g.note || ''); })
      .catch((e) => { if (alive) setError(String(e.message || e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [sid, file?.name, settings?.model, settings?.endpoint, settings?.apiKey, settings?.apiVersion]);

  // Default to collapsing every cluster except the two biggest whenever the
  // graph is large — otherwise the user lands on a wall of dots.
  useEffect(() => {
    const cs = graph?.clusters || [];
    if (cs.length > 1 && (graph?.nodes?.length || 0) > 30) {
      const keepOpen = new Set(
        cs.slice().sort((a, b) => b.size - a.size).slice(0, 2).map((c) => c.id),
      );
      setCollapsed(new Set(cs.filter((c) => c.size >= 3 && !keepOpen.has(c.id)).map((c) => c.id)));
    } else {
      setCollapsed(new Set());
    }
  }, [graph]);

  // ---- layout (cluster-aware) ----------------------------------------------
  // Collapsed clusters become a single "super-node" carrying the cluster's label
  // and member count. Edges that touched a collapsed cluster get rewritten to the
  // super-node and deduplicated. `hulls` are the rounded bounding rects we draw
  // behind expanded clusters so the user sees the grouping at a glance.
  const { nodes, edges, nodeById, hulls, stageW, stageH, clusters, clusterById } = useMemo(() => {
    const raw = graph?.nodes || [];
    const ed = graph?.edges || [];
    const cs = graph?.clusters || [];
    const useClusters = groupMode === 'cluster' && cs.length > 0;
    const cById = Object.fromEntries(cs.map((c) => [c.id, c]));
    const collapsedSet = useClusters ? collapsed : new Set();

    const ns = [];
    const idMap = {}; // real id → visible id (entity or super-node)
    const supers = new Set();
    raw.forEach((n) => {
      const cid = useClusters ? n.cluster_id : null;
      if (cid && collapsedSet.has(cid)) {
        const sid = `__c:${cid}`;
        idMap[n.id] = sid;
        if (!supers.has(sid)) {
          supers.add(sid);
          const c = cById[cid];
          const label = `${c.label} +${c.size - 1}`;
          ns.push({
            id: sid, kind: 'super', cluster_id: cid,
            label, type: c.dominant_type,
            summary: `${c.size} entities · ${c.internal_edges} internal links · ${c.external_edges} crossing`,
            pages: [],
            members: c.members,
            width: Math.max(140, Math.min(360, label.length * CHAR_W + NODE_PADDING_X * 2 + 38)),
            height: NODE_HEIGHT + 8,
          });
        }
      } else {
        idMap[n.id] = n.id;
        ns.push({
          ...n, kind: 'entity',
          width: Math.max(80, Math.min(340, (n.label?.length || 1) * CHAR_W + NODE_PADDING_X * 2 + 18)),
          height: NODE_HEIGHT,
        });
      }
    });

    // Dedupe edges after rewriting endpoints onto super-nodes; remember the
    // crossings so the side panel can offer "show edges into this cluster".
    const edgeMap = new Map();
    ed.forEach((e) => {
      const s = idMap[e.source]; const t = idMap[e.target];
      if (!s || !t || s === t) return;
      const key = `${s}\x00${t}\x00${e.relation}`;
      const prev = edgeMap.get(key);
      if (prev) { prev.count += 1; return; }
      edgeMap.set(key, {
        source: s, target: t, relation: e.relation, page: e.page, quote: e.quote,
        count: 1,
        // Original endpoint ids (real nodes), for evidence on aggregated edges.
        srcReal: e.source, tgtReal: e.target,
      });
    });
    const valid = new Set(ns.map((n) => n.id));
    const es = [...edgeMap.values()].filter((e) => valid.has(e.source) && valid.has(e.target));

    // Layout clusters: only the expanded ones drive cohesion; super-nodes and
    // isolates float freely so they don't get sucked into a cluster.
    const layoutClusters = useClusters
      ? cs.filter((c) => !collapsedSet.has(c.id)).map((c) => ({
          id: c.id, members: c.members.filter((m) => idMap[m] === m),
        })).filter((c) => c.members.length > 0)
      : [];

    const { stageW: w, stageH: h } = stageSize(ns.length);
    layoutClustered(ns, es, w, h, layoutClusters);

    // Hulls = padded bounding rect over each expanded cluster's members.
    const hullRects = layoutClusters.map((lc) => {
      const ms = lc.members.map((m) => ns.find((x) => x.id === m)).filter(Boolean);
      if (ms.length < 2) return null;
      const pad = 26;
      const xs = ms.flatMap((m) => [m.x - m.width / 2, m.x + m.width / 2]);
      const ys = ms.flatMap((m) => [m.y - m.height / 2, m.y + m.height / 2]);
      const c = cById[lc.id];
      const minX = Math.min(...xs), minY = Math.min(...ys);
      return {
        id: lc.id, label: c.label, type: c.dominant_type, size: c.size,
        members: c.members,
        x: minX - pad, y: minY - pad,
        w: (Math.max(...xs) - minX) + pad * 2,
        h: (Math.max(...ys) - minY) + pad * 2,
      };
    }).filter(Boolean);

    return {
      nodes: ns, edges: es, hulls: hullRects, stageW: w, stageH: h,
      nodeById: Object.fromEntries(ns.map((n) => [n.id, n])),
      clusters: cs, clusterById: cById,
    };
  }, [graph, groupMode, collapsed]);

  // ---- zoom / pan / drag ---------------------------------------------------
  const viewportRef = useRef(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [positions, setPositions] = useState({});
  const dragRef = useRef(null);
  const panRef = useRef(null);
  const xyOf = (n) => positions[n.id] || { x: n.x, y: n.y };

  const zoomBy = (factor, anchor) => {
    setZoom((z) => {
      const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * factor));
      if (anchor) {
        const k = next / z;
        setPan((p) => ({ x: anchor.x - (anchor.x - p.x) * k, y: anchor.y - (anchor.y - p.y) * k }));
      }
      return next;
    });
  };
  const fitView = () => {
    const el = viewportRef.current;
    if (!el) { setZoom(1); setPan({ x: 0, y: 0 }); return; }
    const padding = 24;
    const w = el.clientWidth - padding * 2;
    const h = el.clientHeight - padding * 2;
    const z = Math.max(ZOOM_MIN, Math.min(1, 0.9 * Math.min(w / stageW, h / stageH)));
    setZoom(z);
    setPan({ x: (el.clientWidth - stageW * z) / 2, y: (el.clientHeight - stageH * z) / 2 });
  };
  const resetView = () => { setZoom(1); setPan({ x: 0, y: 0 }); setPositions({}); };

  useEffect(() => {
    setPositions({});
    requestAnimationFrame(() => fitView());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  const onDown = (e, n) => {
    const stage = e.currentTarget.closest('.tkg-stage');
    if (!stage) return;
    const bounds = stage.getBoundingClientRect();
    const start = xyOf(n);
    dragRef.current = {
      id: n.id, ox: e.clientX, oy: e.clientY, sx: start.x, sy: start.y,
      scaleX: stageW / bounds.width, scaleY: stageH / bounds.height,
    };
    e.stopPropagation(); e.preventDefault();
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
      if (pn) setPan({ x: pn.sx + (e.clientX - pn.ox), y: pn.sy + (e.clientY - pn.oy) });
    };
    const up = () => { dragRef.current = null; panRef.current = null; };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
  }, []);
  const onStageWheel = (e) => {
    if (e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    zoomBy(e.deltaY < 0 ? 1.1 : 1 / 1.1, { x: e.clientX - rect.left, y: e.clientY - rect.top });
  };
  const onStageDown = (e) => {
    if (e.target.closest('.tkg-node')) return;
    panRef.current = { ox: e.clientX, oy: e.clientY, sx: pan.x, sy: pan.y };
    e.preventDefault();
  };

  // ---- filtering / highlight -----------------------------------------------
  const q = search.trim().toLowerCase();
  const matches = (n) => {
    if (!n) return false;
    if (n.kind === 'super') {
      // A super-node matches if its label, any member label, or its cluster type does.
      if (typeFilter && n.type !== typeFilter) return false;
      if (!q) return true;
      if (n.label.toLowerCase().includes(q)) return true;
      const raws = graph?.nodes || [];
      const memberLabels = (n.members || []).map((mid) => (raws.find((r) => r.id === mid)?.label || '').toLowerCase());
      return memberLabels.some((l) => l.includes(q));
    }
    if (typeFilter && n.type !== typeFilter) return false;
    if (!q) return true;
    return n.label.toLowerCase().includes(q) || (n.summary || '').toLowerCase().includes(q);
  };
  const edgeOf = (e) => {
    const sel = selected?.kind === 'node' ? selected.ref.id : null;
    const hot = hover || sel;
    return { hot: hot && (e.source === hot || e.target === hot),
             dim: (hot && !(e.source === hot || e.target === hot)) || (q && (!matches(nodeById[e.source]) || !matches(nodeById[e.target]))) };
  };

  const counts = useMemo(() => {
    const c = {};
    (graph?.nodes || []).forEach((n) => { c[n.type] = (c[n.type] || 0) + 1; });
    return c;
  }, [graph]);

  // ---- cluster controls ----------------------------------------------------
  const collapseAll = () => setCollapsed(new Set((clusters || []).filter((c) => c.size >= 2).map((c) => c.id)));
  const expandAll = () => setCollapsed(new Set());
  const toggleCluster = (cid) => setCollapsed((prev) => {
    const s = new Set(prev);
    if (s.has(cid)) s.delete(cid); else s.add(cid);
    return s;
  });
  const expandClusterAndFocus = (cid) => {
    setCollapsed((prev) => { const s = new Set(prev); s.delete(cid); return s; });
    setSelected({ kind: 'cluster', ref: clusterById[cid] });
  };

  // ---- render --------------------------------------------------------------
  const engineLabel = doc?.engine?.startsWith('llamaindex')
    ? 'LlamaIndex' : doc?.engine === 'builtin' ? 'built-in' : null;
  const totalEntities = graph?.nodes?.length || 0;
  const title = (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <Icon.network width={16} height={16} /> Knowledge graph — {file?.name}
      {doc && <span className="dim-note">{doc.pages} page{doc.pages === 1 ? '' : 's'} · {totalEntities} entit{totalEntities === 1 ? 'y' : 'ies'} · {clusters.length} cluster{clusters.length === 1 ? '' : 's'} · {edges.length} relation{edges.length === 1 ? '' : 's'}{engineLabel ? ` · ${engineLabel}` : ''}</span>}
    </span>
  );

  const collapsedCount = collapsed.size;
  const hasClusters = (clusters?.length || 0) > 1;
  const activeTypes = TYPE_LIST.filter((t) => counts[t]);
  const typeColour = typeFilter ? (TYPE_COLORS[typeFilter] || TYPE_COLORS.Other) : null;
  const [typeMenuOpen, setTypeMenuOpen] = useState(false);
  const typeMenuRef = useRef(null);
  // Close the type popover on outside click / Escape so it behaves like a
  // standard dropdown instead of swallowing canvas clicks.
  useEffect(() => {
    if (!typeMenuOpen) return undefined;
    const onDoc = (ev) => {
      if (!typeMenuRef.current) return;
      if (!typeMenuRef.current.contains(ev.target)) setTypeMenuOpen(false);
    };
    const onKey = (ev) => { if (ev.key === 'Escape') setTypeMenuOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [typeMenuOpen]);

  return (
    <Modal size="xl" onClose={onClose} title={title}>
      <div className="er-toolbar tkg-toolbar">
        <div className="mini-search">
          <Icon.search width={14} height={14} />
          <input placeholder="Find an entity or term…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        {hasClusters && (
          <div className="tkg-cluster-controls" role="group" aria-label="Cluster controls">
            <button
              className={`er-legend-chip ${groupMode === 'cluster' ? 'on' : ''}`}
              onClick={() => setGroupMode(groupMode === 'cluster' ? 'none' : 'cluster')}
              title="Group entities by community detected in the graph"
            >
              <Icon.network width={12} height={12} /> Group by cluster
            </button>
            {groupMode === 'cluster' && (
              <>
                <button className="icon-btn sm" onClick={collapseAll} title="Collapse every cluster into a super-node">
                  Collapse all
                </button>
                <button className="icon-btn sm" onClick={expandAll} title="Expand every cluster" disabled={collapsedCount === 0}>
                  Expand all
                </button>
                <span className="dim-note tkg-cluster-counter">{collapsedCount}/{clusters.length} collapsed</span>
              </>
            )}
          </div>
        )}
        {activeTypes.length > 0 && (
          <div className="tkg-typefilter" ref={typeMenuRef}>
            <button
              className={`er-legend-chip tkg-typefilter-btn ${typeFilter ? 'on' : ''} ${typeMenuOpen ? 'open' : ''}`}
              onClick={() => setTypeMenuOpen((v) => !v)}
              title="Filter graph by entity type"
              aria-haspopup="listbox" aria-expanded={typeMenuOpen}
            >
              {typeColour && <span className="er-legend-swatch" style={{ background: typeColour.fill, borderColor: typeColour.stroke }} />}
              <span>Type: <strong>{typeFilter || 'All'}</strong></span>
              <span className="dim-note">{typeFilter ? counts[typeFilter] : activeTypes.length}</span>
              <span className="tkg-caret">▾</span>
            </button>
            {typeMenuOpen && (
              <div className="tkg-typefilter-menu" role="listbox">
                <button
                  className={`tkg-typefilter-item ${!typeFilter ? 'on' : ''}`}
                  onClick={() => { setTypeFilter(null); setTypeMenuOpen(false); }}
                  role="option" aria-selected={!typeFilter}
                >
                  <span className="er-legend-swatch tkg-swatch-all" />
                  <span className="tkg-typefilter-label">All types</span>
                  <span className="dim-note">{activeTypes.reduce((s, t) => s + counts[t], 0)}</span>
                </button>
                {activeTypes.map((t) => {
                  const c = TYPE_COLORS[t];
                  const on = typeFilter === t;
                  return (
                    <button key={t}
                      className={`tkg-typefilter-item ${on ? 'on' : ''}`}
                      onClick={() => { setTypeFilter(on ? null : t); setTypeMenuOpen(false); }}
                      role="option" aria-selected={on}
                    >
                      <span className="er-legend-swatch" style={{ background: c.fill, borderColor: c.stroke }} />
                      <span className="tkg-typefilter-label">{t}</span>
                      <span className="dim-note">{counts[t]}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}
        <div className="er-zoom" role="group" aria-label="Zoom controls">
          <button className="icon-btn" title="Zoom out" onClick={() => zoomBy(1 / 1.2)} disabled={zoom <= ZOOM_MIN + 0.001}>−</button>
          <button className="icon-btn" title="Reset" onClick={resetView}>{Math.round(zoom * 100)}%</button>
          <button className="icon-btn" title="Zoom in" onClick={() => zoomBy(1.2)} disabled={zoom >= ZOOM_MAX - 0.001}>+</button>
          <button className="icon-btn" title="Fit" onClick={fitView}><Icon.expand width={13} height={13} /></button>
        </div>
      </div>

      {loading && <div className="dd-muted dd-center" style={{ padding: 30 }}>Asking the model to read {file?.name}…</div>}
      {error && <div className="dd-error"><Icon.alert width={15} height={15} /> {error}</div>}

      {!loading && !error && nodes.length === 0 && (
        <div className="dd-empty">
          <Icon.network width={30} height={30} />
          <div>{note || 'No entities or relations were extracted from this document.'}</div>
        </div>
      )}

      {!loading && !error && nodes.length > 0 && (
        <div className="tkg-layout">
          <div className="er-viewport tkg-viewport" ref={viewportRef} onWheel={onStageWheel} onMouseDown={onStageDown}>
            <div className="er-stage tkg-stage"
              style={{
                width: stageW, height: stageH,
                transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                transformOrigin: '0 0',
              }}
            >
              <svg className="er-edges" viewBox={`0 0 ${stageW} ${stageH}`} preserveAspectRatio="none"
                style={{ width: stageW, height: stageH }}>
                <defs>
                  <marker id="tkg-arrow" viewBox="0 -5 10 10" refX="9" refY="0" markerWidth="6" markerHeight="6" orient="auto">
                    <path d="M0,-5L10,0L0,5" fill="#94a3b8" />
                  </marker>
                </defs>
                {/* cluster hulls — drawn behind everything so they visually group
                    the entities without occluding the edges or node pills */}
                <g className="tkg-hulls">
                  {hulls.map((h) => {
                    const c = TYPE_COLORS[h.type] || TYPE_COLORS.Other;
                    const sel = selected?.kind === 'cluster' && selected.ref?.id === h.id;
                    return (
                      <g key={h.id} className={`tkg-hull ${sel ? 'sel' : ''}`}
                        onClick={(ev) => { ev.stopPropagation(); setSelected({ kind: 'cluster', ref: clusterById[h.id] }); }}
                        style={{ cursor: 'pointer' }}>
                        <rect x={h.x} y={h.y} width={h.w} height={h.h} rx={28} ry={28}
                          fill={c.fill} stroke={c.stroke} strokeDasharray="6 5" strokeWidth="1.5"
                          fillOpacity={sel ? 0.55 : 0.32} />
                        <text x={h.x + 16} y={h.y + 22} className="tkg-hull-label" fill={c.text}>
                          {h.label}<tspan className="tkg-hull-count" dx="6">· {h.size}</tspan>
                        </text>
                        <g className="tkg-hull-action"
                          onClick={(ev) => { ev.stopPropagation(); toggleCluster(h.id); }}
                          transform={`translate(${h.x + h.w - 30}, ${h.y + 8})`}
                          style={{ cursor: 'pointer' }}>
                          <rect width="22" height="22" rx="6" fill={c.stroke} fillOpacity="0.18" stroke={c.stroke} />
                          <text x="11" y="16" textAnchor="middle" fontSize="13" fontWeight="700" fill={c.text}>−</text>
                        </g>
                      </g>
                    );
                  })}
                </g>
                {edges.map((e, i) => {
                  const a = xyOf(nodeById[e.source]); const b = xyOf(nodeById[e.target]);
                  if (a.x == null || b.x == null) return null;
                  const meta = edgeOf(e);
                  const isAgg = e.count > 1;
                  return (
                    <g key={i} className={`tkg-edge ${meta.hot ? 'hot' : ''} ${meta.dim ? 'dim' : ''} ${isAgg ? 'agg' : ''}`}
                      onClick={() => setSelected({ kind: 'edge', ref: e })} style={{ cursor: 'pointer' }}>
                      <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="er-line"
                        vectorEffect="non-scaling-stroke" markerEnd="url(#tkg-arrow)"
                        strokeWidth={isAgg ? Math.min(4, 1 + Math.log2(e.count + 1)) : 1.5} />
                      {meta.hot && (
                        <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4} className="er-edge-label-svg" textAnchor="middle">
                          {e.relation}{isAgg ? ` ×${e.count}` : ''}
                        </text>
                      )}
                    </g>
                  );
                })}
              </svg>

              {nodes.map((n) => {
                const c = TYPE_COLORS[n.type] || TYPE_COLORS.Other;
                const pos = xyOf(n);
                const match = matches(n);
                const isSelected = selected?.kind === 'node' && selected.ref.id === n.id;
                const isSuper = n.kind === 'super';
                return (
                  <div key={n.id}
                    className={`tkg-node ${isSuper ? 'tkg-super' : ''} ${hover === n.id ? 'hot' : ''} ${match ? '' : 'dim'} ${isSelected ? 'sel' : ''}`}
                    style={{
                      left: pos.x - n.width / 2, top: pos.y - n.height / 2,
                      width: n.width, height: n.height,
                      background: c.fill, borderColor: c.stroke, color: c.text,
                    }}
                    title={isSuper ? `Click to expand cluster · ${n.summary}` : (n.summary || n.label)}
                    onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
                    onMouseDown={(e) => onDown(e, n)}
                    onClick={() => {
                      if (isSuper) { toggleCluster(n.cluster_id); setSelected({ kind: 'cluster', ref: clusterById[n.cluster_id] }); }
                      else setSelected({ kind: 'node', ref: n });
                    }}
                  >
                    <span className="tkg-node-type" style={{ background: c.stroke }}>{n.type[0]}</span>
                    <span className="tkg-node-label">{n.label}</span>
                    {isSuper && <span className="tkg-super-expand" title="Expand">+</span>}
                  </div>
                );
              })}
            </div>
          </div>

          <aside className="tkg-side">
            {!selected && hasClusters && (
              <ClusterTree
                clusters={clusters} hierarchy={graph?.hierarchy || []}
                collapsed={collapsed} onToggle={toggleCluster}
                onPickCluster={(c) => setSelected({ kind: 'cluster', ref: c })}
                onPickNode={(nodeId) => {
                  const real = (graph?.nodes || []).find((r) => r.id === nodeId);
                  if (real) { expandClusterAndFocus(real.cluster_id); setSelected({ kind: 'node', ref: { ...real, kind: 'entity' } }); }
                }}
                nodeById={Object.fromEntries((graph?.nodes || []).map((n) => [n.id, n]))}
              />
            )}
            {!selected && !hasClusters && (
              <div className="dim-note" style={{ padding: 12 }}>
                Click any entity or relation to see the quote from the source document.
              </div>
            )}
            {selected?.kind === 'node' && <NodeDetail node={selected.ref} edges={edges} nodeById={nodeById} onPick={setSelected} />}
            {selected?.kind === 'edge' && <EdgeDetail edge={selected.ref} nodeById={nodeById} onPick={setSelected} graph={graph} />}
            {selected?.kind === 'cluster' && (
              <ClusterDetail
                cluster={selected.ref}
                rawNodes={graph?.nodes || []} rawEdges={graph?.edges || []}
                isCollapsed={collapsed.has(selected.ref.id)}
                onToggle={() => toggleCluster(selected.ref.id)}
                onPickNode={(real) => { setCollapsed((p) => { const s = new Set(p); s.delete(selected.ref.id); return s; }); setSelected({ kind: 'node', ref: { ...real, kind: 'entity' } }); }}
                onClear={() => setSelected(null)}
              />
            )}
          </aside>
        </div>
      )}

      {note && nodes.length > 0 && <div className="dim-note" style={{ paddingTop: 10 }}>{note}</div>}
    </Modal>
  );
}


function NodeDetail({ node, edges, nodeById, onPick }) {
  const c = TYPE_COLORS[node.type] || TYPE_COLORS.Other;
  const out = edges.filter((e) => e.source === node.id);
  const inc = edges.filter((e) => e.target === node.id);
  return (
    <div className="tkg-detail">
      <div className="tkg-detail-head" style={{ borderColor: c.stroke }}>
        <span className="tkg-tag" style={{ background: c.fill, color: c.text, borderColor: c.stroke }}>{node.type}</span>
        <h4>{node.label}</h4>
      </div>
      {node.summary && <p className="tkg-summary">{node.summary}</p>}
      {node.pages?.length > 0 && (
        <div className="tkg-pages"><Icon.file width={11} height={11} /> Mentioned on page{node.pages.length === 1 ? '' : 's'} {node.pages.join(', ')}</div>
      )}
      <Relations title="Outgoing relations" rows={out} side="target" nodeById={nodeById} onPick={onPick} />
      <Relations title="Incoming relations" rows={inc} side="source" nodeById={nodeById} onPick={onPick} />
    </div>
  );
}


function EdgeDetail({ edge, nodeById, onPick, graph }) {
  const src = nodeById[edge.source]; const tgt = nodeById[edge.target];
  const aggregated = edge.count > 1;
  // For aggregated edges (touching a super-node), surface the underlying real
  // endpoints so the user can still trace the claim back to specific entities.
  const realSrc = aggregated && graph ? (graph.nodes || []).find((n) => n.id === edge.srcReal) : null;
  const realTgt = aggregated && graph ? (graph.nodes || []).find((n) => n.id === edge.tgtReal) : null;
  return (
    <div className="tkg-detail">
      <div className="tkg-detail-head"><h4>{src?.label} → {tgt?.label}</h4></div>
      <div className="tkg-rel-row">
        <span className="tkg-rel">{edge.relation}</span>
        {edge.page && <span className="dim-note">· page {edge.page}</span>}
        {aggregated && <span className="dim-note">· {edge.count} similar relations aggregated</span>}
      </div>
      {edge.quote ? (
        <blockquote className="tkg-quote">"{edge.quote}"</blockquote>
      ) : (
        <div className="dim-note" style={{ padding: '8px 0' }}>The model did not return a verbatim quote for this relation.</div>
      )}
      {aggregated && realSrc && realTgt && (
        <div className="dim-note" style={{ fontSize: 11.5 }}>
          Sample triple: <strong>{realSrc.label}</strong> → <em>{edge.relation}</em> → <strong>{realTgt.label}</strong>
        </div>
      )}
      <div className="tkg-pair">
        <button className="link-btn" onClick={() => onPick({ kind: 'node', ref: src })}>← {src?.label}</button>
        <button className="link-btn" onClick={() => onPick({ kind: 'node', ref: tgt })}>{tgt?.label} →</button>
      </div>
    </div>
  );
}


// ----- hierarchical cluster panel -----------------------------------------
// Two-level tree: super-cluster (entity type) → community → top entities. Each
// community row carries a collapse/expand toggle that matches the toggle on the
// canvas, so the panel and the graph stay in sync.
function ClusterTree({ clusters, hierarchy, collapsed, onToggle, onPickCluster, onPickNode, nodeById }) {
  const clusterById = Object.fromEntries(clusters.map((c) => [c.id, c]));
  const [open, setOpen] = useState(() => {
    const s = {};
    // Open every super-type by default — the tree is the navigation surface, so
    // hiding it behind another click would just slow the user down.
    (hierarchy || []).forEach((h) => { s[h.id] = true; });
    return s;
  });
  if (!hierarchy || hierarchy.length === 0) {
    return (
      <div className="tkg-tree">
        <div className="tkg-tree-head">All clusters ({clusters.length})</div>
        {clusters.map((c) => (
          <ClusterRow key={c.id} c={c} collapsed={collapsed.has(c.id)}
            onToggle={() => onToggle(c.id)} onPick={() => onPickCluster(c)}
            onPickNode={onPickNode} nodeById={nodeById} />
        ))}
      </div>
    );
  }
  return (
    <div className="tkg-tree">
      <div className="tkg-tree-head">Clusters by type</div>
      {hierarchy.map((sup) => {
        const isOpen = open[sup.id] !== false;
        const colour = TYPE_COLORS[sup.type] || TYPE_COLORS.Other;
        return (
          <div key={sup.id} className="tkg-tree-group">
            <button className="tkg-tree-group-head"
              onClick={() => setOpen((o) => ({ ...o, [sup.id]: !isOpen }))}
              style={{ borderLeftColor: colour.stroke }}>
              <span className="tkg-tree-caret">{isOpen ? '▾' : '▸'}</span>
              <span className="tkg-tree-group-label">{sup.label}</span>
              <span className="dim-note">{sup.children.length} cluster{sup.children.length === 1 ? '' : 's'} · {sup.size} entities</span>
            </button>
            {isOpen && sup.children.map((cid) => {
              const c = clusterById[cid];
              if (!c) return null;
              return (
                <ClusterRow key={cid} c={c} collapsed={collapsed.has(cid)}
                  onToggle={() => onToggle(cid)} onPick={() => onPickCluster(c)}
                  onPickNode={onPickNode} nodeById={nodeById} />
              );
            })}
          </div>
        );
      })}
    </div>
  );
}


function ClusterRow({ c, collapsed, onToggle, onPick, onPickNode, nodeById }) {
  const colour = TYPE_COLORS[c.dominant_type] || TYPE_COLORS.Other;
  // Show the top 3 members by name to give the user a flavour of the cluster
  // without exploding the panel for big communities.
  const top = c.members.slice(0, 3).map((m) => nodeById[m]).filter(Boolean);
  return (
    <div className="tkg-tree-row" style={{ borderLeftColor: colour.stroke }}>
      <button className="tkg-tree-row-main" onClick={onPick}>
        <span className="tkg-tag" style={{ background: colour.fill, color: colour.text, borderColor: colour.stroke }}>{c.dominant_type[0]}</span>
        <span className="tkg-tree-row-label">{c.label}</span>
        <span className="dim-note">{c.size}</span>
      </button>
      <div className="tkg-tree-row-members">
        {top.map((m) => (
          <button key={m.id} className="link-btn" onClick={() => onPickNode(m.id)} title={m.summary || m.label}>
            {m.label}
          </button>
        ))}
        {c.members.length > top.length && <span className="dim-note">+{c.members.length - top.length}</span>}
      </div>
      <button className="icon-btn sm" onClick={onToggle}
        title={collapsed ? 'Expand on canvas' : 'Collapse on canvas'}>
        {collapsed ? '+' : '−'}
      </button>
    </div>
  );
}


function ClusterDetail({ cluster, rawNodes, rawEdges, isCollapsed, onToggle, onPickNode, onClear }) {
  const colour = TYPE_COLORS[cluster.dominant_type] || TYPE_COLORS.Other;
  const idSet = new Set(cluster.members);
  const members = cluster.members.map((m) => rawNodes.find((n) => n.id === m)).filter(Boolean);
  // Bridges = edges leaving this cluster. Surface them so the user understands
  // how the cluster connects to the rest of the document.
  const bridges = rawEdges.filter((e) =>
    (idSet.has(e.source) !== idSet.has(e.target))
  ).slice(0, 12);
  const byType = cluster.type_counts || {};
  return (
    <div className="tkg-detail">
      <div className="tkg-detail-head" style={{ borderColor: colour.stroke }}>
        <span className="tkg-tag" style={{ background: colour.fill, color: colour.text, borderColor: colour.stroke }}>{cluster.dominant_type}</span>
        <h4>{cluster.label} <span className="dim-note">cluster</span></h4>
      </div>
      <div className="tkg-cluster-stats">
        <span><strong>{cluster.size}</strong> entities</span>
        <span>·</span>
        <span><strong>{cluster.internal_edges}</strong> internal</span>
        <span>·</span>
        <span><strong>{cluster.external_edges}</strong> crossing</span>
      </div>
      <div className="tkg-cluster-typebar">
        {Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([t, n]) => {
          const cc = TYPE_COLORS[t] || TYPE_COLORS.Other;
          return (
            <span key={t} className="tkg-tag" style={{ background: cc.fill, color: cc.text, borderColor: cc.stroke }}>
              {t} {n}
            </span>
          );
        })}
      </div>
      <div className="tkg-cluster-actions">
        <button className="icon-btn sm" onClick={onToggle}>
          {isCollapsed ? 'Expand on canvas' : 'Collapse on canvas'}
        </button>
        <button className="icon-btn sm" onClick={onClear}>Close</button>
      </div>
      <div className="tkg-rels-head">Members ({members.length})</div>
      <div className="tkg-cluster-members">
        {members.map((m) => (
          <button key={m.id} className="link-btn" onClick={() => onPickNode(m)} title={m.summary || m.label}>
            {m.label}
          </button>
        ))}
      </div>
      {bridges.length > 0 && (
        <>
          <div className="tkg-rels-head">Bridges to other clusters</div>
          {bridges.map((e, i) => {
            const inside = idSet.has(e.source) ? e.source : e.target;
            const outside = idSet.has(e.source) ? e.target : e.source;
            const a = rawNodes.find((n) => n.id === inside);
            const b = rawNodes.find((n) => n.id === outside);
            if (!a || !b) return null;
            return (
              <div key={i} className="tkg-rel-item">
                <button className="link-btn" onClick={() => onPickNode(a)}>{a.label}</button>
                <span className="tkg-rel">{e.relation}</span>
                <button className="link-btn" onClick={() => onPickNode(b)}>{b.label}</button>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}


function Relations({ title, rows, side, nodeById, onPick }) {
  if (!rows.length) return null;
  return (
    <div className="tkg-rels">
      <div className="tkg-rels-head">{title}</div>
      {rows.map((e, i) => {
        const other = nodeById[e[side]];
        if (!other) return null;
        return (
          <div key={i} className="tkg-rel-item">
            <span className="tkg-rel">{e.relation}</span>
            <button className="link-btn" onClick={() => onPick({ kind: 'node', ref: other })}>{other.label}</button>
            <span className="dim-note">p.{e.page}</span>
          </div>
        );
      })}
    </div>
  );
}


// Deterministic [-0.5, 0.5) jitter from an index — keeps re-renders of the same
// graph stable (no Math.random flicker) while breaking up the seed grid.
function jitter(i, salt) {
  const s = Math.sin((i + 1) * salt) * 43758.5453;
  return s - Math.floor(s) - 0.5;
}

// ----- layout: Fruchterman–Reingold with cluster cohesion. When `clusters`
// (list of `{id, members:[nodeId]}` for *expanded* clusters) is non-empty, the
// seed places members near per-cluster centroids and an extra attractive force
// pulls each member toward its cluster's running centroid every iteration.
// That makes communities visibly clump together instead of mixing back into
// the FR ball — which was the whole "no clustering" complaint.
function layoutClustered(nodes, edges, w, h, clusters) {
  const n = nodes.length;
  if (n === 0) return;

  // node id → cluster id (only for nodes that belong to an *expanded* cluster).
  // Super-nodes and singletons aren't in `clusterOf` so they float freely.
  const clusterOf = {};
  (clusters || []).forEach((c) => c.members.forEach((m) => { clusterOf[m] = c.id; }));

  // Lay out bucket centroids on a coarse grid. The "loose" bucket gathers
  // super-nodes + isolates so they don't collide with the clustered region.
  const buckets = [];
  (clusters || []).forEach((c) => buckets.push({ id: c.id, members: c.members.slice() }));
  const loose = nodes.filter((node) => !clusterOf[node.id]).map((node) => node.id);
  if (loose.length) buckets.push({ id: '__loose__', members: loose });
  buckets.sort((a, b) => b.members.length - a.members.length);

  const bcols = Math.max(1, Math.round(Math.sqrt(buckets.length * (w / h))));
  const bcellW = w / bcols;
  const bcellH = h / Math.max(1, Math.ceil(buckets.length / bcols));
  const centroids = {};
  buckets.forEach((b, i) => {
    centroids[b.id] = {
      x: ((i % bcols) + 0.5) * bcellW,
      y: (Math.floor(i / bcols) + 0.5) * bcellH,
    };
  });

  // Seed each node near its bucket centroid on a deterministic spiral so very
  // dense clusters still spread; jitter keeps neighbours from sitting in
  // identical spots, which causes the FR algorithm to divide by ~0.
  const nodeMap = Object.fromEntries(nodes.map((nd) => [nd.id, nd]));
  buckets.forEach((b) => {
    const c = centroids[b.id];
    const radius = Math.min(bcellW, bcellH) * 0.32;
    b.members.forEach((mid, j) => {
      const nd = nodeMap[mid];
      if (!nd) return;
      const t = (j + 1) / b.members.length;
      const r = radius * Math.sqrt(t);
      const a = j * 2.39996;
      nd.x = c.x + Math.cos(a) * r + jitter(j, 12.9898) * 10;
      nd.y = c.y + Math.sin(a) * r + jitter(j, 78.233) * 10;
    });
  });

  const k = Math.sqrt((w * h) / n);
  const repulse = k * k * 1.2;
  const iter = Math.min(500, 260 + n * 2);
  let temp = Math.max(w, h) / 6;
  const cool = temp / iter;
  const idx = Object.fromEntries(nodes.map((nd, i) => [nd.id, i]));
  const cohesion = 0.06;  // gentle pull toward the cluster's current centroid

  for (let it = 0; it < iter; it++) {
    const disp = nodes.map(() => ({ x: 0, y: 0 }));

    // Running per-cluster centroids — recomputed each iter so the cohesion
    // force tracks the cluster as it migrates.
    const cur = {};
    (clusters || []).forEach((c) => {
      let sx = 0, sy = 0, cnt = 0;
      c.members.forEach((m) => { const nd = nodeMap[m]; if (nd) { sx += nd.x; sy += nd.y; cnt++; } });
      if (cnt) cur[c.id] = { x: sx / cnt, y: sy / cnt };
    });

    // Repulsion — slightly softer between same-cluster nodes so they pack
    // tighter than across cluster boundaries.
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = nodes[i].x - nodes[j].x;
        const dy = nodes[i].y - nodes[j].y;
        const dist = Math.hypot(dx, dy) || 0.01;
        const same = clusterOf[nodes[i].id] && clusterOf[nodes[i].id] === clusterOf[nodes[j].id];
        const f = (repulse / dist) * (same ? 0.55 : 1.0);
        const fx = (dx / dist) * f; const fy = (dy / dist) * f;
        disp[i].x += fx; disp[i].y += fy;
        disp[j].x -= fx; disp[j].y -= fy;
      }
    }
    // Edge attraction
    for (const e of edges) {
      const i = idx[e.source]; const j = idx[e.target];
      if (i == null || j == null) continue;
      const dx = nodes[i].x - nodes[j].x;
      const dy = nodes[i].y - nodes[j].y;
      const dist = Math.hypot(dx, dy) || 0.01;
      const f = Math.min((dist * dist) / k, dist * 6);
      const fx = (dx / dist) * f; const fy = (dy / dist) * f;
      disp[i].x -= fx; disp[i].y -= fy;
      disp[j].x += fx; disp[j].y += fy;
    }
    // Cluster cohesion: gentle pull toward each node's cluster centroid
    for (let i = 0; i < n; i++) {
      const cid = clusterOf[nodes[i].id];
      if (!cid || !cur[cid]) continue;
      disp[i].x += (cur[cid].x - nodes[i].x) * cohesion;
      disp[i].y += (cur[cid].y - nodes[i].y) * cohesion;
    }
    // Apply with cooling
    for (let i = 0; i < n; i++) {
      const d = disp[i];
      const dist = Math.hypot(d.x, d.y) || 0.01;
      const limit = Math.min(dist, temp);
      nodes[i].x += (d.x / dist) * limit;
      nodes[i].y += (d.y / dist) * limit;
      const padX = (nodes[i].width || 120) / 2 + 16;
      const padY = (nodes[i].height || NODE_HEIGHT) / 2 + 12;
      nodes[i].x = Math.max(padX, Math.min(w - padX, nodes[i].x));
      nodes[i].y = Math.max(padY, Math.min(h - padY, nodes[i].y));
    }
    temp = Math.max(0.5, temp - cool);
  }

  // Final de-overlap pass: any pills whose boxes still intersect get nudged
  // apart along the axis of least penetration so labels remain legible.
  for (let pass = 0; pass < 16; pass++) {
    let moved = false;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = nodes[i]; const b = nodes[j];
        const mw = ((a.width || 120) + (b.width || 120)) / 2 + 16;
        const mh = ((a.height || NODE_HEIGHT) + (b.height || NODE_HEIGHT)) / 2 + 14;
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

