import { createContext, useContext, useCallback, useEffect, useState } from 'react';
import { fetchMe, loginUser, registerUser, setToken } from '../../lib/api';

/**
 * Auth state for the whole app. Backed by a real backend (/api/auth/*): the
 * token lives in localStorage and is validated against /api/auth/me on load, so
 * a refresh keeps the user signed in until the token expires or they log out.
 *
 *   const { user, ready, login, register, logout } = useAuth();
 */
const AuthCtx = createContext(null);
export function useAuth() {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);   // { username } | null
  const [ready, setReady] = useState(false); // false until the stored token is checked

  // Validate any stored token once on mount.
  useEffect(() => {
    let alive = true;
    fetchMe()
      .then((me) => { if (alive) setUser(me && me.username ? { username: me.username } : null); })
      .finally(() => { if (alive) setReady(true); });
    return () => { alive = false; };
  }, []);

  const login = useCallback(async (username, password) => {
    const res = await loginUser(username, password);
    setToken(res.token);
    setUser({ username: res.username });
    return res;
  }, []);

  const register = useCallback(async (username, password) => {
    const res = await registerUser(username, password);
    setToken(res.token);
    setUser({ username: res.username });
    return res;
  }, []);

  const logout = useCallback(() => {
    setToken('');
    setUser(null);
  }, []);

  return (
    <AuthCtx.Provider value={{ user, ready, login, register, logout }}>
      {children}
    </AuthCtx.Provider>
  );
}
