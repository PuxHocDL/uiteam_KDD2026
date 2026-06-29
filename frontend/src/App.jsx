import { useCallback, useEffect, useState } from 'react';
import TopBar from './components/common/TopBar';
import SessionList from './components/sessions/SessionList';
import Workspace from './components/workspace/Workspace';
import LoginScreen from './components/auth/LoginScreen';
import Tour from './components/common/Tour';
import ShortcutsModal from './components/common/ShortcutsModal';
import { TOUR_STEPS } from './data/tourSteps';
import { createSession, listSessions } from './lib/api';
import { useAuth } from './components/common/AuthProvider';

const TOUR_SEEN_KEY = 'das-tour-done-v1';

export default function App() {
  const { user, ready, logout } = useAuth();
  const [session, setSession] = useState(null); // null → sessions list; obj → workspace
  const [theme, setTheme] = useState(() => localStorage.getItem('das-theme-v2') || 'dark');
  const [tourOpen, setTourOpen] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('das-theme-v2', theme);
  }, [theme]);

  // Open the chosen backend session: pin it as the active workspace so
  // `useWorkspaceSession` picks it up on mount. `key={session.id}` remounts
  // the Workspace cleanly when switching between sessions.
  const openSession = useCallback((s) => {
    if (s?.id) localStorage.setItem('das-workspace-session', s.id);
    setSession(s);
  }, []);

  const onLogout = () => { setSession(null); logout(); };

  // First-run onboarding: auto-launch the tour once, on the Sessions screen.
  useEffect(() => {
    if (user && !localStorage.getItem(TOUR_SEEN_KEY)) setTourOpen(true);
  }, [user]);

  // Global keyboard shortcuts: `?` opens the help sheet, `/` jumps to the chat
  // box. Both no-op while the user is typing in a field or a dialog is open, so
  // they never hijack normal input.
  useEffect(() => {
    const onKey = (e) => {
      if (!user) return;
      const el = document.activeElement;
      const typing = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
      if (typing || document.querySelector('.modal-overlay')) return;
      if (e.key === '?') { e.preventDefault(); setShowShortcuts(true); }
      else if (e.key === '/') {
        const input = document.querySelector('.chat-input');
        if (input) { e.preventDefault(); input.focus(); }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [user]);

  const closeTour = useCallback(() => {
    setTourOpen(false);
    localStorage.setItem(TOUR_SEEN_KEY, '1');
  }, []);

  // Bridge the tour across screens. To show the workspace steps we open the most
  // recent session (or create one) — the same path a user takes manually.
  const tourNavigate = useCallback(async (target) => {
    if (target === 'sessions') { setSession(null); return; }
    if (target === 'workspace' && !session) {
      try {
        const list = await listSessions().catch(() => []);
        const s = (list && list[0]) || await createSession('Tour workspace');
        openSession(s);
      } catch { /* backend down — the step falls back to a centered card */ }
    }
  }, [session, openSession]);

  // Wait for the stored token to be validated before deciding what to show.
  if (!ready) return <div className="app-splash">Loading…</div>;
  // Auth gate: nothing in the app is reachable until signed in.
  if (!user) return <LoginScreen />;

  return (
    <div className="app-shell">
      <TopBar
        centerTitle={session ? session.name : null}
        onBack={session ? () => setSession(null) : null}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
        onShowShortcuts={() => setShowShortcuts(true)}
        user={user}
        onLogout={onLogout}
      />
      <div className="view">
        {session ? <Workspace key={session.id} /> : <SessionList onOpen={openSession} onStartTour={() => setTourOpen(true)} />}
      </div>
      <Tour
        open={tourOpen}
        steps={TOUR_STEPS}
        page={session ? 'workspace' : 'sessions'}
        onNavigate={tourNavigate}
        onClose={closeTour}
      />
      {showShortcuts && <ShortcutsModal onClose={() => setShowShortcuts(false)} />}
    </div>
  );
}
