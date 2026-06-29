import { Icon } from './Icons';
import uitLogo from '../../assets/Logo_UIT.png';

const userInitials = (name) =>
  (name || '?').replace(/[^A-Za-z0-9 ]/g, ' ').trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase();

export default function TopBar({ centerTitle, onBack, theme, onToggleTheme, onShowShortcuts, user, onLogout }) {
  return (
    <header className="topbar">
      {onBack ? (
        <button className="topbar-link" onClick={onBack}><Icon.back width={16} height={16} /> Back to Sessions</button>
      ) : (
        <div className="brand">
          <img className="brand-logo" src={uitLogo} alt="UIT" />
          <span className="brand-divider" />
          <div>
            <div className="brand-name">Data Agent Studio</div>
            <div className="brand-sub">Conversational data analysis</div>
          </div>
        </div>
      )}
      <span className="topbar-spacer" />
      {centerTitle && <div className="topbar-center">{centerTitle}</div>}
      <span className="topbar-spacer" />
      {onShowShortcuts && (
        <button className="theme-btn help-btn" onClick={onShowShortcuts} title="Keyboard shortcuts (press ?)" aria-label="Keyboard shortcuts">?</button>
      )}
      <button className="theme-btn" onClick={onToggleTheme} title={theme === 'dark' ? 'Switch to light' : 'Switch to dark'}>
        {theme === 'dark' ? <Icon.sun width={17} height={17} /> : <Icon.moon width={17} height={17} />}
      </button>
      {user && (
        <div className="topbar-user">
          <span className="topbar-avatar" title={user.username}>{userInitials(user.username)}</span>
          <span className="topbar-username">{user.username}</span>
          <button className="topbar-logout" onClick={onLogout} title="Sign out">
            <Icon.back width={15} height={15} /> Sign out
          </button>
        </div>
      )}
    </header>
  );
}
