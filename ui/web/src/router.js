import { useEffect, useState } from 'react';

function current() {
  const path = window.location.hash.replace(/^#/, '') || '/';
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  return { path, parts };
}

/** Current hash route: { path: '/p/foo/run', parts: ['p', 'foo', 'run'] }. */
export function useRoute() {
  const [route, setRoute] = useState(current);
  useEffect(() => {
    const onHash = () => setRoute(current());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  return route;
}

/** Go to a route, e.g. navigate('/p/tiny_changes/run'). */
export function navigate(path) {
  window.location.hash = path.startsWith('/') ? path : '/' + path;
}
