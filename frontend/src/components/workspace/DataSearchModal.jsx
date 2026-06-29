import { useMemo, useState } from 'react';
import Modal from '../common/Modal';
import { Icon } from '../common/Icons';
import { FILES } from '../../data/mockData';

// Cross-file value search — the UI face of the `extract_info` tool. Scans every
// context file (CSV rows, JSON records, SQLite samples, markdown) for a keyword
// and shows where it occurs, so the user can find data without knowing the file.
export default function DataSearchModal({ onClose }) {
  const [q, setQ] = useState('');
  const results = useMemo(() => (q.trim().length < 2 ? [] : search(q.trim())), [q]);
  const total = results.reduce((n, r) => n + r.hits.length, 0);

  return (
    <Modal size="md" onClose={onClose} title={
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}><Icon.search width={16} height={16} /> Search across all data</span>
    }>
      <div className="ds-body">
        <div className="search ds-search">
          <Icon.search />
          <input autoFocus placeholder="Find a value across every file… e.g. Design, 555, Workshop" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="dim-note ds-hint">Powered by <code className="inline-code">extract_info</code> — searches CSV / JSON / SQLite / docs at once.</div>

        {q.trim().length >= 2 && (
          <div className="ds-summary">{total} match{total !== 1 ? 'es' : ''} in {results.length} file{results.length !== 1 ? 's' : ''}</div>
        )}

        <div className="ds-results">
          {results.map((r) => (
            <div className="ds-file" key={r.file}>
              <div className="ds-file-head"><Icon.file width={13} height={13} /> {r.file} <span className="dim-note">{r.hits.length}</span></div>
              {r.hits.map((h, i) => (
                <div className="ds-hit" key={i}>
                  <span className="ds-loc">{h.loc}</span>
                  <span className="ds-snippet" dangerouslySetInnerHTML={{ __html: h.snippet }} />
                </div>
              ))}
            </div>
          ))}
          {q.trim().length >= 2 && total === 0 && <div className="result-empty" style={{ padding: 30 }}><div className="dim-note">No matches for “{q}”.</div></div>}
        </div>
      </div>
    </Modal>
  );
}

function hl(text, q) {
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return esc(text);
  const start = Math.max(0, i - 24);
  const pre = (start > 0 ? '…' : '') + esc(text.slice(start, i));
  return pre + '<mark>' + esc(text.slice(i, i + q.length)) + '</mark>' + esc(text.slice(i + q.length, i + q.length + 40));
}
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

function search(q) {
  const t = q.toLowerCase();
  const out = [];
  for (const f of FILES) {
    const hits = [];
    if (f.kind === 'csv') {
      f.preview.rows.forEach((row, ri) => {
        row.forEach((cell, ci) => {
          if (String(cell).toLowerCase().includes(t)) hits.push({ loc: `row ${ri + 1} · ${f.preview.columns[ci]}`, snippet: hl(String(cell), q) });
        });
      });
    } else if (f.kind === 'json') {
      (f.json || []).forEach((rec, ri) => {
        const s = JSON.stringify(rec);
        if (s.toLowerCase().includes(t)) hits.push({ loc: `record ${ri + 1}`, snippet: hl(s, q) });
      });
    } else if (f.kind === 'sqlite') {
      (f.tables || []).forEach((tb) => tb.sample?.forEach((row, ri) => {
        if (row.some((c) => String(c).toLowerCase().includes(t))) hits.push({ loc: `${tb.name} · sample ${ri + 1}`, snippet: hl(row.join(', '), q) });
      }));
    } else if (f.kind === 'md') {
      (f.markdown || '').split('\n').forEach((ln, li) => {
        if (ln.toLowerCase().includes(t)) hits.push({ loc: `line ${li + 1}`, snippet: hl(ln, q) });
      });
    }
    if (hits.length) out.push({ file: f.name, hits: hits.slice(0, 6) });
  }
  return out;
}
