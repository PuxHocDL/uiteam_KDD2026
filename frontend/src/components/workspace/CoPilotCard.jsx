import { useState } from 'react';
import { Icon } from '../common/Icons';

// Shown when a run is AWAITING_USER (Co-pilot mode, or a sensitive tool in
// Autopilot). Exposes the five Co-pilot commands (approve / edit / reject / guide / cancel).
export default function CoPilotCard({ awaiting, onApprove, onEdit, onReject, onGuide, onCancel }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => JSON.stringify(awaiting.input, null, 2));
  const [hint, setHint] = useState('');

  const submitEdit = () => {
    try { onEdit(JSON.parse(draft)); setEditing(false); }
    catch { onEdit(awaiting.input); setEditing(false); } // mock: ignore parse errors
  };

  return (
    <div className="copilot-card">
      <div className="cc-head">
        <Icon.steps width={16} height={16} />
        {awaiting.sensitive ? 'Approval required — sensitive tool' : 'Review this step before it runs'}
      </div>
      <div className="cc-reason">{awaiting.reason}</div>
      <div className="cc-action">
        <span className="tool-chip">{awaiting.action}</span>
        {!editing && <span style={{ fontFamily: 'var(--ds-mono)', fontSize: 12, color: 'var(--ds-muted)' }}>{compact(awaiting.input)}</span>}
      </div>

      {editing && (
        <textarea className="cc-edit" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} />
      )}

      <div className="cc-actions">
        {!editing ? (
          <>
            <button className="btn btn-accent btn-sm" onClick={onApprove}><Icon.check width={15} height={15} /> Approve &amp; run</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(true)}><Icon.edit width={14} height={14} /> Edit</button>
            <button className="btn btn-ghost btn-sm" onClick={onReject}><Icon.x width={14} height={14} /> Reject</button>
            <button className="btn btn-danger btn-sm" onClick={onCancel}><Icon.stop width={14} height={14} /> Cancel run</button>
          </>
        ) : (
          <>
            <button className="btn btn-accent btn-sm" onClick={submitEdit}><Icon.check width={15} height={15} /> Run edited</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(false)}>Discard</button>
          </>
        )}
      </div>

      {!editing && (
        <div className="cc-guide-row">
          <input
            placeholder="Add a hint to steer the next step…"
            value={hint}
            onChange={(e) => setHint(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && hint.trim()) { onGuide(hint.trim()); setHint(''); } }}
          />
          <button className="btn btn-ghost btn-sm" disabled={!hint.trim()} onClick={() => { onGuide(hint.trim()); setHint(''); }}>
            <Icon.spark width={14} height={14} /> Guide
          </button>
        </div>
      )}
    </div>
  );
}

function compact(obj) {
  const s = JSON.stringify(obj);
  return s.length > 80 ? s.slice(0, 78) + '…' : s;
}
