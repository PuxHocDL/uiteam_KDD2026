import { useState } from 'react';
import { Icon } from '../common/Icons';
import { DECOMPOSITION, PHASES, TASK } from '../../data/mockData';
import { RUN_STATE, STEP_KIND } from '../../lib/eventContract';

// Phase labels per intent — data-quality runs care about Profile/Diagnose/Fix,
// not Compute/Validate. The Plan panel adapts so the breakdown isn't awkward.
const PHASES_BY_INTENT = {
  quality: ['Read file', 'Profile data', 'Diagnose issues', 'Suggest fixes'],
};

// Left-bottom panel: the agent's understanding of the task — a question
// decomposition (Entities/Filters/Aggregation/Output/Not-asking, which the
// ReAct engine enforces on step 1) + a phase tracker that lights up live.
// Hidden entirely until the user asks at least one question, so the workspace
// doesn't show an awkward empty plan card on first load.
export default function PlanPanel({ agent }) {
  const intent = agent.plan?.intent || 'analytic';
  const phases = PHASES_BY_INTENT[intent] || PHASES;
  const phase = currentPhase(agent, intent);
  const isLive = agent.plan !== undefined;      // live hook exposes `plan`; mock doesn't
  const fields = isLive ? agent.plan?.fields : DECOMPOSITION;
  const [expanded, setExpanded] = useState(null);  // phase index whose "why" is open

  // Before the first question, show a friendly placeholder instead of a blank
  // panel so the workspace doesn't look broken / empty on first load.
  const hasRun = (agent.feed || []).some((e) => e.type === 'run');
  if (!hasRun) {
    return (
      <div className="panel grow">
        <div className="panel-head"><span className="title"><Icon.list width={16} height={16} /> Plan</span></div>
        <div className="panel-body">
          <div className="panel-placeholder">
            <div className="pp-ic"><Icon.list width={22} height={22} /></div>
            <div className="pp-title">Your plan shows up here</div>
            <p className="pp-text">Ask a question and the agent breaks it into entities, filters and steps — you’ll watch each one light up live.</p>
          </div>
        </div>
      </div>
    );
  }

  // The latest run's trace, bucketed per phase for click-to-see-why drill-down.
  const lastRun = [...(agent.feed || [])].reverse().find((e) => e.type === 'run');
  const byPhase = groupTraceByPhase(lastRun?.trace, intent, phases.length);

  return (
    <div className="panel grow">
      <div className="panel-head">
        <span className="title"><Icon.list width={16} height={16} /> Plan</span>
        <span className="spacer" />
        {isLive ? <span className="diff-badge live-badge">live</span> : <span className={`diff-badge ${TASK.difficulty}`}>{TASK.difficulty}</span>}
      </div>
      <div className="panel-body">
        <div className="plan-section-label">
          {intent === 'quality' ? 'Data-quality plan' : 'Question decomposition'}
        </div>
        {fields ? (
          <div className="decomp">
            {fields.map((d) => (
              <div className="decomp-row" key={d.k}>
                <span className="decomp-k">{d.k}</span>
                <span className="decomp-v">{d.v}</span>
              </div>
            ))}
          </div>
        ) : agent.busy ? (
          <div className="decomp skel-decomp" aria-label="Building the plan…">
            {[0, 1, 2, 3].map((i) => (
              <div className="decomp-row" key={i}>
                <span className="skel-line" style={{ width: 64 }} />
                <span className="skel-line" style={{ flex: 1, animationDelay: `${i * 0.1}s` }} />
              </div>
            ))}
          </div>
        ) : (
          <div className="plan-empty">The agent generates the plan from your question — it appears here once it decomposes the task.</div>
        )}

        <div className="plan-section-label" style={{ marginTop: 14 }}>Progress <span className="plan-hint">— click a step to see why</span></div>
        <div className="phase-track">
          {phases.map((p, i) => {
            const state = i < phase ? 'done' : i === phase ? 'active' : 'todo';
            const entries = byPhase[i] || [];
            const open = expanded === i;
            return (
              <div key={p}>
                <button
                  className={`phase ${state} ${entries.length ? 'has-detail' : ''} ${open ? 'open' : ''}`}
                  onClick={() => entries.length && setExpanded(open ? null : i)}
                  disabled={!entries.length}
                >
                  <span className="phase-dot">{state === 'done' ? <Icon.check width={11} height={11} /> : i + 1}</span>
                  <span className="phase-label">{p}</span>
                  {entries.length > 0 && <span className="phase-count">{entries.length}</span>}
                  {entries.length > 0 && (
                    <Icon.chevron className="phase-caret" width={12} height={12}
                      style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }} />
                  )}
                </button>
                {open && (
                  <div className="phase-detail">
                    {entries.map((e, j) => <PhaseEntry key={j} e={e} />)}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

const QUALITY_PROFILE = new Set(['profile_csv', 'profile_json', 'profile_context', 'profile_database', 'profile_quality']);
const QUALITY_DIAGNOSE = new Set(['execute_python', 'execute_universal_sql', 'execute_context_sql', 'suggest_fixes']);
const EXPLORE_TOOLS = new Set(['list_context', 'read_doc', 'read_pdf', 'profile_csv', 'profile_json',
  'build_knowledge_graph', 'inspect_sqlite_schema', 'excel_sheets']);

// Which phase a tool call belongs to — mirrors currentPhase()'s routing so the
// drill-down buckets match the progress tracker exactly.
function classifyPhaseIndex(tool, intent, phaseCount) {
  if (intent === 'quality') {
    if (tool === 'answer') return phaseCount - 1;       // Suggest fixes
    if (QUALITY_DIAGNOSE.has(tool)) return 2;           // Diagnose issues
    if (QUALITY_PROFILE.has(tool)) return 1;            // Profile data
    return 0;                                            // Read file
  }
  if (tool === 'answer') return phaseCount - 1;          // Answer
  if (/^execute_/.test(tool)) return 2;                  // Compute
  if (EXPLORE_TOOLS.has(tool)) return 1;                 // Explore
  return 0;                                              // Understand
}

// Bucket the run's trace into phases: each entry pairs the preceding thought,
// the tool action (+ its SQL/code) and the resulting observation — the "why"
// behind each phase. Returns an array of entry-lists, one per phase.
function groupTraceByPhase(trace, intent, phaseCount) {
  const byPhase = Array.from({ length: phaseCount }, () => []);
  let pendingThought = null;
  let lastEntry = null;
  for (const t of trace || []) {
    if (t.kind === STEP_KIND.THOUGHT) { pendingThought = t.text; lastEntry = null; continue; }
    if (t.kind === STEP_KIND.ACTION) {
      const idx = classifyPhaseIndex(t.tool, intent, phaseCount);
      const entry = {
        thought: pendingThought,
        tool: t.tool,
        sql: typeof t.input?.sql === 'string' ? t.input.sql : null,
        code: typeof t.input?.code === 'string' ? t.input.code : null,
        observation: null,
      };
      byPhase[idx].push(entry);
      lastEntry = entry; pendingThought = null;
      continue;
    }
    if (t.kind === STEP_KIND.OBSERVE && lastEntry) {
      lastEntry.observation = { ok: t.ok, summary: t.summary };
      continue;
    }
    if (t.kind === STEP_KIND.ANSWER) {
      byPhase[phaseCount - 1].push({ thought: pendingThought, tool: 'answer', answer: t.summary, observation: null });
      lastEntry = null; pendingThought = null;
    }
  }
  return byPhase;
}

function currentPhase(agent, intent = 'analytic') {
  const run = [...agent.feed].reverse().find((e) => e.type === 'run');
  if (!run) return 0;
  if (run.state === RUN_STATE.DONE) return (intent === 'quality' ? 4 : 4);
  const tools = run.trace.filter((t) => t.kind === 'action').map((t) => t.tool);
  if (intent === 'quality') {
    if (tools.some((t) => t === 'answer')) return 3;            // Suggest fixes
    if (tools.some((t) => QUALITY_DIAGNOSE.has(t))) return 2;   // Diagnose
    if (tools.some((t) => QUALITY_PROFILE.has(t))) return 1;    // Profile
    return 0;                                                    // Read file
  }
  if (tools.some((t) => t.startsWith('execute_') || t === 'answer')) return 2; // Compute
  if (tools.some((t) => ['list_context', 'read_doc', 'profile_csv', 'profile_json', 'build_knowledge_graph', 'inspect_sqlite_schema'].includes(t))) return 1; // Explore
  return 0; // Understand
}

const clip = (s, n) => (s && s.length > n ? `${s.slice(0, n)}…` : (s || ''));

// One step inside a phase drill-down: the thought that led to it, the tool +
// its SQL/code, and the observation it produced — the "why" for that phase.
function PhaseEntry({ e }) {
  return (
    <div className="phase-entry">
      {e.thought && <div className="pe-thought"><Icon.brain width={12} height={12} /> {clip(e.thought, 180)}</div>}
      <div className="pe-action">
        <span className="tool-chip">{e.tool}</span>
        {e.observation && <span className={`ok-pill ${e.observation.ok ? 'ok' : 'err'}`}>{e.observation.ok ? 'ok' : 'error'}</span>}
      </div>
      {(e.sql || e.code) && <pre className="pe-code">{clip(e.sql || e.code, 400)}</pre>}
      {e.answer && <div className="pe-obs">{clip(e.answer, 180)}</div>}
      {e.observation?.summary && <div className="pe-obs">{clip(e.observation.summary, 200)}</div>}
    </div>
  );
}
