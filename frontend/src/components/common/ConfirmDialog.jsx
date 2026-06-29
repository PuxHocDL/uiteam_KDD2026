import { createContext, useContext, useCallback, useState, useEffect } from 'react';
import { Icon } from './Icons';

/**
 * App-wide confirmation dialog — replaces the browser-native window.confirm()
 * with a branded modal. Usage:
 *
 *   const confirm = useConfirm();
 *   if (!(await confirm({ title, message, confirmText, danger: true }))) return;
 *
 * Resolves true on confirm, false on cancel / overlay click / Escape.
 */
const ConfirmCtx = createContext(() => Promise.resolve(false));
export function useConfirm() { return useContext(ConfirmCtx); }

const DEFAULTS = { title: 'Are you sure?', message: '', confirmText: 'Confirm', cancelText: 'Cancel', danger: false };

export function ConfirmProvider({ children }) {
  const [state, setState] = useState(null);

  const confirm = useCallback(
    (opts) => new Promise((resolve) => setState({ ...DEFAULTS, ...opts, resolve })),
    [],
  );

  const close = useCallback((value) => {
    setState((s) => { s?.resolve(value); return null; });
  }, []);

  useEffect(() => {
    if (!state) return;
    const onKey = (e) => {
      if (e.key === 'Escape') close(false);
      if (e.key === 'Enter') close(true);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [state, close]);

  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      {state && (
        <div className="modal-overlay" onClick={() => close(false)}>
          <div
            className="modal confirm-modal"
            role="alertdialog"
            aria-modal="true"
            aria-label={state.title}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h3>{state.title}</h3>
              <span className="spacer" />
              <button className="icon-btn" onClick={() => close(false)} aria-label="Close">
                <Icon.x width={16} height={16} />
              </button>
            </div>
            <div className="modal-body">
              {state.message.split('\n').map((line, i) => (
                <p className="confirm-msg" key={i}>{line}</p>
              ))}
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={() => close(false)}>{state.cancelText}</button>
              <button
                className={`btn ${state.danger ? 'btn-danger' : 'btn-primary'}`}
                onClick={() => close(true)}
                autoFocus
              >
                {state.confirmText}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmCtx.Provider>
  );
}
