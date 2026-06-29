import { useState } from 'react';
import { Icon } from '../common/Icons';
import { useAuth } from '../common/AuthProvider';
import uitLogo from '../../assets/Logo_UIT.png';

// Full-screen auth gate: sign in or create an account. Real backend (hashed
// passwords + signed token); on success the AuthProvider stores the token and
// the app un-gates.
export default function LoginScreen() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState('login');   // 'login' | 'register'
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const isRegister = mode === 'register';

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (!username.trim() || !password) { setError('Enter a username and password.'); return; }
    if (isRegister) {
      if (password.length < 6) { setError('Password must be at least 6 characters.'); return; }
      if (password !== confirm) { setError('Passwords do not match.'); return; }
    }
    setBusy(true);
    try {
      if (isRegister) await register(username.trim(), password);
      else await login(username.trim(), password);
      // success → AuthProvider flips `user`, App swaps to the workspace.
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };

  const swap = (next) => { setMode(next); setError(''); setPassword(''); setConfirm(''); };

  return (
    <div className="login-shell">
      <div className="login-card">
        <div className="login-brand">
          <img className="brand-logo" src={uitLogo} alt="UIT" />
          <div className="login-brand-text">
            <div className="brand-name">Data Agent Studio</div>
            <div className="brand-sub">Conversational data analysis</div>
          </div>
        </div>

        <div className="login-tabs" role="tablist">
          <button className={`login-tab ${!isRegister ? 'on' : ''}`} role="tab" aria-selected={!isRegister}
                  onClick={() => swap('login')}>Sign in</button>
          <button className={`login-tab ${isRegister ? 'on' : ''}`} role="tab" aria-selected={isRegister}
                  onClick={() => swap('register')}>Create account</button>
        </div>

        <form className="login-form" onSubmit={submit}>
          <label className="field">
            <span>Username</span>
            <input
              autoFocus autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="your username"
            />
          </label>

          <label className="field">
            <span>Password</span>
            <div className="login-pw">
              <input
                type={showPw ? 'text' : 'password'}
                autoComplete={isRegister ? 'new-password' : 'current-password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isRegister ? 'at least 6 characters' : 'your password'}
              />
              <button type="button" className="icon-btn sm" tabIndex={-1}
                      title={showPw ? 'Hide password' : 'Show password'}
                      onClick={() => setShowPw((v) => !v)}>
                <Icon.search width={14} height={14} />
              </button>
            </div>
          </label>

          {isRegister && (
            <label className="field">
              <span>Confirm password</span>
              <input
                type={showPw ? 'text' : 'password'}
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="re-enter your password"
              />
            </label>
          )}

          {error && <div className="login-error"><Icon.alert width={15} height={15} /> {error}</div>}

          <button className="btn btn-primary login-submit" type="submit" disabled={busy}>
            {busy ? (isRegister ? 'Creating account…' : 'Signing in…')
                  : (isRegister ? 'Create account' : 'Sign in')}
          </button>
        </form>

        <div className="login-foot">
          {isRegister ? (
            <>Already have an account? <button className="link-btn" onClick={() => swap('login')}>Sign in</button></>
          ) : (
            <>New here? <button className="link-btn" onClick={() => swap('register')}>Create an account</button></>
          )}
        </div>
      </div>
    </div>
  );
}
