import { Icon } from '../common/Icons';
import { RUN_STATE } from '../../lib/eventContract';
import { categoryOf } from '../../data/tools';

// Live, animated visualization of what the agent is doing right now — which tool
// it picked and what kind of work is running. Pure UI animation (no prose).
const ICONS = { list: Icon.list, file: Icon.file, table: Icon.table, data: Icon.data, code: Icon.code, spark: Icon.spark, check: Icon.check, search: Icon.search, tool: Icon.tool, globe: Icon.globe, plug: Icon.plug };

const PHASE = {
  [RUN_STATE.THINKING]: 'thinking',
  [RUN_STATE.STEP_PROPOSED]: 'picking',
  [RUN_STATE.AWAITING_USER]: 'picking',
  [RUN_STATE.TOOL_EXECUTING]: 'running',
  [RUN_STATE.OBSERVING]: 'observing',
  [RUN_STATE.DONE]: 'done',
  [RUN_STATE.FAILED]: 'done',
  [RUN_STATE.CANCELLED]: 'done',
};

export default function AgentActivity({ agent, tools }) {
  const run = [...agent.feed].reverse().find((e) => e.type === 'run');
  const phase = run ? (PHASE[run.state] || 'thinking') : 'idle';
  const activeTool = activeToolOf(run);
  const category = activeTool ? categoryOf(activeTool, tools) : null;
  const enabledTools = tools.filter((t) => t.enabled);

  return (
    <div className={`activity-stage ph-${phase}`}>
      {/* Agent core */}
      <div className={`agent-core ${phase === 'running' || phase === 'thinking' ? 'busy' : ''}`}>
        <span className="core-ring" />
        <span className="core-ring r2" />
        <span className="core-glow" />
        <Icon.bot width={26} height={26} />
      </div>

      {/* Tool dock — the picked tool lights up */}
      <div className="tool-dock">
        {enabledTools.map((t) => {
          const IconC = ICONS[t.icon] || Icon.tool;
          const on = t.name === activeTool;
          return (
            <div key={t.name} className={`dock-tool ${on ? 'on' : ''}`} title={t.name}>
              {on && <span className="uplink"><i /><i /><i /></span>}
              <span className="dock-ic"><IconC width={17} height={17} /></span>
              {on && <span className="dock-name">{t.name}</span>}
            </div>
          );
        })}
      </div>

      {/* Execution window — animation depends on what's running */}
      <div className="exec-window">
        {phase === 'idle' && <IdleViz />}
        {(phase === 'thinking' || phase === 'picking') && <ThinkingViz picking={phase === 'picking'} tool={activeTool} />}
        {phase === 'observing' && <ObservingViz />}
        {phase === 'done' && <SuccessViz />}
        {phase === 'running' && (
          ['sql', 'python'].includes(category)
            ? <CodeRunner kind={category} />
            : <Scanner kind={category} />
        )}
      </div>
    </div>
  );
}

function activeToolOf(run) {
  if (!run) return null;
  const actions = run.trace.filter((t) => t.kind === 'action');
  const last = actions[actions.length - 1];
  if (!last) return null;
  if (['running', 'proposed'].includes(last.status)) return last.tool;
  return run.state === RUN_STATE.OBSERVING ? last.tool : null;
}

// --- running compute (SQL / Python): faux code lines + sweep + streaming rows -
function CodeRunner({ kind }) {
  return (
    <div className="exec-card compute">
      <div className="exec-top">
        <span className="dot r" /><span className="dot y" /><span className="dot g" />
        <span className="lang-chip">{kind === 'sql' ? 'SQL · DuckDB' : 'Python · pandas'}</span>
        <span className="run-spinner" />
      </div>
      <div className="code-body">
        <span className="scan-line" />
        {[64, 86, 48, 72, 38, 80].map((w, i) => (
          <span key={i} className="code-line" style={{ width: `${w}%`, animationDelay: `${i * 0.12}s` }} />
        ))}
        <span className="cursor" />
      </div>
      <div className="exec-divider"><span /></div>
      <div className="stream-rows">
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className="stream-row" style={{ animationDelay: `${i * 0.18}s` }} />
        ))}
      </div>
    </div>
  );
}

// --- running scan (read/profile/list): sheet + sweep + filling stat bars ------
function Scanner({ kind }) {
  return (
    <div className="exec-card scan">
      <div className="exec-top">
        <Icon.eye width={14} height={14} style={{ color: 'var(--ds-observe)' }} />
        <span className="lang-chip cyan">{kind === 'list' ? 'Scanning context' : 'Profiling data'}</span>
        <span className="run-spinner cyan" />
      </div>
      <div className="scan-sheet">
        <span className="scan-line v" />
        <div className="sheet-grid">
          {Array.from({ length: 24 }).map((_, i) => <span key={i} className="cell" style={{ animationDelay: `${(i % 6) * 0.08}s` }} />)}
        </div>
      </div>
      <div className="stat-bars">
        {[40, 75, 55, 90, 30, 65].map((h, i) => (
          <span key={i} className="stat-bar" style={{ height: `${h}%`, animationDelay: `${i * 0.1}s` }} />
        ))}
      </div>
    </div>
  );
}

function ThinkingViz({ picking, tool }) {
  return (
    <div className="exec-card think">
      <div className="think-orbits">
        <span className="t-dot" /><span className="t-dot" /><span className="t-dot" />
      </div>
      <div className="think-caption">
        {picking ? <>Selecting tool <span className="lang-chip">{tool}</span></> : 'Reasoning'}
      </div>
    </div>
  );
}

function ObservingViz() {
  return (
    <div className="exec-card observe">
      <div className="particles">{Array.from({ length: 7 }).map((_, i) => <span key={i} style={{ animationDelay: `${i * 0.12}s` }} />)}</div>
      <div className="think-caption">Reading result</div>
    </div>
  );
}

function SuccessViz() {
  return (
    <div className="exec-card success">
      <span className="burst" /><span className="burst b2" />
      <span className="success-check"><Icon.check width={26} height={26} /></span>
    </div>
  );
}

function IdleViz() {
  return (
    <div className="exec-card idle">
      <div className="idle-eq">{[0, 1, 2, 3, 4].map((i) => <span key={i} style={{ animationDelay: `${i * 0.15}s` }} />)}</div>
      <div className="think-caption dim">Ask a question to watch the agent work</div>
    </div>
  );
}
