// Toasts. App.jsx wraps the whole app in <ToastProvider>; screens call useToast() -> toast(message, kind).
// kind: 'info' (default) | 'success' | 'error'.
import { createContext, useCallback, useContext, useState } from 'react';

const Ctx = createContext(() => {});
const ICONS = { info: 'ph-info', success: 'ph-check-circle', error: 'ph-warning-circle' };

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const dismiss = (id) => setItems((xs) => xs.filter((x) => x.id !== id));
  const toast = useCallback((message, kind = 'info') => {
    const id = Math.random();
    setItems((xs) => [...xs, { id, message: String(message), kind }]);
    setTimeout(() => dismiss(id), kind === 'error' ? 7000 : 4000);
  }, []);
  return (
    <Ctx.Provider value={toast}>
      {children}
      <div style={{ position: 'fixed', right: 20, bottom: 20, display: 'flex', flexDirection: 'column', gap: 8, zIndex: 100, maxWidth: 420 }}>
        {items.map((t) => (
          <div key={t.id} className="card elev-md" onClick={() => dismiss(t.id)} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 14, cursor: 'pointer', color: t.kind === 'error' ? 'var(--color-accent-2-700)' : 'inherit' }}>
            <i className={'ph-duotone ' + (ICONS[t.kind] || ICONS.info)} style={{ fontSize: 18, marginTop: 1 }} />
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  return useContext(Ctx);
}

export default ToastProvider;
