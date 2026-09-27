import { useCallback, useEffect, useRef, useState } from 'react';

/** Run fn once when deps change. Returns { data, error, loading, reload }. */
export function useAsync(fn, deps = []) {
  const [state, setState] = useState({ data: undefined, error: null, loading: true });
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);

  const reload = useCallback(async () => {
    const id = ++seq.current;
    setState((s) => ({ ...s, loading: true }));
    try {
      const data = await fnRef.current();
      if (id === seq.current) setState({ data, error: null, loading: false });
      return data;
    } catch (error) {
      if (id === seq.current) setState((s) => ({ ...s, error, loading: false }));
    }
  }, []);

  useEffect(() => { reload(); }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, reload };
}

/** Like useAsync, but also re-runs fn every `ms` milliseconds (pass ms = 0/null to stop polling). */
export function usePoll(fn, ms, deps = []) {
  const result = useAsync(fn, deps);
  const { reload } = result;
  useEffect(() => {
    if (!ms) return undefined;
    const t = setInterval(reload, ms);
    return () => clearInterval(t);
  }, [ms, reload, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps
  return result;
}
