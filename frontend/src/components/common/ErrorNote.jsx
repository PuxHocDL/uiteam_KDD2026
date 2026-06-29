import { useState } from 'react';
import { Icon } from './Icons';
import { humanizeError } from '../../lib/errors';

// Friendly, end-user-facing error banner: a plain-language message up front, the
// raw technical text tucked behind a "Details" toggle, and an optional Retry.
export default function ErrorNote({ error, onRetry }) {
  const [open, setOpen] = useState(false);
  if (!error) return null;
  const { message, detail } = humanizeError(error);
  const hasDetail = detail && detail.trim() && detail !== message;

  return (
    <div className="error-note" role="alert">
      <Icon.alert width={16} height={16} className="error-note-ic" />
      <div className="error-note-body">
        <div className="error-note-msg">{message}</div>
        {hasDetail && (
          <button className="error-note-toggle" onClick={() => setOpen((o) => !o)}>
            {open ? 'Hide details' : 'Details'}
          </button>
        )}
        {open && hasDetail && <pre className="error-note-detail">{detail}</pre>}
      </div>
      {onRetry && <button className="btn btn-ghost btn-sm error-note-retry" onClick={onRetry}><Icon.reset width={13} height={13} /> Retry</button>}
    </div>
  );
}
