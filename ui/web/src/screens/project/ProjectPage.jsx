// Project page: header, main action, tabs (design: PROJECT HEAD; spec 5).
// Props: { name, tab } from the route #/p/:name/:tab (tab null = pick default; 'narration' = NarrationEditor).
// Every tab gets { name, project, onChange } — project is GET /api/projects/{name}, onChange reloads it.
import { useState } from 'react';
import OverviewTab from './OverviewTab.jsx';
import ScreenshotsTab from './ScreenshotsTab.jsx';
import SettingsTab from './SettingsTab.jsx';
import RunTab, { StopDialog, StartOverDialog } from './RunTab.jsx';
import ReviewsTab from './ReviewsTab.jsx';
import OutputsTab from './OutputsTab.jsx';
import CoverTab from './CoverTab.jsx';
import CaptionTab from './CaptionTab.jsx';
import NarrationEditor from './NarrationEditor.jsx';
import StatusBadge from '../../components/StatusBadge.jsx';
import { useToast } from '../../components/Toast.jsx';
import { usePoll, useAsync } from '../../hooks.js';
import { getProject, getMeta, startRun, getStatus } from '../../api.js';
import { navigate } from '../../router.js';
import { money, MUTED } from '../../components/Stepper.jsx';

const TABS = [
  ['overview', 'Overview', OverviewTab], ['screenshots', 'Screenshots', ScreenshotsTab], ['settings', 'Settings', SettingsTab],
  ['run', 'Run', RunTab], ['reviews', 'Reviews', ReviewsTab], ['outputs', 'Outputs', OutputsTab], ['cover', 'Cover', CoverTab],
  ['caption', 'Caption', CaptionTab],
];
const LIVE = ['running', 'paused', 'stopped'];

export default function ProjectPage({ name, tab }) {
  const toast = useToast();
  const [ms, setMs] = useState(3000);
  const { data: p, error, reload } = usePoll(async () => {
    const d = await getProject(name);
    setMs(d.status === 'running' || d.status === 'paused' ? 2000 : 5000);
    return d;
  }, ms, [name]);
  const { data: meta } = useAsync(getMeta, []);
  const [dialog, setDialog] = useState(null); // 'stop' | 'startOver'

  if (error && !p) {
    return (
      <div className="rs-stack">
        <div><button className="btn btn-ghost" onClick={() => navigate('/')} style={{ fontSize: 13 }}><i className="ph-duotone ph-arrow-left" />Projects</button></div>
        <div className="rs-danger-text">{error.message}</div>
      </div>
    );
  }
  if (!p) return <div className="rs-muted">Loading…</div>;

  const st = p.status;
  const go = (t) => navigate(`/p/${encodeURIComponent(name)}/${t}`);
  const key = tab === 'narration' ? 'narration' : TABS.some(([k]) => k === tab) ? tab : LIVE.includes(st) ? 'run' : 'overview';
  const curTab = key === 'narration' ? 'reviews' : key;

  const run = async (opts) => {
    try { await startRun(name, opts); reload(); go('run'); } catch (e) { toast(e.message, 'error'); }
  };
  const btn = (label, icon, onClick, primary, disabled) => ({ label, icon, onClick, primary, disabled });
  const actions = {
    // On the Run tab its own Run button (with the Pause toggle) is the one to use.
    draft: key === 'run' ? [] : [btn('Run', 'ph-play', () => run({ mode: 'resume', pause_after_narration: !!(p.settings && p.settings.pause_after_narration) }), true)],
    running: [btn('Stop', 'ph-stop', () => setDialog('stop')), btn('Running', 'ph-circle-notch', null, true, true)],
    // Paused: the pipeline has already exited, so there is nothing to stop.
    paused: [btn('Review narration', 'ph-note-pencil', () => go('run'), true)],
    stopped: [btn('Start over', 'ph-arrow-counter-clockwise', () => setDialog('startOver')), btn('Resume', 'ph-play', async () => {
      try { run({ mode: 'resume', pause_after_narration: (await getStatus(name)).pause_after_narration }); } catch (e) { toast(e.message, 'error'); }
    }, true)],
    finished: [btn('Make cover', 'ph-image-square', () => go('cover')), btn('Run again', 'ph-arrow-clockwise', () => setDialog('startOver'), true)],
  }[st] || [];

  const styleName = ((meta && meta.styles) || []).find((x) => x.id === p.style);
  const pctLabel = st === 'running' ? Math.floor(p.percent || 0) + '% complete' : st === 'paused' ? 'Holding at ' + Math.floor(p.percent || 0) + '%' : '';
  const moneyLabel = p.last_run_at || st !== 'draft' ? money(p.spent_usd) + ' of ' + money(p.budget_usd) : '$0.00 spent · budget ' + money(p.budget_usd);
  const metaLabel = [styleName ? styleName.name : p.style, p.character || 'No character', (p.screenshot_count || 0) + ' screenshots'].filter(Boolean).join(' · ');
  const dotColor = st === 'paused' ? 'var(--color-process-yellow)' : st === 'stopped' ? 'var(--color-accent-2)' : 'var(--color-accent)';
  const onChange = () => reload();

  let body;
  if (key === 'narration') {
    body = <NarrationEditor name={name} waiting={st === 'paused'} fromReviews onDone={() => { reload(); go('run'); }} />;
  } else {
    const Tab = TABS.find(([k]) => k === key)[2];
    body = <Tab key={key} name={name} project={p} onChange={onChange} />;
  }

  return (
    <>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
        <div><button className="btn btn-ghost" onClick={() => navigate('/')} style={{ fontSize: 13 }}><i className="ph-duotone ph-arrow-left" />Projects</button></div>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 30, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 15, flexWrap: 'wrap' }}>
              <h1 style={{ margin: 0, fontSize: 40, overflowWrap: 'anywhere' }}>{p.name}</h1>
              <StatusBadge status={st} percent={p.percent} />
            </div>
            <div style={{ display: 'flex', gap: 25, fontSize: 14, flexWrap: 'wrap', color: 'color-mix(in srgb,var(--color-text) 65%,transparent)' }}>
              {pctLabel && <span style={{ color: 'var(--color-text)', fontWeight: 600 }}>{pctLabel}</span>}
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>{moneyLabel}</span>
              <span>{metaLabel}</span>
            </div>
          </div>
          <span style={{ flex: 1 }} />
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            {actions.map((a) => (
              <button key={a.label} className={'btn ' + (a.primary ? 'btn-primary' : 'btn-secondary')} onClick={a.onClick || undefined} disabled={a.disabled}>
                <i className={'ph-duotone ' + a.icon} style={a.icon === 'ph-circle-notch' ? { animation: 'rs-spin 1s linear infinite' } : undefined} />{a.label}
              </button>
            ))}
          </div>
        </div>
        <div role="tablist" style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
          {TABS.map(([k, label]) => {
            const on = curTab === k;
            return (
              <button key={k} role="tab" aria-selected={on} onClick={() => go(k)}
                style={{ border: 0, background: 'none', padding: '4px 0 8px', font: 'inherit', fontSize: 16, cursor: 'pointer', color: on ? 'var(--color-text)' : MUTED, fontWeight: on ? 600 : 400, boxShadow: on ? 'inset 0 -2px 0 var(--color-accent)' : 'none', opacity: k === 'cover' && st !== 'finished' && !p.has_cover ? 0.55 : 1 }}>
                {label}
                {k === 'run' && LIVE.includes(st) && <span style={{ width: 6, height: 6, borderRadius: '50%', background: dotColor, marginLeft: 6, display: 'inline-block', verticalAlign: 'middle' }} />}
              </button>
            );
          })}
        </div>
      </div>
      {body}
      <StopDialog name={name} open={dialog === 'stop'} onClose={() => setDialog(null)} onDone={reload} />
      <StartOverDialog name={name} finished={st === 'finished'} open={dialog === 'startOver'} onClose={() => setDialog(null)} onDone={() => { reload(); go('run'); }} />
    </>
  );
}
