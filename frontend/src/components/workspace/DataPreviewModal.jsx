import { useEffect, useMemo, useState } from 'react';
import Modal from '../common/Modal';
import { Icon } from '../common/Icons';
import { previewFile } from '../../lib/api';

// Real preview of an uploaded file (§12.4): table for CSV/Excel/SQLite, text for JSON/MD/TXT.
// For SQLite (.db) files, the backend also returns a `tables` list so the user can switch between
// tables in-place; we re-request with `?table=` whenever the selection changes.
export default function DataPreviewModal({ sid, file, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [table, setTable] = useState(null);  // current table inside a .db file

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(''); setData(null);
    previewFile(sid, file.id, 100, table ? { table } : {})
      .then((d) => { if (alive) { setData(d); if (d?.table && !table) setTable(d.table); } })
      .catch((e) => { if (alive) setError(String(e.message || e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [sid, file.id, table]);

  const tables = data?.tables;

  return (
    <Modal size="lg" onClose={onClose} title={
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Icon.eye width={16} height={16} /> {file.name}
        <span className="kind-badge">{(file.kind || 'file').toUpperCase()}</span>
        {data?.total_rows != null && <span className="dim-note">{data.total_rows} rows</span>}
      </span>
    }>
      <div className="preview-wrap">
        {tables && tables.length > 0 && (
          <div className="table-toolbar" style={{ marginBottom: 10 }}>
            <label className="ex-sheet">table
              <select value={table || tables[0]} onChange={(e) => setTable(e.target.value)}>
                {tables.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <span className="dim-note">{tables.length} table{tables.length === 1 ? '' : 's'} in this database</span>
          </div>
        )}
        {loading && <div className="dim-note" style={{ padding: 24 }}>Loading preview…</div>}
        {error && <div className="dd-error"><Icon.alert width={15} height={15} /> {error}</div>}
        {data?.kind === 'table' && <DataTable columns={data.columns} rows={data.rows} total={data.total_rows} />}
        {data?.kind === 'text' && <pre className="json-pre">{data.text || '(empty file)'}</pre>}
        {data?.kind === 'unsupported' && <div className="dim-note" style={{ padding: 24 }}>{data.note}</div>}
      </div>
    </Modal>
  );
}

// interactive rows: sort + filter + pagination
const PAGE = 12;
function DataTable({ columns, rows, total }) {
  const [sort, setSort] = useState({ col: null, dir: 1 });
  const [q, setQ] = useState('');
  const [page, setPage] = useState(0);
  const cell = (c) => (c === null || c === undefined ? '' : String(c));

  const filtered = useMemo(() => {
    let r = rows;
    if (q.trim()) {
      const t = q.toLowerCase();
      r = r.filter((row) => row.some((c) => cell(c).toLowerCase().includes(t)));
    }
    if (sort.col != null) {
      const i = sort.col;
      r = [...r].sort((a, b) => {
        const x = a[i], y = b[i];
        const n = parseFloat(x), m = parseFloat(y);
        const cmp = !isNaN(n) && !isNaN(m) ? n - m : cell(x).localeCompare(cell(y));
        return cmp * sort.dir;
      });
    }
    return r;
  }, [rows, q, sort]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
  const pageRows = filtered.slice(page * PAGE, page * PAGE + PAGE);
  const setSortCol = (i) => { setSort((s) => ({ col: i, dir: s.col === i ? -s.dir : 1 })); setPage(0); };

  return (
    <div>
      <div className="table-toolbar">
        <div className="mini-search">
          <Icon.search width={14} height={14} />
          <input placeholder="Filter rows…" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} />
        </div>
        <span className="dim-note">{filtered.length} shown{total != null && rows.length < total ? ` · preview of ${total}` : ''}</span>
      </div>
      <div className="preview-table-wrap">
        <table className="answer-table sortable">
          <thead><tr>{columns.map((c, i) => (
            <th key={i} onClick={() => setSortCol(i)}>
              {c}<span className="sort-ind">{sort.col === i ? (sort.dir === 1 ? '▲' : '▼') : '↕'}</span>
            </th>
          ))}</tr></thead>
          <tbody>
            {pageRows.map((row, i) => (
              <tr key={i}>{row.map((c, j) => <td key={j}>{c === null || c === '' ? <span className="null-cell">∅</span> : String(c)}</td>)}</tr>
            ))}
            {pageRows.length === 0 && <tr><td colSpan={columns.length} className="dim-note" style={{ textAlign: 'center', padding: 18 }}>No matching rows.</td></tr>}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="pager">
          <button className="icon-btn" disabled={page === 0} onClick={() => setPage((p) => p - 1)}><Icon.chevron width={16} height={16} style={{ transform: 'rotate(90deg)' }} /></button>
          <span className="dim-note">Page {page + 1} / {pages}</span>
          <button className="icon-btn" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}><Icon.chevron width={16} height={16} style={{ transform: 'rotate(-90deg)' }} /></button>
        </div>
      )}
    </div>
  );
}
