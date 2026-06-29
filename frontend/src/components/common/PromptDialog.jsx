import { createContext, useContext, useCallback, useState, useEffect, useRef } from 'react';
import { Icon } from './Icons';

/**
 * App-wide single-input dialog — the custom-coded replacement for the browser's
 * window.prompt(). Usage:
 *
 *   const prompt = usePrompt();
 *   const name = await prompt({ title: 'New session', label: 'Name', defaultValue: '…' });
 *   if (name == null) return;            // cancelled
 *
 * Resolves the trimmed string on confirm, or null on cancel / overlay / Escape.
 */
const PromptCtx = createContext(() => Promise.resolve(null));
export function usePrompt() { return useContext(PromptCtx); }

const DEFAULTS = {
  title: 'Enter a value', label: '', message: '', placeholder: '',
  defaultValue: '', confirmText: 'OK', cancelText: 'Cancel',
};

export function PromptProvider({ children }) {
  const [state, setState] = useState(null);
  const [value, setValue] = useState('');
  const inputRef = useRef(null);

  const prompt = useCallback(
    (opts) => new Promise((resolve) => {
      const merged = { ...DEFAULTS, ...opts, resolve };
      setValue(merged.defaultValue || '');
      setState(merged);
    }),
    [],
  );

  const close = useCallback((result) => {
    setState((s) => { s?.resolve(result); return null; });
  }, []);

  const submit = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed) return;             // don't resolve empty — keep the dialog open
    close(trimmed);
  }, [value, close]);

  // Focus + select the prefilled text ONCE when the dialog opens, so the user can
  // type over it. Keyed only on `state` (set once per open) — NOT on `submit`,
  // which is recreated on every keystroke and would otherwise re-select all the
  // text each time you type, making the field impossible to edit.
  useEffect(() => {
    if (!state) return undefined;
    const id = requestAnimationFrame(() => { inputRef.current?.focus(); inputRef.current?.select(); });
    return () => cancelAnimationFrame(id);
  }, [state]);

  useEffect(() => {
    if (!state) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') close(null);
      else if (e.key === 'Enter') submit();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [state, close, submit]);

  return (
    <PromptCtx.Provider value={prompt}>
      {children}
      {state && (
        <div className="modal-overlay" onClick={() => close(null)}>
          <div
            className="modal confirm-modal"
            role="dialog"
            aria-modal="true"
            aria-label={state.title}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h3>{state.title}</h3>
              <span className="spacer" />
              <button className="icon-btn" onClick={() => close(null)} aria-label="Close">
                <Icon.x width={16} height={16} />
              </button>
            </div>
            <div className="modal-body">
              {state.message && <p className="confirm-msg">{state.message}</p>}
              {state.label && <label className="set-label" htmlFor="prompt-input">{state.label}</label>}
              <input
                id="prompt-input"
                ref={inputRef}
                className="prompt-input"
                value={value}
                placeholder={state.placeholder}
                onChange={(e) => setValue(e.target.value)}
              />
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={() => close(null)}>{state.cancelText}</button>
              <button className="btn btn-primary" onClick={submit} disabled={!value.trim()}>
                {state.confirmText}
              </button>
            </div>
          </div>
        </div>
      )}
    </PromptCtx.Provider>
  );
}
