import { Fragment, useEffect, useRef, useState } from 'react';
import Modal from '../common/Modal';
import { Icon } from '../common/Icons';
import { API_BASE, createSession, exploreStats, getSession, listFiles, uploadFile } from '../../lib/api';

// §12.2c Explore — a "data-scientist" view of a CSV/Excel file: per-column
// distributions (histograms / top categories), a numeric correlation heatmap,
// and a missingness map. Deterministic stats from POST /api/sessions/{id}/explore.
const TABS = [
  { id: 'columns', label: 'Columns', icon: Icon.bars },
  { id: 'correlation', label: 'Correlation', icon: Icon.network },
  { id: 'missing', label: 'Missingness', icon: Icon.filter },
];

export default function ExploreModal({ sid: propSid, onClose }) {
  const [sid, setSid] = useState(null);
  const [files, setFiles] = useState([]);
  const [active, setActive] = useState(null);
  const [stats, setStats] = useState(null);
  const [tab, setTab] = useState('columns');
  const [busy, setBusy] = useState('init');   // 'init' | 'upload' | 'explore' | ''
  const [error, setError] = useState('');
  const fileInput = useRef(null);

  // Use the shared workspace session when provided; otherwise a dedicated one.
  useEffect(() => {
    (async () => {
      try {
        let id = propSid;
        if (!id) {
          id = localStorage.getItem('das-dd-session');
          if (id && !(await getSession(id))) id = null;
          if (!id) { id = (await createSession('Data Doctor')).id; localStorage.setItem('das-dd-session', id); }
        }
        setSid(id);
        setFiles(await listFiles(id));
      } catch (e) {
        setError(`${e.message || e} — is the backend running on ${API_BASE}?`);
      } finally { setBusy(''); }
    })();
  }, [propSid]);

  const explore = async (filename, sheet, table) => {
    setBusy('explore'); setError(''); setActive(filename);
    if (!sheet && !table) setStats(null);
    try {
      setStats(await exploreStats(sid, filename, sheet, table));
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
  };

  const onPick = async (fileList) => {
    const list = Array.from(fileList || []);
    if (!sid || list.length === 0) return;
    setBusy('upload'); setError('');
    try {
      let last;
      for (const f of list) last = await uploadFile(sid, f);
      setFiles(await listFiles(sid));
      if (last) await explore(last.name);
    } catch (e) { setError(String(e.message || e)); setBusy(''); }
  };

  return (
    <Modal size="lg" onClose={onClose} title={
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Icon.bars width={16} height={16} /> Explore
        <span className="dim-note">distributions · correlation · missingness</span>
      </span>
    }>
      <div className="dd">
        <aside className="dd-rail">
          <div className="dd-rail-head">Files</div>
          <div className="dd-files">
            {files.length === 0 && <div className="dd-muted">No files yet — upload one.</div>}
            {files.map((f) => (
              <button key={f.id} className={`dd-file ${active === f.name ? 'on' : ''}`}
                onClick={() => explore(f.name)} disabled={busy === 'explore'}>
                <Icon.table width={14} height={14} />
                <span className="dd-file-name" title={f.name}>{f.name}</span>
                <span className="dd-file-sz">{f.rowCount != null ? `${f.rowCount}r` : f.size}</span>
              </button>
            ))}
          </div>
          <button className="btn btn-ghost btn-sm dd-upload" onClick={() => fileInput.current?.click()}
            disabled={!sid || busy === 'upload'} title="Upload a CSV, Excel or SQLite file">
            <Icon.upload width={14} height={14} /> {busy === 'upload' ? 'Uploading…' : 'Upload file'}
          </button>
          <input ref={fileInput} type="file" accept=".csv,.tsv,.xlsx,.xls,.db,.sqlite,.sqlite3" multiple hidden
            onChange={(e) => { onPick(e.target.files); e.target.value = ''; }} />
        </aside>

        <section className="dd-main">
          {error && <div className="dd-error"><Icon.alert width={15} height={15} /> {error}</div>}
          {busy === 'init' && <div className="dd-muted dd-center">Connecting to the backend…</div>}

          {!error && busy !== 'init' && !stats && (
            <div className="dd-empty">
              <Icon.bars width={30} height={30} />
              <div>Upload a <b>CSV</b>, <b>Excel</b>, or <b>SQLite</b> file (or pick one on the left).<br />
                Explore shows every column's distribution, how columns correlate, and where data is missing.</div>
            </div>
          )}

          {stats && (
            <>
              <div className="dd-summary">
                <div className="dd-sum-file"><Icon.table width={15} height={15} /> {active}</div>
                <span className="dd-stat"><b>{stats.rows}</b> rows</span>
                <span className="dd-stat"><b>{stats.columns}</b> cols</span>
                <span className="dd-stat"><b>{stats.numeric_columns}</b> numeric</span>
                <span className="spacer" />
                {stats.sheets && stats.sheets.length > 1 && (
                  <label className="ex-sheet">sheet
                    <select value={stats.sheet || stats.sheets[0]} onChange={(e) => explore(active, e.target.value)}>
                      {stats.sheets.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </label>
                )}
                {stats.tables && stats.tables.length > 1 && (
                  <label className="ex-sheet">table
                    <select value={stats.table || stats.tables[0]}
                      onChange={(e) => explore(active, undefined, e.target.value)}>
                      {stats.tables.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </label>
                )}
              </div>

              <div className="result-tabs ex-tabs">
                {TABS.map((t) => (
                  <button key={t.id} className={`result-tab ${tab === t.id ? 'active' : ''}`} onClick={() => setTab(t.id)}>
                    <t.icon width={14} height={14} style={{ verticalAlign: '-2px', marginRight: 5 }} />{t.label}
                  </button>
                ))}
              </div>

              {tab === 'columns' && (
                <div className="ex-grid">
                  {stats.column_stats.map((c) => <ColumnCard key={c.column} c={c} />)}
                </div>
              )}
              {tab === 'correlation' && (
                stats.correlation
                  ? <><Heatmap corr={stats.correlation} /><ScatterPairs pairs={stats.scatter_suggestions} /></>
                  : <div className="dd-muted dd-center">Need at least two numeric columns for a correlation matrix.</div>
              )}
              {tab === 'missing' && <Missingness rows={stats.missingness} />}
            </>
          )}
        </section>
      </div>
    </Modal>
  );
}

function ColumnCard({ c }) {
  return (
    <div className="ex-card">
      <div className="ex-card-head">
        <span className="ex-col-name" title={c.column}>{c.column}</span>
        <span className={`ex-kind ${c.kind}`}>{c.kind}</span>
      </div>
      <div className="ex-card-meta">
        <span>{c.unique} unique</span>
        {c.missing > 0 && <span className="warn">{(c.missing_pct * 100).toFixed(0)}% missing</span>}
        {c.outliers > 0 && <span>{c.outliers} outliers</span>}
      </div>
      {c.kind === 'numeric' && c.histogram && (
        <>
          <Histogram hist={c.histogram} />
          <div className="ex-num-stats">
            <span>min <b>{fmt(c.min)}</b></span><span>med <b>{fmt(c.median)}</b></span>
            <span>max <b>{fmt(c.max)}</b></span><span>μ <b>{fmt(c.mean)}</b></span>
          </div>
        </>
      )}
      {c.kind === 'datetime' && (
        <div className="ex-date">{c.min} <span className="dim-note">→</span> {c.max}</div>
      )}
      {(c.kind === 'categorical' || c.kind === 'empty') && (
        c.top && c.top.length ? <TopBars top={c.top} /> : <div className="dd-muted">no values</div>
      )}
    </div>
  );
}

function Histogram({ hist }) {
  const counts = hist.counts || [];
  const max = Math.max(1, ...counts);
  return (
    <div className="ex-hist" aria-hidden>
      {counts.map((n, i) => (
        <div key={i} className="ex-hbar" style={{ height: `${(n / max) * 100}%` }} title={String(n)} />
      ))}
    </div>
  );
}

function TopBars({ top }) {
  const max = Math.max(1, ...top.map((t) => t.count));
  const shown = top.slice(0, 6);
  const rest = top.length - shown.length;
  return (
    <div className="ex-tops">
      {shown.map((t, i) => (
        <div className="ex-top" key={i}>
          <span className="ex-top-label" title={t.value}>{t.value || '∅'}</span>
          <span className="ex-top-track"><span style={{ width: `${(t.count / max) * 100}%` }} /></span>
          <span className="ex-top-n">{t.count}</span>
        </div>
      ))}
      {rest > 0 && <div className="ex-top-more">+{rest} more value{rest > 1 ? 's' : ''}</div>}
    </div>
  );
}

function Heatmap({ corr }) {
  const { columns, matrix } = corr;
  return (
    <div className="ex-heat-wrap">
      <div className="ex-heat" style={{ gridTemplateColumns: `minmax(70px,110px) repeat(${columns.length}, minmax(34px, 1fr))` }}>
        <div className="ex-heat-corner" />
        {columns.map((c) => <div key={c} className="ex-heat-colhead" title={c}>{c}</div>)}
        {matrix.map((row, i) => (
          <Fragment key={i}>
            <div className="ex-heat-rowhead" title={columns[i]}>{columns[i]}</div>
            {row.map((v, j) => (
              <div key={j} className="ex-heat-cell" style={{ background: corrColor(v) }}
                title={`${columns[i]} ~ ${columns[j]}: ${v == null ? 'n/a' : v}`}>
                {v == null ? '' : v.toFixed(2)}
              </div>
            ))}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function ScatterPairs({ pairs }) {
  if (!pairs || pairs.length === 0) return null;
  return (
    <div className="ex-pairs">
      <div className="dd-section-label">Strongest relationships</div>
      {pairs.map((p, i) => (
        <div className="ex-pair" key={i}>
          <span className="ex-pair-cols">{p.x} <span className="dim-note">×</span> {p.y}</span>
          <span className="ex-top-track"><span className={p.r >= 0 ? 'pos' : 'neg'} style={{ width: `${Math.abs(p.r) * 100}%` }} /></span>
          <span className="ex-pair-r">r = {p.r}</span>
        </div>
      ))}
    </div>
  );
}

function Missingness({ rows }) {
  const any = rows.some((m) => m.missing_pct > 0);
  return (
    <div className="ex-miss">
      {!any && <div className="dd-clean"><Icon.check width={16} height={16} /> No missing values anywhere.</div>}
      {rows.map((m) => (
        <div className="ex-miss-row" key={m.column}>
          <span className="ex-miss-label" title={m.column}>{m.column}</span>
          <span className="ex-miss-track">
            <span className={m.missing_pct > 0.2 ? 'hi' : ''} style={{ width: `${Math.max(m.missing_pct * 100, m.missing_pct > 0 ? 2 : 0)}%` }} />
          </span>
          <span className="ex-miss-n">{(m.missing_pct * 100).toFixed(m.missing_pct >= 0.1 ? 0 : 1)}%</span>
        </div>
      ))}
    </div>
  );
}

const fmt = (v) => (v == null ? '—' : (Math.abs(v) >= 1000 || (v % 1 === 0) ? String(v) : v.toFixed(2)));
const corrColor = (v) => (v == null ? 'transparent'
  : v >= 0 ? `rgba(18,168,156,${Math.abs(v) * 0.85})` : `rgba(230,0,126,${Math.abs(v) * 0.85})`);
