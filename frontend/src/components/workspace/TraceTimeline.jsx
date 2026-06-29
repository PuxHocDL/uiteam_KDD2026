import { useEffect, useState } from 'react';
import { Icon } from '../common/Icons';
import { RUN_STATE, STEP_KIND } from '../../lib/eventContract';
import CoPilotCard from './CoPilotCard';
import RichText from '../common/RichText';

const STATE_PILL = {
  [RUN_STATE.THINKING]: { cls: 'thinking live', label: 'Thinking' },
  [RUN_STATE.STEP_PROPOSED]: { cls: 'running live', label: 'Proposing' },
  [RUN_STATE.AWAITING_USER]: { cls: 'awaiting live', label: 'Awaiting you' },
  [RUN_STATE.TOOL_EXECUTING]: { cls: 'running live', label: 'Running tool' },
  [RUN_STATE.OBSERVING]: { cls: 'running live', label: 'Observing' },
  [RUN_STATE.DONE]: { cls: 'done', label: 'Done' },
  [RUN_STATE.FAILED]: { cls: 'failed', label: 'Failed' },
  [RUN_STATE.CANCELLED]: { cls: 'failed', label: 'Cancelled' },
};

const KIND_ICON = {
  [STEP_KIND.THOUGHT]: Icon.brain,
  [STEP_KIND.ACTION]: Icon.tool,
  [STEP_KIND.OBSERVE]: Icon.bars,
  [STEP_KIND.ANSWER]: Icon.check,
  [STEP_KIND.KG]: Icon.network,
};

// Group the flat trace into "rounds" — each round is one Thought + the
// Action(s) and Observation(s) it triggered. ANSWER and KG steps stay outside
// the rounds so they aren't visually merged with intermediate reasoning.
function buildRounds(trace) {
  const out = [];
  let cur = null;
  for (const t of trace) {
    if (t.kind === STEP_KIND.ANSWER || t.kind === STEP_KIND.KG) {
      if (cur) { out.push(cur); cur = null; }
      out.push({ kind: 'solo', steps: [t] });
      continue;
    }
    if (t.kind === STEP_KIND.THOUGHT) {
      if (cur) out.push(cur);
      cur = { kind: 'round', steps: [t] };
      continue;
    }
    // ACTION / OBSERVE — attach to current round, or start a synthetic one
    // (defensive: covers the rare case where the first event isn't a thought).
    if (!cur) cur = { kind: 'round', steps: [] };
    cur.steps.push(t);
  }
  if (cur) out.push(cur);
  return out;
}

export default function TraceTimeline({ run, controls }) {
  const pill = STATE_PILL[run.state] || STATE_PILL[RUN_STATE.THINKING];
  const live = ![RUN_STATE.DONE, RUN_STATE.FAILED, RUN_STATE.CANCELLED].includes(run.state);
  const rounds = buildRounds(run.trace);
  const hasReasoning = rounds.some((r) => r.kind === 'round');

  // Collapse the reasoning by default once the run is done — the chat bubble
  // and the Results panel already carry the answer, so the long trace would
  // just add noise. While live, always force-expanded so the user sees it stream.
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => { if (!live && hasReasoning) setCollapsed(true); }, [live, hasReasoning]);

  return (
    <div className="run-block">
      <div className="run-head">
        <Icon.bot width={17} height={17} style={{ color: 'var(--ds-teal-600)' }} />
        <span className="q" title={run.question}>{run.question}</span>
        <span className={`state-pill ${pill.cls}`}><span className="pulse" />{pill.label}</span>
        {!live && hasReasoning && (
          <button
            type="button"
            className="reasoning-toggle"
            onClick={() => setCollapsed((c) => !c)}
            title={collapsed ? 'Show how the agent reasoned' : 'Hide reasoning'}
          >
            <Icon.chevron
              width={13}
              height={13}
              style={{ transform: collapsed ? 'rotate(0deg)' : 'rotate(180deg)', transition: 'transform .15s' }}
            />
            {collapsed ? 'Show reasoning' : 'Hide reasoning'}
          </button>
        )}
      </div>

      {!(collapsed && !live) && (
        <div className="timeline">
          {rounds.map((r, i) =>
            r.kind === 'solo'
              ? <TraceStep key={r.steps[0].id} t={r.steps[0]} live={live} />
              : (
                <div className="trace-round" key={`round-${i}-${r.steps[0]?.id || i}`}>
                  {r.steps.map((t) => <TraceStep key={t.id} t={t} live={live} />)}
                </div>
              )
          )}
        </div>
      )}

      {run.awaiting && (
        <CoPilotCard
          awaiting={run.awaiting}
          onApprove={controls.approve}
          onEdit={controls.editAndRun}
          onReject={controls.reject}
          onGuide={controls.guide}
          onCancel={controls.cancel}
        />
      )}
    </div>
  );
}

function TraceStep({ t, live }) {
  const [open, setOpen] = useState(false);
  const IconC = KIND_ICON[t.kind] || Icon.brain;
  const isLive = live && (t.status === 'running' || t.status === 'proposed');

  return (
    <div className={`tstep ${t.kind} ${isLive ? 'live' : ''}`}>
      <span className="tnode"><IconC /></span>

      {t.kind === STEP_KIND.THOUGHT && (
        <>
          <div className="tstep-head">
            <span className="tstep-kind">{t.guide ? 'Guidance' : 'Thought'}</span>
          </div>
          <div className="tstep-body"><RichText text={t.text} /></div>
        </>
      )}

      {t.kind === STEP_KIND.ACTION && (
        <>
          <div className="tstep-head">
            <span className="tstep-kind">Action</span>
            <span className="tool-chip">{t.tool}</span>
            {(typeof t.input?.sql === 'string' || typeof t.input?.code === 'string') && (
              <span className="evidence-chip" title="Produces the data behind the answer"><Icon.code width={11} height={11} /> evidence</span>
            )}
            <StatusTag status={t.status} />
          </div>
          <div className="tstep-body">
            {t.reason && <div>{t.reason}</div>}
            <ActionInput tool={t.tool} input={t.input} />
          </div>
        </>
      )}

      {t.kind === STEP_KIND.OBSERVE && (
        <>
          <div className="tstep-head">
            <span className="tstep-kind">Observation</span>
            <span className={`ok-pill ${t.ok ? 'ok' : 'err'}`}>{t.ok ? 'ok' : (t.rejected ? 'rejected' : 'error')}</span>
          </div>
          <div className="tstep-body">
            {t.summary}
            {(t.raw || t.details) && (
              <>
                <button className="details-toggle" onClick={() => setOpen((o) => !o)}>{open ? 'Hide details' : 'View raw result'}</button>
                {open && <pre className="details-pre">{t.raw != null ? t.raw : JSON.stringify(t.details, null, 2)}</pre>}
              </>
            )}
          </div>
        </>
      )}

      {t.kind === STEP_KIND.ANSWER && (
        <>
          <div className="tstep-head"><span className="tstep-kind">Final answer</span></div>
          <div className="tstep-body">{t.summary} <span style={{ color: 'var(--ds-muted)' }}>→ see Results panel.</span></div>
        </>
      )}

      {t.kind === STEP_KIND.KG && (
        <>
          <div className="tstep-head">
            <span className="tstep-kind">Knowledge graph</span>
            <span className="ok-pill ok">primed</span>
          </div>
          <div className="tstep-body">
            <span style={{ color: 'var(--ds-muted)' }}>Pre-built before step 1 so the agent reasons over real entities + join paths:</span>{' '}
            {t.summary}
            {t.graph && (
              <>
                <button className="details-toggle" onClick={() => setOpen((o) => !o)}>{open ? 'Hide map' : 'View map'}</button>
                {open && <KnowledgeGraphPreview graph={t.graph} />}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// Render a tool's input nicely: code → code block, SQL → SQL block,
// small params → compact key:value, nested → pretty JSON.
function ActionInput({ input }) {
  if (input == null || typeof input !== 'object') return input == null ? null : <div className="input-line">{String(input)}</div>;
  if (typeof input.code === 'string') return <CodeBlock lang="python" code={input.code} />;
  if (typeof input.sql === 'string') return <CodeBlock lang="sql" code={input.sql} />;
  const keys = Object.keys(input);
  if (keys.length === 0) return null;
  const compact = keys.every((k) => input[k] === null || typeof input[k] !== 'object');
  if (compact) return <div className="input-line">{keys.map((k) => `${k}: ${fmt(input[k])}`).join('   ·   ')}</div>;
  return <pre className="code-block">{JSON.stringify(input, null, 2)}</pre>;
}

function CodeBlock({ lang, code }) {
  return (
    <div className="code-block-wrap">
      <span className="code-lang">{lang}</span>
      <pre className="code-block">{code}</pre>
    </div>
  );
}

const fmt = (v) => { const s = JSON.stringify(v); return s && s.length > 60 ? s.slice(0, 60) + '…' : (typeof v === 'string' ? v : s); };

function StatusTag({ status }) {
  if (status === 'proposed') return <span className="tstep-meta">proposed · awaiting</span>;
  if (status === 'running') return <span className="tstep-meta">executing…</span>;
  if (status === 'rejected') return <span className="ok-pill err">rejected</span>;
  return <span className="tstep-meta">executed</span>;
}

// Compact preview of the pre-built Knowledge Graph: entities (tables/files),
// join paths between them, and any constraints/metrics from knowledge.md.
function KnowledgeGraphPreview({ graph }) {
  const entities = Array.isArray(graph?.entities) ? graph.entities : [];
  const relationships = Array.isArray(graph?.relationships) ? graph.relationships : [];
  const constraints = Array.isArray(graph?.constraints) ? graph.constraints : [];
  const metrics = Array.isArray(graph?.metrics) ? graph.metrics : [];

  return (
    <div className="kg-preview">
      {entities.length > 0 && (
        <div className="kg-section">
          <div className="kg-section-title">Entities</div>
          <div className="kg-entity-list">
            {entities.slice(0, 12).map((e) => (
              <div key={`${e.source_file}:${e.name}`} className="kg-entity">
                <div className="kg-entity-head">
                  <span className="kg-entity-name">{e.name}</span>
                  <span className="kg-entity-meta">
                    {e.source_type}
                    {e.row_count != null ? ` · ${e.row_count} rows` : ''}
                    {` · ${(e.columns || []).length} cols`}
                  </span>
                </div>
                <div className="kg-entity-cols">
                  {(e.columns || []).slice(0, 10).map((c) => (
                    <span key={c.name} className="kg-col-chip" title={c.description || ''}>
                      {c.name}<span className="kg-col-type">:{c.dtype || '?'}</span>
                    </span>
                  ))}
                  {(e.columns || []).length > 10 && (
                    <span className="kg-col-more">+{(e.columns || []).length - 10} more</span>
                  )}
                </div>
              </div>
            ))}
            {entities.length > 12 && (
              <div className="kg-col-more">+{entities.length - 12} more entities</div>
            )}
          </div>
        </div>
      )}
      {relationships.length > 0 && (
        <div className="kg-section">
          <div className="kg-section-title">Join paths</div>
          <ul className="kg-edge-list">
            {relationships.slice(0, 10).map((r, i) => (
              <li key={i} className={`kg-edge kg-edge-${r.type || 'shared'}`}>
                <code>{r.from}</code> <span className="kg-edge-arrow">↔</span> <code>{r.to}</code>
                <span className="kg-edge-meta">{r.type}{r.confidence != null ? ` · ${Math.round(r.confidence * 100)}%` : ''}</span>
              </li>
            ))}
            {relationships.length > 10 && (
              <li className="kg-col-more">+{relationships.length - 10} more joins</li>
            )}
          </ul>
        </div>
      )}
      {constraints.length > 0 && (
        <div className="kg-section">
          <div className="kg-section-title">Constraints</div>
          <ul className="kg-constraint-list">
            {constraints.slice(0, 8).map((c, i) => (
              <li key={i}><code>{c.entity}.{c.field}</code>: {c.rule}</li>
            ))}
          </ul>
        </div>
      )}
      {metrics.length > 0 && (
        <div className="kg-section">
          <div className="kg-section-title">Metrics</div>
          <ul className="kg-constraint-list">
            {metrics.slice(0, 6).map((m, i) => (
              <li key={i}><strong>{m.name}</strong>: <code>{m.formula}</code></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
