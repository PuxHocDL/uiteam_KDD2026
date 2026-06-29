import { createContext, useContext, useCallback, useState } from 'react';
import { Icon } from './Icons';

/**
 * App-wide toast notifications. Usage:
 *   const toast = useToast();
 *   toast('File removed');                 // success (default)
 *   toast('Reset failed: …', 'error');     // error
 *   toast('Add your API key first', 'info');
 * Toasts auto-dismiss; click to dismiss early. Stacks bottom-left.
 */
const ToastCtx = createContext(() => {});
export function useToast() { return useContext(ToastCtx); }

const ICON = { success: Icon.check, error: Icon.alert, info: Icon.spark };
let seq = 0;

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const dismiss = useCallback((id) => setItems((xs) => xs.filter((t) => t.id !== id)), []);
  const toast = useCallback((message, type = 'success', ms = 2800) => {
    const id = ++seq;
    setItems((xs) => [...xs, { id, message, type }]);
    if (ms) setTimeout(() => dismiss(id), ms);
    return id;
  }, [dismiss]);

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {items.map((t) => {
          const I = ICON[t.type] || Icon.check;
          return (
            <div className={`toast toast-${t.type}`} key={t.id} onClick={() => dismiss(t.id)}>
              <I width={16} height={16} />
              <span>{t.message}</span>
            </div>
          );
        })}
      </div>
    </ToastCtx.Provider>
  );
}
