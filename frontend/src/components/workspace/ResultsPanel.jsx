import { useEffect, useState } from 'react';
import { Icon } from '../common/Icons';
import BarChart from './BarChart';
import AgentActivity from './AgentActivity';
import DataFixes from './DataFixes';
import { EVENT, STEP_KIND } from '../../lib/eventContract';
import { useToast } from '../common/Toast';

const TABS = [
  { id: 'activity', label: 'Activity', icon: Icon.zap },
  { id: 'answer', label: 'Answer', icon: Icon.table },
  { id: 'chart', label: 'Chart', icon: Icon.bars },
  { id: 'log', label: 'Event Log', icon: Icon.list },
  // Data Doctor lives in its own tab (magenta accent), decoupled from the chat.
  { id: 'doctor', label: 'Data Doctor', icon: Icon.brain, accent: 'dd' },
];

const cellStr = (c) => {
  if (c == null) return '';
  if (typeof c === 'object') { try { return JSON.stringify(c); } catch { return String(c); } }
  return String(c);
};
const csvEscape = (s) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);

// Normalise the answer into a columns + array-of-arrays matrix once, tolerating
// odd persisted shapes (row as dict / cell as object) so Copy/CSV never crash.
function toMatrix(answer) {
  const columns = Array.isArray(answer?.columns) ? answer.columns : [];
  const raw = Array.isArray(answer?.rows) ? answer.rows : [];
  const rows = raw.map((r) => {
    if (Array.isArray(r)) return r;
    if (r && typeof r === 'object') return columns.map((c) => r[c]);
    return [r];
  });
  return { columns, rows };
}

// §12.6 — run-level provenance: the data-producing steps (SQL / Python) that fed
// the final answer. True per-cell provenance needs engine instrumentation; this
// surfaces "how the answer was computed" from the trace we already have.
function deriveEvidence(trace) {
  return (trace || [])
    .filter((t) => t.kind === STEP_KIND.ACTION
      && (typeof t.input?.sql === 'string' || typeof t.input?.code === 'string' || /^execute_/.test(t.tool || '')))
    .map((t) => ({
      tool: t.tool,
      sql: typeof t.input?.sql === 'string' ? t.input.sql : null,
      code: typeof t.input?.code === 'string' ? t.input.code : null,
    }));
}

export default function ResultsPanel({ agent, tools, sid, files, settings, onFilesChanged }) {
  const { results, events, busy, feed } = agent;
  const [tab, setTab] = useState('activity');
  const [stage, setStage] = useState('');
  const [artifact, setArtifact] = useState('');
  const hasAnswer = !!results.answer;
  const hasChart = !!results.chart;

  // The latest run's trace — used to show how the answer was computed.
  const lastRun = [...(feed || [])].reverse().find((e) => e.type === 'run');
  const trace = lastRun?.trace || [];

  // Live: jump to Activity while the agent works, to Answer when it finishes.
  useEffect(() => { if (busy && !hasAnswer) setTab('activity'); }, [busy, hasAnswer]);
  useEffect(() => { if (hasAnswer) setTab('answer'); }, [hasAnswer]);

  return (
    <div className="panel" style={{ flex: 1 }}>
      <div className="panel-head"><span className="title"><Icon.bars width={16} height={16} /> Results</span></div>

      {/* Stage / Artifact pickers only make sense when the run exposes stages
          (mock replay). Live runs carry a single answer, so we hide the dead
          dropdowns rather than show a no-op control. */}
      {results.stages.length > 0 && (
        <div className="results-selects">
          <Select label="Analysis Stage" value={stage || results.stages.at(-1)?.stage || ''}
            options={results.stages.map((s) => s.stage)} placeholder="Select stage…" onChange={setStage} />
          <Select label="Artifact" value={artifact || results.stages.at(-1)?.artifact || ''}
            options={results.stages.map((s) => s.artifact)} placeholder="Select artifact…" onChange={setArtifact} />
        </div>
      )}

      <div className="result-tabs">
        {TABS.map((t) => (
          <button key={t.id} className={`result-tab ${tab === t.id ? 'active' : ''} ${t.accent === 'dd' ? 'dd-tab' : ''}`} onClick={() => setTab(t.id)}
            data-tour={t.id === 'activity' ? 'tour-activity' : t.id === 'doctor' ? 'tour-doctor' : undefined}>
            <t.icon width={14} height={14} style={{ verticalAlign: '-2px', marginRight: 5 }} />{t.label}
          </button>
        ))}
      </div>

      <div className={`result-content ${tab === 'doctor' ? 'flush' : ''}`}>
        {tab === 'activity' && <AgentActivity agent={agent} tools={tools} />}
        {tab === 'answer' && (hasAnswer ? <AnswerTable answer={results.answer} trace={trace} /> : (busy ? <AnswerSkeleton /> : <Empty icon={Icon.table} text="The final answer table appears here once the agent submits it — this is what gets scored against gold.csv." />))}
        {tab === 'chart' && (hasChart ? <BarChart chart={results.chart} /> : <Empty icon={Icon.bars} text="When the answer is groupable, a chart of it renders here." />)}
        {tab === 'log' && <EventLog events={events} />}
        {tab === 'doctor' && <DataFixes sid={sid} files={files} settings={settings} onChanged={onFilesChanged} />}
      </div>
    </div>
  );
}

function Select({ label, value, options, placeholder, onChange }) {
  return (
    <div className="select-field">
      <label>{label}</label>
      <select value={value} onChange={(e) => onChange?.(e.target.value)}>
        {options.length === 0 && <option value="">{placeholder}</option>}
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
      <Icon.chevron className="chev" width={14} height={14} />
    </div>
  );
}

function AnswerTable({ answer, trace }) {
  const toast = useToast();
  const [showEvidence, setShowEvidence] = useState(false);
  const { columns, rows } = toMatrix(answer);
  const evidence = deriveEvidence(trace);

  const onCopy = async () => {
    const tsv = [columns.join('\t'), ...rows.map((r) => r.map(cellStr).join('\t'))].join('\n');
    try { await navigator.clipboard.writeText(tsv); toast('Answer copied to clipboard'); }
    catch { toast('Copy failed — clipboard blocked', 'error'); }
  };
  const onCSV = () => {
    const csv = [columns.map((c) => csvEscape(cellStr(c))).join(','),
      ...rows.map((r) => r.map((v) => csvEscape(cellStr(v))).join(','))].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url; a.download = 'answer.csv'; document.body.appendChild(a); a.click();
    a.remove(); URL.revokeObjectURL(url);
    toast('answer.csv downloaded');
  };

  return (
    <div>
      <div className="result-caption">
        <Icon.check width={14} height={14} style={{ color: 'var(--ds-success)' }} />
        Final answer · {rows.length} rows × {columns.length} columns
        <span className="spacer" />
        <button className="btn btn-ghost btn-sm" onClick={onCopy} disabled={!columns.length}><Icon.copy width={13} height={13} /> Copy</button>
        <button className="btn btn-ghost btn-sm" onClick={onCSV} disabled={!columns.length}><Icon.download width={13} height={13} /> CSV</button>
      </div>
      <table className="answer-table">
        <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>{row.map((cell, j) => <td key={j} className={answer.numeric?.[columns[j]] ? 'num' : ''}>{cellStr(cell)}</td>)}</tr>
          ))}
        </tbody>
      </table>

      {evidence.length > 0 && (
        <div className="answer-evidence">
          <button className="evidence-toggle" onClick={() => setShowEvidence((v) => !v)}>
            <Icon.code width={13} height={13} />
            How this answer was computed · {evidence.length} step{evidence.length === 1 ? '' : 's'}
            <Icon.chevron width={12} height={12} style={{ transform: showEvidence ? 'rotate(180deg)' : 'none', transition: 'transform .15s', marginLeft: 4 }} />
          </button>
          {showEvidence && (
            <div className="evidence-steps">
              {evidence.map((e, i) => (
                <div className="evidence-step" key={i}>
                  <div className="evidence-step-head"><span className="step-num">{i + 1}</span><span className="tool-chip">{e.tool}</span></div>
                  {e.sql && <div className="code-block-wrap"><span className="code-lang">sql</span><pre className="code-block">{e.sql}</pre></div>}
                  {e.code && <div className="code-block-wrap"><span className="code-lang">python</span><pre className="code-block">{e.code}</pre></div>}
                  {!e.sql && !e.code && <span className="dim-note">ran <code>{e.tool}</code> (no SQL/code captured)</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EventLog({ events }) {
  if (events.length === 0) return <Empty icon={Icon.list} text="Every AgentEvent streams here — the single source of truth shared with the UI." />;
  return (
    <div className="event-log">
      {events.map((e) => (
        <div className="ev-row" key={e.id}>
          <span className="ev-time">{e.time}</span>
          <span className={`ev-type ${typeClass(e.type)}`}>{e.type}</span>
          <span className="ev-pay">{payloadText(e.payload)}</span>
        </div>
      ))}
    </div>
  );
}

// Shown on the Answer tab while a run is in flight — a shimmering table outline
// so the panel reads as "working", not empty.
function AnswerSkeleton() {
  return (
    <div className="answer-skel" aria-label="Computing the answer…">
      <div className="answer-skel-caption"><span className="up-spin" /> Computing the answer…</div>
      <div className="answer-skel-grid">
        {Array.from({ length: 5 }).map((_, r) => (
          <div className="answer-skel-row" key={r}>
            {Array.from({ length: 3 }).map((__, c) => (
              <span className="skel-line" key={c} style={{ animationDelay: `${(r + c) * 0.08}s` }} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function Empty({ icon: I, text }) {
  return (
    <div className="result-empty">
      <div>
        <div className="ic"><I width={24} height={24} /></div>
        <div style={{ maxWidth: 250, fontSize: 13 }}>{text}</div>
      </div>
    </div>
  );
}

function typeClass(t) {
  if (t === EVENT.AGENT_THOUGHT) return 't-thought';
  if (t.startsWith('TOOL_') || t === EVENT.STEP_PROPOSED) return 't-action';
  if (t === EVENT.AWAITING_USER) return 't-await';
  if (t === EVENT.RUN_FINISHED) return 't-done';
  if (t === EVENT.STATE_CHANGED) return 't-state';
  return 't-observe';
}

function payloadText(p) {
  if (!p || Object.keys(p).length === 0) return '';
  if (p.thought) return p.thought.slice(0, 90) + (p.thought.length > 90 ? '…' : '');
  if (p.action) return `${p.action} ${p.action_input ? JSON.stringify(p.action_input) : ''}`.slice(0, 90);
  if (p.to) return `→ ${p.to}`;
  if (p.command) return `${p.command}${p.mode ? ' ' + p.mode : ''}`;
  return JSON.stringify(p).slice(0, 90);
}
