// A single persistent backend session that backs the workspace's Files panel and
// (when it has uploads) the agent run. Lazily created and remembered in localStorage,
// so uploads survive reloads. This is the real workspace behind §12.4 (file CRUD).
import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE, createSession, deleteFile, deleteSession, getSession, listFiles } from '../lib/api';

const KEY = 'das-workspace-session';

export function useWorkspaceSession() {
  const [sid, setSid] = useState(null);
  const [files, setFiles] = useState([]);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  const sidRef = useRef(null);

  const refresh = useCallback(async (id = sidRef.current) => {
    if (!id) return;
    try { setFiles(await listFiles(id)); setError(''); }
    catch (e) { setError(String(e.message || e)); }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        let id = localStorage.getItem(KEY);
        if (id && !(await getSession(id))) id = null;     // stale id → recreate
        if (!id) { id = (await createSession('Workspace')).id; localStorage.setItem(KEY, id); }
        if (!alive) return;
        sidRef.current = id; setSid(id);
        setFiles(await listFiles(id));
      } catch (e) {
        if (alive) setError(`${e.message || e} — is the backend running on ${API_BASE}?`);
      } finally {
        if (alive) setReady(true);
      }
    })();
    return () => { alive = false; };
  }, []);

  const remove = useCallback(async (fid) => {
    if (!sidRef.current) return;
    await deleteFile(sidRef.current, fid);
    refresh();
  }, [refresh]);

  // Wipe this workspace: deletes the backend session (files + analyses + metadata)
  // and provisions a fresh one. Returns the new session id.
  const reset = useCallback(async () => {
    const old = sidRef.current;
    if (old) {
      try { await deleteSession(old); } catch { /* best-effort */ }
      try { localStorage.removeItem(`das-chat-${old}`); } catch { /* ignore */ }
    }
    const fresh = await createSession('Workspace');
    localStorage.setItem(KEY, fresh.id);
    sidRef.current = fresh.id;
    setSid(fresh.id);
    setFiles([]);
    setError('');
    return fresh.id;
  }, []);

  return { sid, files, error, ready, refresh, remove, reset };
}
