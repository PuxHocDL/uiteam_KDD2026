import { MODE } from '../../lib/eventContract';
import { Icon } from './Icons';

// Segmented control for the two interaction modes (Autopilot / Co-pilot).
export default function ModeToggle({ mode, onChange, disabled }) {
  return (
    <div className="mode-toggle" role="tablist" aria-label="Interaction mode">
      <button
        className={`mode-opt ${mode === MODE.AUTOPILOT ? 'active' : ''}`}
        onClick={() => onChange(MODE.AUTOPILOT)}
        disabled={disabled}
        title="Agent runs end-to-end; you watch the trace"
      >
        <Icon.zap width={14} height={14} /> Autopilot
      </button>
      <button
        className={`mode-opt copilot ${mode === MODE.COPILOT ? 'active copilot' : ''}`}
        onClick={() => onChange(MODE.COPILOT)}
        disabled={disabled}
        title="Agent pauses at every step for your approval"
      >
        <Icon.steps width={14} height={14} /> Co-pilot
      </button>
    </div>
  );
}
