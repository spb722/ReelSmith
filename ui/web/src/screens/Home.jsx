import { useEffect, useState } from 'react';
import { deleteProject, duplicateProject, getMeta, listProjects } from '../api.js';
import { useAsync, usePoll } from '../hooks.js';
import { navigate } from '../router.js';
import Modal from '../components/Modal.jsx';
import StatusBadge, { normStatus } from '../components/StatusBadge.jsx';
import { useToast } from '../components/Toast.jsx';

const FILTERS = [['all', 'All'], ['draft', 'Draft'], ['running', 'Running'], ['stopped', 'Stopped'], ['finished', 'Finished']];
const NAME_RE = /^[A-Za-z0-9_-]+$/;

export function fmtDur(t) {
  t = Math.round(t || 0);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return h ? h + 'h ' + m + 'm' : m ? m + 'm ' + String(s).padStart(2, '0') + 's' : s + 's';
}
export const money = (v) => '$' + Number(v || 0).toFixed(2);

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(new Date()) - day(d)) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
}

function metaLine(p) {
  const st = normStatus(p.status);
  if (st === 'draft' || !p.last_run_at) return 'Never run';
  const date = fmtDate(p.last_run_at), dur = fmtDur(p.duration_s);
  if (st === 'finished') return date + ' · ' + dur;
  if (st === 'running' || st === 'paused') return 'Started ' + date.toLowerCase() + ' · ' + dur;
  return date + ' · ran ' + dur;
}

const openTabFor = (p) => (['running', 'paused', 'stopped'].includes(normStatus(p.status)) ? 'run' : 'overview');
const openProject = (p) => navigate(`/p/${encodeURIComponent(p.name)}/${openTabFor(p)}`);

export default function Home() {
  const toast = useToast();
  const { data: projects, error, reload } = usePoll(listProjects, 3000, []);
  const { data: meta } = useAsync(getMeta, []);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [menu, setMenu] = useState(null);
  const [dialog, setDialog] = useState(null); // { kind: 'delete'|'duplicate', name, newName? }
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!menu) return undefined;
    const close = () => setMenu(null);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, [menu]);

  const styleName = (id) => ((meta && meta.styles) || []).find((s) => s.id === id)?.name || id || '—';
  const charName = (id) => (id ? ((meta && meta.characters) || []).find((c) => c.id === id)?.name || id : 'No character');

  const all = projects || [];
  const filterKey = (p) => (normStatus(p.status) === 'paused' ? 'running' : normStatus(p.status));
  const counts = { all: all.length };
  all.forEach((p) => { counts[filterKey(p)] = (counts[filterKey(p)] || 0) + 1; });
  const runningCount = all.filter((p) => ['running', 'paused'].includes(normStatus(p.status))).length;
  const visible = all.filter((p) => (filter === 'all' || filterKey(p) === filter) && p.name.toLowerCase().includes(search.toLowerCase()));
  const names = all.map((p) => p.name);

  const dupName = dialog && dialog.kind === 'duplicate' ? dialog.newName : '';
  const dupError = !dupName ? '' : !NAME_RE.test(dupName) ? 'Use only letters, numbers, - and _. No spaces.' : names.includes(dupName) ? `A project called “${dupName}” already exists.` : '';

  const startDuplicate = (p) => {
    let id = p.name + '_copy', k = 2;
    while (names.includes(id)) id = p.name + '_copy' + k++;
    setDialog({ kind: 'duplicate', name: p.name, newName: id });
  };

  const run = async (fn, okMsg) => {
    setBusy(true);
    try { await fn(); toast(okMsg, 'success'); setDialog(null); reload(); } catch (e) { toast(e.message, 'error'); } finally { setBusy(false); }
  };

  const loaded = projects !== undefined;
  const empty = loaded && all.length === 0;

  return (
    <div className="rs-stack">
      <div className="rs-page-head">
        <div><h1>Projects</h1><div className="rs-sub">{!loaded ? 'Loading…' : empty ? 'Nothing here yet' : `${all.length} projects · ${runningCount} running`}</div></div>
        <span className="rs-spacer" />
        <button className="btn btn-primary" onClick={() => navigate('/new')}><i className="ph-duotone ph-plus" />New project</button>
      </div>

      {error && !loaded && <div className="rs-danger-text">Couldn’t load projects: {error.message}</div>}

      {all.length > 0 && (
        <>
          <div style={{ display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ position: 'relative', width: 260, maxWidth: '100%' }}>
              <i className="ph-duotone ph-magnifying-glass" style={{ position: 'absolute', left: 10, top: 10, fontSize: 16, opacity: 0.6 }} />
              <input className="input" placeholder="Search by name" value={search} onChange={(e) => setSearch(e.target.value)} style={{ paddingLeft: 32 }} />
            </div>
            <div className="seg" style={{ flexWrap: 'wrap', maxWidth: '100%' }}>
              {FILTERS.map(([k, label]) => (
                <label key={k} className="seg-opt">
                  <input type="radio" name="rs-filter" checked={filter === k} onChange={() => setFilter(k)} />
                  {label}<span style={{ fontSize: 11, opacity: 0.7 }}>{counts[k] || 0}</span>
                </label>
              ))}
            </div>
          </div>
          {visible.length === 0 && <div style={{ fontSize: 15, padding: '30px 0', color: 'color-mix(in srgb,var(--color-text) 65%,transparent)' }}>No projects match. Clear the search or pick another status.</div>}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(220px,1fr))', gap: '40px 30px' }}>
            {visible.map((p) => {
              const st = normStatus(p.status);
              const live = st === 'running' || st === 'paused';
              return (
                <div key={p.name} style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>
                  <div style={{ position: 'relative', width: '100%', aspectRatio: '4/5', borderRadius: 'var(--radius-md)', overflow: 'hidden', background: 'var(--color-surface)' }}>
                    {p.thumbnail_url ? (
                      <div role="img" aria-label={p.has_cover ? 'Cover' : 'First screenshot'} onClick={() => openProject(p)} style={{ width: '100%', height: '100%', backgroundSize: 'cover', backgroundPosition: 'top center', cursor: 'pointer', backgroundImage: `url("${p.thumbnail_url}")` }} />
                    ) : (
                      <div onClick={() => openProject(p)} style={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center', cursor: 'pointer' }}>
                        <i className="ph-duotone ph-image" style={{ fontSize: 36, opacity: 0.4 }} />
                      </div>
                    )}
                    {live && (
                      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 4, background: 'color-mix(in srgb,var(--color-bg) 70%,transparent)', pointerEvents: 'none' }}>
                        <div style={{ height: '100%', width: (p.percent || 0) + '%', background: st === 'paused' ? 'var(--color-process-yellow)' : 'var(--color-accent)' }} />
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, position: 'relative' }}>
                    <button className="rs-link-btn" onClick={() => openProject(p)} style={{ flex: 1, minWidth: 0, fontWeight: 600, fontSize: 17, overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</button>
                    <button className="btn btn-icon" style={{ width: 28, height: 28 }} aria-label="Project menu" onClick={(e) => { e.stopPropagation(); setMenu(menu === p.name ? null : p.name); }}>
                      <i className="ph-duotone ph-dots-three" style={{ fontSize: 18 }} />
                    </button>
                    {menu === p.name && (
                      <div className="rs-menu" onClick={(e) => e.stopPropagation()}>
                        <button className="rs-menu-item" onClick={() => { setMenu(null); openProject(p); }}><i className="ph-duotone ph-folder-open" />Open</button>
                        <button className="rs-menu-item" onClick={() => { setMenu(null); startDuplicate(p); }}><i className="ph-duotone ph-copy" />Duplicate</button>
                        <button className="rs-menu-item is-danger" onClick={() => { setMenu(null); setDialog({ kind: 'delete', name: p.name }); }}><i className="ph-duotone ph-trash" />Delete…</button>
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <StatusBadge status={st} percent={p.percent} />
                    <span className="rs-dim">{metaLine(p)}</span>
                  </div>
                  {st === 'stopped' && p.stop_reason && <div className="rs-danger-text" style={{ fontSize: 13, lineHeight: 1.4 }}>{p.stop_reason}</div>}
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span className="tag tag-neutral">{styleName(p.style)}</span>
                    <span className="tag tag-neutral" style={{ gap: 4 }}><i className="ph-duotone ph-user" />{charName(p.character)}</span>
                    <span className="rs-spacer" />
                    <span style={{ fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>{money(p.spent_usd)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {empty && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 15, maxWidth: 520, padding: '50px 0' }}>
          <i className="ph-duotone ph-film-slate" style={{ fontSize: 48, color: 'var(--color-accent)' }} />
          <h2 style={{ margin: 0 }}>No projects yet</h2>
          <p style={{ fontSize: 16, margin: 0 }}>A project is a folder of 3–15 book screenshots. ReelSmith turns them into a 40–60 second vertical reel, then an Instagram cover.</p>
          <div><button className="btn btn-primary" onClick={() => navigate('/new')}><i className="ph-duotone ph-plus" />New project</button></div>
        </div>
      )}

      <Modal
        open={dialog?.kind === 'delete'}
        title={dialog ? `Delete ${dialog.name}?` : ''}
        onClose={() => setDialog(null)}
        footer={<>
          <button className="btn btn-secondary" onClick={() => setDialog(null)}>Cancel</button>
          <button className="btn btn-primary" disabled={busy} style={{ background: 'var(--color-accent-2-600)' }} onClick={() => run(() => deleteProject(dialog.name), `Deleted ${dialog.name}.`)}>Delete project</button>
        </>}
      >
        This removes the project folder: screenshots, settings, run history and outputs. It can’t be undone.
      </Modal>

      <Modal
        open={dialog?.kind === 'duplicate'}
        title={dialog ? `Duplicate ${dialog.name}` : ''}
        onClose={() => setDialog(null)}
        footer={<>
          <button className="btn btn-secondary" onClick={() => setDialog(null)}>Cancel</button>
          <button className="btn btn-primary" disabled={busy || !dupName || !!dupError} onClick={() => run(() => duplicateProject(dialog.name, dupName), `Made ${dupName}.`)}>Duplicate</button>
        </>}
      >
        <div className="field">
          <label>New project name</label>
          <input className="input rs-mono" autoFocus value={dupName} onChange={(e) => setDialog({ ...dialog, newName: e.target.value })} />
          <div className="rs-dim" style={{ fontSize: 13, marginTop: 6 }}>Copies the settings and screenshots. Run history and outputs stay behind.</div>
          {dupError && <div className="rs-danger-text" style={{ fontSize: 13, marginTop: 4, display: 'flex', gap: 6, alignItems: 'center' }}><i className="ph-duotone ph-warning-circle" />{dupError}</div>}
        </div>
      </Modal>
    </div>
  );
}
