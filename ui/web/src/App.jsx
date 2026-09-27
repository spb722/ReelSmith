import { useEffect, useRef, useState } from 'react';
import { useRoute, navigate } from './router.js';
import { usePoll } from './hooks.js';
import { listProjects } from './api.js';
import { ToastProvider } from './components/Toast.jsx';
import Home from './screens/Home.jsx';
import Wizard from './screens/Wizard.jsx';
import ProjectPage from './screens/project/ProjectPage.jsx';
import Characters from './screens/Characters.jsx';
import Styles from './screens/Styles.jsx';
import Defaults from './screens/Defaults.jsx';
import Setup from './screens/Setup.jsx';

const NAV = [
  ['home', 'Projects', 'ph-film-strip', '/'],
  ['characters', 'Characters', 'ph-user-circle', '/characters'],
  ['styles', 'Styles', 'ph-palette', '/styles'],
  ['defaults', 'Defaults', 'ph-sliders-horizontal', '/defaults'],
  ['setup', 'Setup', 'ph-gear-six', '/setup'],
];
const TITLES = { home: 'Projects', wizard: 'New project', characters: 'Characters', styles: 'Styles', defaults: 'Defaults', setup: 'Setup' };

function readTheme() {
  try { return localStorage.getItem('rs-theme') === 'dark' ? 'dark' : 'light'; } catch { return 'light'; }
}

function screenOf(parts) {
  if (parts.length === 0) return { key: 'home' };
  if (parts[0] === 'new') return { key: 'wizard' };
  if (parts[0] === 'p' && parts[1]) return { key: 'project', name: parts[1], tab: parts[2] || null };
  if (TITLES[parts[0]]) return { key: parts[0] };
  return { key: 'home' };
}

function renderScreen(s) {
  switch (s.key) {
    case 'wizard': return <Wizard />;
    case 'project': return <ProjectPage key={s.name} name={s.name} tab={s.tab} />;
    case 'characters': return <Characters />;
    case 'styles': return <Styles />;
    case 'defaults': return <Defaults />;
    case 'setup': return <Setup />;
    default: return <Home />;
  }
}

export default function App() {
  const { path, parts } = useRoute();
  const mainRef = useRef(null);
  const screen = screenOf(parts);
  const [theme, setTheme] = useState(readTheme);
  const { data: projects } = usePoll(listProjects, 3000, []);

  useEffect(() => {
    // Class on <html> too, so modals/portals outside the shell get the dark tokens.
    document.documentElement.classList.toggle('rs-dark', theme === 'dark');
    try { localStorage.setItem('rs-theme', theme); } catch { /* storage blocked */ }
  }, [theme]);

  // "paused" = waiting for you at the narration checkpoint; the design lists it with running ones.
  const running = (projects || []).filter((p) => p.status === 'running' || p.status === 'paused');
  const runsLabel = running.length + (running.length === 1 ? ' run going' : ' runs going');
  const activeNav = ['home', 'wizard', 'project'].includes(screen.key) ? 'home' : screen.key;
  const title = 'ReelSmith Studio — ' + (screen.key === 'project' ? screen.name : TITLES[screen.key]);
  const openRun = (name) => navigate(`/p/${encodeURIComponent(name)}/run`);

  useEffect(() => { document.title = title; }, [title]);
  useEffect(() => { if (mainRef.current) mainRef.current.scrollTop = 0; }, [path]);

  return (
    <ToastProvider>
      <div className={'rs-shell' + (theme === 'dark' ? ' rs-dark' : '')}>
        <div className="rs-titlebar">
          <div style={{ display: 'flex', gap: 8 }}>
            {[0, 1, 2].map((i) => <span key={i} style={{ width: 12, height: 12, borderRadius: '50%', background: 'var(--color-neutral-300)' }} />)}
          </div>
          <div style={{ flex: 1, textAlign: 'center', fontSize: 13, color: 'color-mix(in srgb,var(--color-text) 60%,transparent)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</div>
          {running.length > 0 && (
            <button className="rs-hover-accent" onClick={() => openRun(running[0].name)} style={{ display: 'flex', alignItems: 'center', gap: 8, font: 'inherit', fontSize: 13, color: 'var(--color-accent-700)', background: 'none', border: 0, cursor: 'pointer', padding: '4px 8px', borderRadius: 'var(--radius-md)' }}>
              <span className="rs-pulse-dot" />{runsLabel}
            </button>
          )}
          <button className="btn btn-icon" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} title={theme === 'dark' ? 'Light mode' : 'Dark mode'} style={{ width: 30, height: 30 }}>
            <i className={theme === 'dark' ? 'ph-duotone ph-sun' : 'ph-duotone ph-moon'} style={{ fontSize: 17 }} />
          </button>
        </div>

        <div className="rs-body">
          <nav className="rs-nav">
            <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 23, letterSpacing: '-0.015em', paddingLeft: 10, lineHeight: 1 }}>
              ReelSmith <span style={{ fontWeight: 400, fontStyle: 'italic' }}>Studio</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {NAV.map(([k, label, icon, path]) => (
                <button key={k} className={'rs-nav-item' + (activeNav === k ? ' is-active' : '')} onClick={() => navigate(path)}>
                  <i className={'ph-duotone ' + icon} style={{ fontSize: 19 }} />
                  <span style={{ flex: 1, textAlign: 'left' }}>{label}</span>
                  {k === 'home' && running.length > 0 && <span style={{ fontSize: 12, whiteSpace: 'nowrap', color: 'var(--color-accent-700)' }}>{running.length} live</span>}
                </button>
              ))}
            </div>
            <div style={{ flex: 1 }} />
            {running.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingLeft: 10 }}>
                <div style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap', color: 'color-mix(in srgb,var(--color-text) 65%,transparent)' }}>
                  <span className="rs-pulse-dot" />{runsLabel}
                </div>
                {running.map((p) => {
                  const waiting = p.status === 'paused';
                  const pct = Math.floor(p.percent || 0);
                  return (
                    <button key={p.name} className="rs-link-btn" onClick={() => openRun(p.name)} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 13, width: '100%' }}>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                        <span className="rs-muted">{waiting ? 'waiting' : pct + '%'}</span>
                      </span>
                      <span className="rs-bar"><span style={{ width: pct + '%', background: waiting ? 'var(--color-process-yellow)' : 'var(--color-accent)' }} /></span>
                    </button>
                  );
                })}
              </div>
            )}
            <div className="seg" style={{ alignSelf: 'flex-start', marginLeft: 10 }}>
              <label className="seg-opt"><input type="radio" name="rs-theme" checked={theme !== 'dark'} onChange={() => setTheme('light')} /><i className="ph-duotone ph-sun" />Light</label>
              <label className="seg-opt"><input type="radio" name="rs-theme" checked={theme === 'dark'} onChange={() => setTheme('dark')} /><i className="ph-duotone ph-moon" />Dark</label>
            </div>
          </nav>

          <main className="rs-main" ref={mainRef}>
            <div className="rs-content">{renderScreen(screen)}</div>
          </main>
        </div>
      </div>
    </ToastProvider>
  );
}
