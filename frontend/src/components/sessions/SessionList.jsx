import { useEffect, useState } from 'react';
import { Icon } from '../common/Icons';
import { useConfirm } from '../common/ConfirmDialog';
import { usePrompt } from '../common/PromptDialog';
import { useToast } from '../common/Toast';
import ErrorNote from '../common/ErrorNote';
import { API_BASE, createSession, deleteSession, listSessions, renameSession } from '../../lib/api';

const initials = (name) => (name || '?').replace(/[^A-Za-z0-9 ]/g, ' ').trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
const fmt = (iso) => { try { return iso ? new Date(iso).toLocaleString() : '—'; } catch { return iso || '—'; } };

// Friendly relative time ("2h ago", "yesterday", "Mar 3") — calmer than a full
// timestamp in a dense list. Falls back to the locale date for anything old.
const relTime = (iso) => {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const s = (Date.now() - t) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 172800) return 'yesterday';
  if (s < 604800) return `${Math.floor(s / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
};
const fileLabel = (s) => { const n = (s.files || []).length; return `${n} file${n === 1 ? '' : 's'}`; };
const domainOf = (s) => {
  const exts = new Set((s.files || []).map((f) => (f.name?.split('.').pop() || '').toLowerCase()).filter(Boolean));
  if (!exts.size) return '';
  const labels = [...exts].map((e) => ({ csv: 'CSV', xlsx: 'Excel', xls: 'Excel', json: 'JSON', db: 'SQLite', sqlite: 'SQLite', pdf: 'PDF', txt: 'Text', md: 'Markdown' }[e] || e.toUpperCase()));
  return [...new Set(labels)].slice(0, 3).join(' · ');
};

export default function SessionList({ onOpen, onStartTour }) {
  const [sessions, setSessions] = useState(null);   // null = loading
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [editing, setEditing] = useState('');       // session id being renamed
  const [editValue, setEditValue] = useState('');
  const confirm = useConfirm();
  const prompt = usePrompt();
  const toast = useToast();

  const refresh = async () => {
    setError('');
    try { setSessions(await listSessions()); }
    catch (e) { setSessions([]); setError(`${e.message || e} — is the backend running on ${API_BASE}?`); }
  };
  useEffect(() => { refresh(); }, []);

  const rows = (sessions || []).filter((s) => (s.name || '').toLowerCase().includes(query.toLowerCase()));

  const newSession = async () => {
    const name = await prompt({
      title: 'New session',
      label: 'Session name',
      placeholder: 'e.g. Q3 sales review',
      defaultValue: `Workspace ${new Date().toLocaleDateString()}`,
      confirmText: 'Create session',
    });
    if (!name) return;               // cancelled
    setBusy('new'); setError('');
    try { const s = await createSession(name); await refresh(); onOpen(s); }
    catch (e) { setError(String(e.message || e)); }
    finally { setBusy(''); }
  };

  const startRename = (s) => { setEditing(s.id); setEditValue(s.name || ''); };
  const commitRename = async (s) => {
    const next = editValue.trim();
    setEditing(''); setEditValue('');
    if (!next || next === s.name) return;
    setBusy(s.id); setError('');
    try { await renameSession(s.id, next); await refresh(); }
    catch (e) { setError(String(e.message || e)); }
    finally { setBusy(''); }
  };

  const remove = async (s) => {
    if (!(await confirm({ title: 'Delete session?', message: `“${s.name}” — its files, analyses, and chat history will be permanently deleted.`, confirmText: 'Delete', danger: true }))) return;
    setBusy(s.id); setError('');
    try {
      await deleteSession(s.id);
      // If this was the active workspace, drop the cached id so a stale lookup
      // doesn't 404 on next open.
      if (localStorage.getItem('das-workspace-session') === s.id) {
        localStorage.removeItem('das-workspace-session');
      }
      await refresh();
      toast('Session deleted');
    } catch (e) { setError(String(e.message || e)); }
    finally { setBusy(''); }
  };

  return (
    <div className="sessions">
      <div className="sessions-hero" data-tour="tour-sessions">
        <div>
          <h1>Your sessions</h1>
          <p className="subtitle">
            Each session keeps its own files, conversation, agent settings, and results.
            {sessions ? <span className="sessions-tally"> · {sessions.length} total</span> : null}
          </p>
        </div>
        <div className="sessions-hero-actions">
          {onStartTour && (
            <button className="btn btn-ghost" onClick={onStartTour} title="Replay the guided tour">
              <Icon.spark width={16} height={16} /> Take a tour
            </button>
          )}
          <button className="btn btn-primary" data-tour="tour-new-session" onClick={newSession} disabled={busy === 'new'}>
            <Icon.plus width={16} height={16} /> {busy === 'new' ? 'Creating…' : 'New Session'}
          </button>
        </div>
      </div>

      <div className="sessions-toolbar">
        <div className="search" data-tour="tour-search">
          <Icon.search />
          <input placeholder="Search sessions…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
      </div>

      {error && <ErrorNote error={error} onRetry={refresh} />}

      {sessions === null && <div className="cell-muted" style={{ textAlign: 'center', padding: 40 }}>Loading sessions…</div>}

      {sessions !== null && (
        <div className="session-list">
          {rows.map((s) => {
            const renaming = editing === s.id;
            const domain = domainOf(s);
            return (
              <div className={`session-row ${renaming ? 'editing' : ''}`} key={s.id}
                   onClick={() => !renaming && onOpen(s)} role="button" tabIndex={0}
                   onKeyDown={(e) => { if (!renaming && (e.key === 'Enter')) onOpen(s); }}>
                <div className="sr-avatar">{initials(s.name)}</div>

                <div className="sr-main">
                  {renaming ? (
                    <input
                      autoFocus
                      className="sr-rename"
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onBlur={() => commitRename(s)}
                      onKeyDown={(e) => { if (e.key === 'Enter') commitRename(s); if (e.key === 'Escape') { setEditing(''); setEditValue(''); } }}
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <div className="sr-name" title={s.name}>{s.name}</div>
                  )}
                  <div className="sr-meta">
                    <span>{fileLabel(s)}</span>
                    {domain && <><span className="sr-dot">·</span><span className="sr-domain">{domain}</span></>}
                    <span className="sr-dot">·</span>
                    <span title={`Modified ${fmt(s.modified)}`}>edited {relTime(s.modified)}</span>
                  </div>
                </div>

                <div className="sr-actions">
                  <button className="icon-btn sr-act" title="Rename" disabled={busy === s.id}
                          onClick={(e) => { e.stopPropagation(); startRename(s); }}>
                    <Icon.edit width={15} height={15} />
                  </button>
                  <button className="icon-btn danger sr-act" title="Delete" disabled={busy === s.id}
                          onClick={(e) => { e.stopPropagation(); remove(s); }}>
                    <Icon.trash width={15} height={15} />
                  </button>
                  <button className="sr-open" onClick={(e) => { e.stopPropagation(); onOpen(s); }}>
                    Open <Icon.arrowRight width={15} height={15} />
                  </button>
                </div>
              </div>
            );
          })}
          {sessions.length === 0 && (
            <div className="session-empty">
              <Icon.data width={26} height={26} />
              <div>No sessions yet — click <b>New Session</b> to create your first workspace.</div>
            </div>
          )}
          {sessions.length > 0 && rows.length === 0 && (
            <div className="session-empty">No sessions match “{query}”.</div>
          )}
        </div>
      )}
    </div>
  );
}
