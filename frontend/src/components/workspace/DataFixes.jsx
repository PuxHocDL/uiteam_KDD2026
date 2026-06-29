import { useEffect, useState } from 'react';
import { Icon } from '../common/Icons';
import { useConfirm } from '../common/ConfirmDialog';
import { useToast } from '../common/Toast';
import {
  analyzeQuality, applyQualityFix, clearAnalyses, deleteAnalysis,
  downloadUrl, getAnalysis, listAnalyses, previewFile,
} from '../../lib/api';

// §12.1 — AI data fixes, embedded in the Results panel (no separate modal).
// The LLM both finds issues AND writes the pandas snippet that implements each fix.
// Cards therefore render generically: title + rationale + code (read-only, editable
// via the Edit toggle) → Fix runs a dry-run preview → Approve commits *_clean.csv.
const SEV_ICON = { error: Icon.alert, warn: Icon.alert, info: Icon.spark };

// Persist per-analysis applied/skipped state so switching through the history
// keeps the green "applied" frame on cards the user already approved.
const doneKey = (sid, aid) => `das-fixes-done:${sid}:${aid}`;
const loadDone = (sid, aid) => {
  if (!sid || !aid) return {};
  try { return JSON.parse(localStorage.getItem(doneKey(sid, aid)) || '{}') || {}; }
  catch { return {}; }
};
const saveDone = (sid, aid, done) => {
  if (!sid || !aid) return;
  try { localStorage.setItem(doneKey(sid, aid), JSON.stringify(done || {})); }
  catch { /* quota */ }
};
const clearDone = (sid, aid) => {
  if (!sid || !aid) return;
  try { localStorage.removeItem(doneKey(sid, aid)); } catch { /* ignore */ }
};

export default function DataFixes({ sid, files = [], settings, onChanged }) {
  const tabular = files.filter((f) => f.kind === 'csv' || f.kind === 'excel' || f.kind === 'sqlite');
  const confirm = useConfirm();
  const toast = useToast();
  const [file, setFile] = useState('');        // selected in the dropdown
  const [active, setActive] = useState('');     // file whose fixes are shown
  const [activeTable, setActiveTable] = useState(''); // for .db files
  const [report, setReport] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [note, setNote] = useState('');
  const [diag, setDiag] = useState(null);   // { raw_count, parse_ok, dropped, dropped_examples } from the server
  const [done, setDone] = useState({});       // id -> result | 'skipped'
  const [preview, setPreview] = useState({});  // id -> { code, result }
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [agentFilter, setAgentFilter] = useState(new Set()); // empty = show all
  const [focusId, setFocusId] = useState('');         // expanded fix card overlay
  const [showHistory, setShowHistory] = useState(false);
  const [restoredAt, setRestoredAt] = useState('');    // banner for a re-opened analysis
  const [analysisId, setAnalysisId] = useState('');    // server id — keys persisted done state
  // For .db files: list of tables in the currently-selected file (dropdown), and the chosen table.
  const [pickerTables, setPickerTables] = useState([]);
  const [pickerTable, setPickerTable] = useState('');

  // When the user picks a .db file from the dropdown, fetch its table list once.
  useEffect(() => {
    if (!file) { setPickerTables([]); setPickerTable(''); return; }
    const meta = files.find((f) => f.name === file);
    if (!meta || meta.kind !== 'sqlite') { setPickerTables([]); setPickerTable(''); return; }
    let alive = true;
    previewFile(sid, meta.id, 1).then((d) => {
      if (!alive) return;
      const ts = d?.tables || [];
      setPickerTables(ts);
      setPickerTable(ts[0] || '');
    }).catch(() => { if (alive) { setPickerTables([]); setPickerTable(''); } });
    return () => { alive = false; };
  }, [sid, file, files]);

  // Persist `done` to localStorage whenever it changes, keyed by analysis id.
  useEffect(() => { saveDone(sid, analysisId, done); }, [sid, analysisId, done]);

  // Load the LLM's fix recommendations (only AFTER the agent finished analyzing).
  const loadFixes = async (filename, table) => {
    setBusy('analyze'); setError(''); setNote(''); setActive(filename); setRestoredAt('');
    if (table !== undefined) setActiveTable(table || '');
    setReport(null); setSuggestions([]); setDiag(null); setDone({}); setPreview({}); setAnalysisId('');
    try {
      const ep = settings?.endpoint || {};
      const creds = { model: ep.model, api_base: ep.apiBase, api_key: ep.apiKey, api_version: ep.apiVersion || '' };
      const tbl = table !== undefined ? table : activeTable;
      const res = await analyzeQuality(sid, filename, creds, tbl ? { table: tbl } : {});
      setReport(res.report); setSuggestions(res.suggestions || []); setNote(res.note || ''); setDiag(res.diag || null);
      const aid = res.analysis_id || '';
      setAnalysisId(aid);
      // Fresh analyze on the same file → resume any approvals the user had already
      // committed (their *_clean.csv lives on disk; the green frame should match).
      if (aid) setDone(loadDone(sid, aid));
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
  };

  // Open a previously-saved analysis (read-only review until Re-analyze).
  const restoreAnalysis = async (aid) => {
    setBusy('analyze'); setError('');
    try {
      const a = await getAnalysis(sid, aid);
      setActive(a.filename); setReport(a.report); setSuggestions(a.suggestions || []);
      setNote(a.note || ''); setDiag(a.diag || null); setPreview({});
      setAnalysisId(a.id || aid);
      setDone(loadDone(sid, a.id || aid));   // restore prior approvals for this analysis
      setRestoredAt(a.when || ''); setShowHistory(false);
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
  };

  // Run the analysis directly against the /quality endpoint. This is deliberately
  // SELF-CONTAINED — it must never post into the main chat (the whole point of the
  // Data Doctor tab): loadFixes() renders the report + fix cards right here.
  const startAnalyze = () => {
    if (!file) return;
    loadFixes(file, pickerTable);
  };

  const previewFix = async (sug, codeOverride) => {
    const code = (codeOverride ?? sug.pandas_code ?? '').trim();
    if (!code) { setError('No pandas code to run for this fix.'); return; }
    setBusy(sug.id); setError('');
    try {
      const res = await applyQualityFix(sid, active, { pandas_code: code }, true, activeTable ? { table: activeTable } : {});
      setPreview((p) => ({ ...p, [sug.id]: { code, result: res.result } }));
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
  };

  const approveFix = async (sug) => {
    const pv = preview[sug.id];
    if (!pv) return;
    setBusy(sug.id); setError('');
    try {
      const res = await applyQualityFix(sid, active, { pandas_code: pv.code }, false, activeTable ? { table: activeTable } : {});
      setDone((d) => ({ ...d, [sug.id]: res.result }));
      setPreview((p) => { const n = { ...p }; delete n[sug.id]; return n; });
      setActive(res.file.name);    // chain further fixes onto the cleaned copy
      setActiveTable('');          // *_clean.csv has no concept of a sqlite table
      onChanged?.();               // refresh the workspace Files list
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
  };

  const cancelPreview = (sug) => setPreview((p) => { const n = { ...p }; delete n[sug.id]; return n; });

  const activeMeta = files.find((f) => f.name === active);
  const sevCount = { error: 0, warn: 0, info: 0 };
  suggestions.forEach((s) => { sevCount[s.severity] = (sevCount[s.severity] || 0) + 1; });

  return (
    <div className="fixes">
      <div className="fixes-head">
        <span className="title"><Icon.brain width={15} height={15} /> AI data fixes</span>
        <div className="fixes-controls">
          <select className="fixes-file" value={file} onChange={(e) => setFile(e.target.value)} disabled={busy === 'analyze'}>
            <option value="">{tabular.length ? 'Choose a file…' : 'Upload a CSV / Excel / SQLite in Files'}</option>
            {tabular.map((f) => <option key={f.id} value={f.name}>{f.name}</option>)}
          </select>
          {pickerTables.length > 0 && (
            <select className="fixes-file" value={pickerTable}
              onChange={(e) => setPickerTable(e.target.value)}
              disabled={busy === 'analyze'}
              title="Table inside this SQLite database">
              {pickerTables.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          )}
          <button className="btn btn-primary btn-sm" title="Analyze data quality (runs here, not in the chat)" onClick={startAnalyze} disabled={!file || busy === 'analyze'}>
            {busy === 'analyze' ? 'Analyzing…' : <><Icon.brain width={13} height={13} /> Analyze</>}
          </button>
          <button className="icon-btn" title="View past analyses for this workspace" onClick={() => setShowHistory(true)}>
            <Icon.list width={14} height={14} />
          </button>
          {activeMeta && <a className="icon-btn" title="Download this file" href={downloadUrl(sid, activeMeta.id)}><Icon.download width={14} height={14} /></a>}
        </div>
      </div>

      <div className="fixes-body">
        {error && <div className="dd-error"><Icon.alert width={15} height={15} /> {error}</div>}
        {busy === 'analyze' && <div className="dd-muted dd-center"><span className="up-spin" style={{ display: 'inline-block', verticalAlign: '-2px', marginRight: 8 }} />Analyzing your data — finding issues &amp; fixes…</div>}

        {!active && busy !== 'analyze' && !error && (
          <div className="dd-muted" style={{ padding: '10px 2px' }}>
            Pick a file above and click <b>Analyze</b> — the issues &amp; fixes appear right here, in this tab. It runs on its own and <b>won't post into your chat</b>. Each fix is a pandas snippet you can preview, edit, then approve.
          </div>
        )}

        {busy !== 'analyze' && report && (
          <>
            {restoredAt && (
              <div className="dd-note dd-restored">
                <Icon.reset width={14} height={14} />
                <div style={{ flex: 1 }}>
                  Restored from a saved analysis ({new Date(restoredAt).toLocaleString()}). Re-analyze to refresh against the current file.
                </div>
                <button className="icon-btn" title="Dismiss" onClick={() => setRestoredAt('')}><Icon.x width={13} height={13} /></button>
              </div>
            )}
            <div className="fixes-summary">
              <span className="dd-stat"><b>{report.rows}</b> rows</span>
              <span className="dd-stat"><b>{report.columns}</b> cols</span>
              <span className={`dd-stat ${report.duplicate_rows ? 'warn' : ''}`}><b>{report.duplicate_rows}</b> dupes</span>
              <span className="spacer" />
              {['error', 'warn', 'info'].map((s) => sevCount[s] > 0 && <span key={s} className={`sev ${s}`}>{sevCount[s]} {s}</span>)}
              <button className="btn btn-ghost btn-sm" disabled={busy === 'analyze'}
                onClick={() => loadFixes(active, activeTable)}>
                <Icon.reset width={13} height={13} /> Re-analyze
              </button>
            </div>

            {note && (
              <div className="dd-note">
                <Icon.spark width={15} height={15} />
                <div style={{ flex: 1 }}>
                  <div>{note}</div>
                  {diag?.dropped_examples?.length > 0 && (
                    <details className="dd-diag" style={{ marginTop: 4 }}>
                      <summary>Show what the specialists proposed</summary>
                      <ul>
                        {diag.dropped_examples.map((x, i) => (
                          <li key={i}>
                            {x.agent && <span className="dd-agent-chip mini">{x.agent}</span>}
                            <b>{x.title || '(untitled)'}</b> — <span className="dim-note">{x.reason}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              </div>
            )}

            {/* Per-specialist strip — click to filter, hover for dropped reasons. */}
            {diag?.agents?.length > 0 && (
              <AgentStrip
                agents={diag.agents}
                active={agentFilter}
                onToggle={(name) => setAgentFilter((s) => {
                  const n = new Set(s);
                  if (n.has(name)) n.delete(name); else n.add(name);
                  return n;
                })}
                onClear={() => setAgentFilter(new Set())}
              />
            )}

            {suggestions.length === 0 && !note && <div className="dd-clean"><Icon.check width={16} height={16} /> No issues found — the AI thinks this data looks clean.</div>}

            <div className="dd-cards">
              {suggestions
                .filter((s) => agentFilter.size === 0 || agentFilter.has(s.agent))
                .map((s) => (
                  <FixCard key={s.id} sug={s} state={done[s.id]} preview={preview[s.id]} busy={busy === s.id}
                    onPreview={(code) => previewFix(s, code)} onApprove={() => approveFix(s)}
                    onCancel={() => cancelPreview(s)} onSkip={() => setDone((d) => ({ ...d, [s.id]: 'skipped' }))}
                    onFocus={() => setFocusId(s.id)} />
                ))}
            </div>

            <ColumnReport report={report} />
          </>
        )}
      </div>

      {focusId && suggestions.find((s) => s.id === focusId) && (
        <FixFocusModal
          sug={suggestions.find((s) => s.id === focusId)}
          state={done[focusId]} preview={preview[focusId]} busy={busy === focusId}
          onClose={() => setFocusId('')}
          onPreview={(code) => previewFix(suggestions.find((s) => s.id === focusId), code)}
          onApprove={() => approveFix(suggestions.find((s) => s.id === focusId))}
          onCancel={() => cancelPreview(suggestions.find((s) => s.id === focusId))}
          onSkip={() => { setDone((d) => ({ ...d, [focusId]: 'skipped' })); setFocusId(''); }}
        />
      )}

      {showHistory && (
        <AnalysisHistoryModal sid={sid} onClose={() => setShowHistory(false)} onRestore={restoreAnalysis} />
      )}
    </div>
  );
}

function FixCard({ sug, state, preview, busy, onPreview, onApprove, onCancel, onSkip, onFocus }) {
  const [editing, setEditing] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const [code, setCode] = useState(sug.pandas_code || '');
  const SevIcon = SEV_ICON[sug.severity] || Icon.spark;
  const resolved = state === 'skipped' ? 'skipped' : (state ? 'applied' : '');

  // Click anywhere on the card body (excluding buttons / inputs) opens the focus view.
  const handleBodyClick = (e) => {
    if (!onFocus) return;
    if (e.target.closest('button, a, textarea, input, .dd-code-edit')) return;
    onFocus();
  };

  return (
    <div className={`dd-card ${resolved} ${preview ? 'previewing' : ''}`} onClick={handleBodyClick}
         title={onFocus ? 'Click to open the full view' : undefined}>
      <div className="dd-card-top">
        <span className={`sev dot ${sug.severity}`}><SevIcon width={12} height={12} /></span>
        <div className="dd-card-title">
          {sug.title}
          {sug.agent && <span className={`dd-agent-chip a-${sug.agent}`} title={sug.agent_label || sug.agent}>{sug.agent_label || sug.agent}</span>}
          {sug.column && <span className="dd-col-chip">{sug.column}</span>}
        </div>
        {resolved === 'applied' && <span className="dd-tag ok"><Icon.check width={12} height={12} /> applied</span>}
        {resolved === 'skipped' && <span className="dd-tag skip">skipped</span>}
        {onFocus && (
          <button className="icon-btn dd-focus-btn" title="Expand" onClick={(e) => { e.stopPropagation(); onFocus(); }}>
            <Icon.expand width={13} height={13} />
          </button>
        )}
      </div>
      {sug.rationale && <div className="dd-card-why">{sug.rationale}</div>}
      {sug.expected_effect && <div className="dd-card-why dim">→ {sug.expected_effect}</div>}

      {(editing || showCode) && (
        <div className="dd-code-wrap">
          <div className="dd-code-head">
            {Icon.code ? <Icon.code width={12} height={12} /> : <Icon.spark width={12} height={12} />}
            <span className="dim-note">pandas · sandboxed (no imports, no I/O)</span>
            <span className="spacer" />
            <button className="icon-btn" title="Reset to the agent's original snippet" onClick={() => setCode(sug.pandas_code || '')}>
              <Icon.reset width={12} height={12} />
            </button>
          </div>
          {editing ? (
            <textarea className="dd-code-edit" value={code} spellCheck={false}
              onChange={(e) => setCode(e.target.value)} rows={Math.min(8, Math.max(2, code.split('\n').length))} />
          ) : (
            <pre className="dd-code">{code}</pre>
          )}
        </div>
      )}

      {typeof state === 'object' && state ? (
        <FixResult result={state} />
      ) : preview ? (
        <>
          <PreviewDiff result={preview.result} />
          <div className="dd-card-actions">
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={onApprove}>
              {busy ? 'Applying…' : <><Icon.check width={13} height={13} /> Approve</>}
            </button>
            <button className="btn btn-ghost btn-sm" disabled={busy} onClick={onCancel}>Cancel</button>
          </div>
        </>
      ) : !resolved && (
        <div className="dd-card-actions">
          <button className="btn btn-primary btn-sm" disabled={busy || !code.trim()} onClick={() => onPreview(code)}>
            {busy ? 'Previewing…' : <><Icon.spark width={13} height={13} /> Fix</>}
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => { setEditing((v) => !v); setShowCode(true); }}>
            <Icon.edit width={13} height={13} /> {editing ? 'Done' : 'Edit code'}
          </button>
          {!editing && (
            <button className="btn btn-ghost btn-sm" onClick={() => setShowCode((v) => !v)}>
              {showCode ? 'Hide code' : 'View code'}
            </button>
          )}
          <button className="btn btn-ghost btn-sm" onClick={onSkip}>Skip</button>
        </div>
      )}
    </div>
  );
}

function ResultStats({ result }) {
  return (
    <div className="dd-result-stats">
      {result.rows_before !== result.rows_after && <span>{result.rows_before} → {result.rows_after} rows</span>}
      {result.nulls_before != null && result.nulls_before !== result.nulls_after &&
        <span>nulls {result.nulls_before} → {result.nulls_after}</span>}
      {result.columns_added?.length > 0 && <span>+ {result.columns_added.join(', ')}</span>}
      {result.columns_removed?.length > 0 && <span>− {result.columns_removed.join(', ')}</span>}
    </div>
  );
}

function ChangedTable({ changed, animated }) {
  if (!changed?.length) {
    return <div className="dim-note" style={{ marginTop: 4 }}>No per-cell changes to show.</div>;
  }
  const multiCol = changed.some((c) => c.column !== changed[0].column);
  return (
    <table className={`dd-diff ${animated ? 'dd-diff-anim' : ''}`}>
      <thead><tr>
        <th>row</th>{multiCol && <th>column</th>}<th>before</th><th /><th>after</th>
      </tr></thead>
      <tbody>
        {changed.map((c, i) => (
          <tr key={`${c.row}-${c.column}`} style={animated ? { animationDelay: `${i * 70}ms` } : undefined}>
            <td className="num">{c.row}</td>
            {multiCol && <td className="dd-col-cell">{c.column}</td>}
            <td className="old">{c.before === null ? '∅' : String(c.before)}</td>
            <td className="arrow">→</td>
            <td className="new">{c.after === null ? '∅' : String(c.after)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PreviewDiff({ result }) {
  return (
    <div className="dd-preview">
      <div className="dd-preview-msg"><Icon.spark width={13} height={13} /> Preview — {result.message || 'review the change, then Approve'}</div>
      <ResultStats result={result} />
      <ChangedTable changed={result.changed} animated />
    </div>
  );
}

function FixResult({ result }) {
  return (
    <div className="dd-result">
      <div className="dd-result-msg"><Icon.check width={13} height={13} /> {result.message}</div>
      <ResultStats result={result} />
      <ChangedTable changed={result.changed} />
    </div>
  );
}

function ColumnReport({ report }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="dd-colrep">
      <button className="dd-colrep-toggle" onClick={() => setOpen((v) => !v)}>
        <Icon.chevron width={14} height={14} style={{ transform: open ? 'rotate(90deg)' : 'none' }} />
        Column report ({report.column_reports.length} columns)
      </button>
      {open && (
        <div className="dd-coltable">
          {report.column_reports.map((c) => (
            <div className="dd-colrow" key={c.column}>
              <div className="dd-col-name">{c.column}<span className="dd-col-kind">{c.kind}</span></div>
              <div className="dd-col-issues">
                <span className="dd-col-fact">{c.null_pct > 0 ? `${Math.round(c.null_pct * 100)}% null` : 'no nulls'}</span>
                <span className="dd-col-fact">{c.unique} unique</span>
                {c.samples?.length > 0 && <span className="dd-col-fact dim" title={c.samples.join(' · ')}>e.g. {c.samples.slice(0, 3).join(', ')}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Per-specialist strip: one chip per agent with `kept / raw` counts. Click to
// filter the cards below; hover for the dropped-reason breakdown. Agents that
// errored (HTTP / parse) get an amber dot so the user knows something went wrong.
function AgentStrip({ agents, active, onToggle, onClear }) {
  const anyActive = active.size > 0;
  return (
    <div className="dd-agent-strip">
      <span className="dim-note" style={{ marginRight: 6 }}>Specialists:</span>
      {agents.map((a) => {
        const dropped = Object.entries(a.dropped || {}).filter(([, v]) => v > 0)
          .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`).join(', ');
        const tip = [
          a.role,
          `${a.kept} kept · ${a.raw} proposed${a.repaired ? ` · ${a.repaired} repaired` : ''}`,
          a.error ? `error: ${a.error}` : '',
          dropped ? `dropped — ${dropped}` : '',
        ].filter(Boolean).join('\n');
        const cls = [
          'dd-agent-chip', `a-${a.name}`,
          active.has(a.name) ? 'on' : '',
          a.error ? 'err' : '',
          a.kept === 0 && !a.error ? 'empty' : '',
        ].filter(Boolean).join(' ');
        return (
          <button key={a.name} type="button" className={cls} title={tip}
            onClick={() => onToggle(a.name)} aria-pressed={active.has(a.name)}>
            <span className="lbl">{a.label}</span>
            <span className="cnt">{a.kept}{a.raw !== a.kept ? `/${a.raw}` : ''}</span>
            {a.error && <span className="dot err" />}
          </button>
        );
      })}
      {anyActive && (
        <button type="button" className="dd-agent-clear" onClick={onClear} title="Show all specialists">
          clear filter
        </button>
      )}
    </div>
  );
}

// Focus view — opens when a card is clicked. Same actions as the inline card, just
// with a lot more room to read the rationale and edit the snippet comfortably.
function FixFocusModal({ sug, state, preview, busy, onClose, onPreview, onApprove, onCancel, onSkip }) {
  const [editing, setEditing] = useState(true);   // open straight into editable mode
  const [code, setCode] = useState(sug.pandas_code || '');
  const SevIcon = SEV_ICON[sug.severity] || Icon.spark;
  const resolved = state === 'skipped' ? 'skipped' : (state ? 'applied' : '');

  // Escape to close, Cmd/Ctrl+Enter to run a preview.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
      else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !busy && code.trim() && !resolved && !preview) {
        e.preventDefault();
        onPreview(code);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [code, busy, resolved, preview, onPreview, onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal dd-focus" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className={`sev dot ${sug.severity}`}><SevIcon width={14} height={14} /></span>
          <h3 style={{ margin: 0, flex: 1 }}>{sug.title}</h3>
          {sug.agent && (
            <span className={`dd-agent-chip a-${sug.agent}`} title={sug.agent_label || sug.agent}>
              {sug.agent_label || sug.agent}
            </span>
          )}
          {sug.column && <span className="dd-col-chip">{sug.column}</span>}
          {resolved === 'applied' && <span className="dd-tag ok"><Icon.check width={12} height={12} /> applied</span>}
          {resolved === 'skipped' && <span className="dd-tag skip">skipped</span>}
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon.x /></button>
        </div>
        <div className="modal-body dd-focus-body">
          {sug.rationale && <p className="dd-focus-why">{sug.rationale}</p>}
          {sug.expected_effect && <p className="dd-focus-why dim">→ {sug.expected_effect}</p>}

          <div className="dd-code-wrap dd-focus-code">
            <div className="dd-code-head">
              <Icon.code width={12} height={12} />
              <span className="dim-note">pandas · sandboxed (no imports, no I/O)</span>
              <span className="spacer" />
              <button className="icon-btn" title="Reset to the agent's original snippet" onClick={() => setCode(sug.pandas_code || '')}>
                <Icon.reset width={12} height={12} />
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => setEditing((v) => !v)}>
                <Icon.edit width={13} height={13} /> {editing ? 'Read-only' : 'Edit'}
              </button>
            </div>
            {editing ? (
              <textarea className="dd-code-edit dd-focus-edit" value={code} spellCheck={false}
                onChange={(e) => setCode(e.target.value)} rows={Math.min(20, Math.max(6, code.split('\n').length + 2))} />
            ) : (
              <pre className="dd-code dd-focus-pre">{code}</pre>
            )}
            <div className="dim-note" style={{ marginTop: 4, fontSize: 11 }}>
              Tip: Esc closes · Ctrl/Cmd + Enter previews the fix
            </div>
          </div>

          {typeof state === 'object' && state ? (
            <FixResult result={state} />
          ) : preview ? (
            <>
              <PreviewDiff result={preview.result} />
              <div className="dd-card-actions">
                <button className="btn btn-primary btn-sm" disabled={busy} onClick={onApprove}>
                  {busy ? 'Applying…' : <><Icon.check width={13} height={13} /> Approve</>}
                </button>
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={onCancel}>Cancel preview</button>
                <button className="btn btn-ghost btn-sm" disabled={busy} onClick={onClose}>Close</button>
              </div>
            </>
          ) : !resolved && (
            <div className="dd-card-actions">
              <button className="btn btn-primary btn-sm" disabled={busy || !code.trim()} onClick={() => onPreview(code)}>
                {busy ? 'Previewing…' : <><Icon.spark width={13} height={13} /> Fix</>}
              </button>
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={onSkip}>Skip</button>
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={onClose}>Close</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Past analyses for this workspace (auto-saved after each Analyze). Click a row to
// re-open it; per-row delete; "Clear all" wipes the history without touching files.
function AnalysisHistoryModal({ sid, onClose, onRestore }) {
  const [items, setItems] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const load = async () => {
    setError('');
    try { setItems(await listAnalyses(sid)); }
    catch (e) { setError(String(e.message || e)); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const remove = async (aid) => {
    setBusy(aid); setError('');
    try { await deleteAnalysis(sid, aid); clearDone(sid, aid); await load(); toast('Analysis deleted'); }
    catch (e) { setError(String(e.message || e)); }
    finally { setBusy(''); }
  };
  const clearAll = async () => {
    if (!items?.length) return;
    if (!(await confirm({ title: 'Delete all analyses?', message: `All ${items.length} saved analyses for this workspace will be deleted.`, confirmText: 'Delete all', danger: true }))) return;
    setBusy('clear'); setError('');
    try {
      const ids = items.map((a) => a.id);
      await clearAnalyses(sid);
      ids.forEach((aid) => clearDone(sid, aid));
      await load();
      toast(`Deleted ${ids.length} ${ids.length === 1 ? 'analysis' : 'analyses'}`);
    }
    catch (e) { setError(String(e.message || e)); }
    finally { setBusy(''); }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal dd-history" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3 style={{ margin: 0, flex: 1 }}>Analysis history</h3>
          <button className="btn btn-ghost btn-sm" onClick={clearAll}
                  disabled={!items?.length || busy === 'clear'}>
            <Icon.trash width={13} height={13} /> Clear all
          </button>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon.x /></button>
        </div>
        <div className="modal-body">
          {error && <div className="dd-error"><Icon.alert width={15} height={15} /> {error}</div>}
          {items === null && <div className="dd-muted dd-center">Loading…</div>}
          {items && items.length === 0 && (
            <div className="dd-muted" style={{ padding: '12px 2px' }}>
              No saved analyses yet. Run <b>Analyze</b> on a file — each result is auto-saved here.
            </div>
          )}
          {items && items.length > 0 && (
            <ul className="dd-history-list">
              {items.map((a) => (
                <li key={a.id} className="dd-history-row">
                  <button className="dd-history-main" onClick={() => onRestore(a.id)} disabled={busy === a.id}>
                    <div className="dd-history-title">
                      <Icon.file width={13} height={13} /> <b>{a.filename || '(unknown file)'}</b>
                    </div>
                    <div className="dd-history-meta dim-note">
                      {a.when ? new Date(a.when).toLocaleString() : '—'}
                      <span className="sep">·</span>
                      {a.rows ?? '?'} rows · {a.columns ?? '?'} cols
                      <span className="sep">·</span>
                      <b>{a.suggestion_count}</b> fix{a.suggestion_count === 1 ? '' : 'es'}
                      {a.has_note && <> <span className="sep">·</span> note</>}
                    </div>
                  </button>
                  <button className="icon-btn danger" title="Delete this entry"
                          disabled={busy === a.id} onClick={() => remove(a.id)}>
                    <Icon.trash width={14} height={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}


