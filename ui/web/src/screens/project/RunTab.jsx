// Live run screen (design: RUN TAB; spec 5.4).
// Props: { name, project (ProjectSummary & settings), onChange() — refresh the page header }
import { useEffect, useState } from 'react';
import { usePoll } from '../../hooks.js';
import { getStatus, startRun, stopRun, setPauseAfterNarration, getSettings, putSettings } from '../../api.js';
import { useToast } from '../../components/Toast.jsx';
import Modal from '../../components/Modal.jsx';
import Stepper, { money, fmtDur, MUTED } from '../../components/Stepper.jsx';
import ShotGrid, { ShotDrawer } from '../../components/ShotGrid.jsx';
import LogPanel from '../../components/LogPanel.jsx';
import NarrationEditor from './NarrationEditor.jsx';

const STOP_HINT = {
  attempts: 'Raise the attempt limit, then resume. Finished shots are kept.',
  budget: 'Raise the budget ceiling, then resume. Finished shots are kept.',
  vendor: 'A service didn’t answer or refused the request. Check Setup, then resume. Finished work is kept.',
  operator: 'Resume when you’re ready. Finished work is kept.',
};
const STOP_REASON = {
  attempts: 'A step used all of its attempts.', budget: 'The next step would go over the budget.',
  vendor: 'A service stopped the run.', operator: 'You stopped the run.',
};
const STEP_AGENT = { 1: 'asset_analyst', 2: 'story_agent', 3: 'visual_agent', 4: 'voice_agent' };
const AGENT_LABEL = {
  asset_analyst: 'Max attempts — reading the screenshots', story_agent: 'Max attempts — narration',
  visual_agent: 'Max attempts — shot planning', voice_agent: 'Max attempts — voice',
  veo_agent: 'Max attempts — each video clip', stills_agent: 'Max attempts — each still picture',
};

const pressed = (on) => ({
  border: '1px solid ' + (on ? 'var(--color-accent)' : 'var(--color-divider)'),
  background: on ? 'var(--color-accent-100)' : 'transparent',
  color: on ? 'var(--color-accent-800)' : 'var(--color-text)',
});

/** Which setting "Raise limit & resume" changes for this stop, or null. */
function raiseTarget(st) {
  if (st.stop_kind === 'budget') return { budget: true, label: 'Budget ceiling for the whole run (USD)', btn: 'Raise budget & resume' };
  if (st.stop_kind !== 'attempts') return null;
  const step = st.steps.find((s) => s.state === 'failed') || st.steps.find((s) => s.index === st.step);
  let key = step && STEP_AGENT[step.index];
  if (step && step.index === 5) {
    const shot = st.shots.find((s) => s.state === 'failed');
    key = shot && shot.type === 'video' ? 'veo_agent' : 'stills_agent';
  }
  return key ? { key, label: AGENT_LABEL[key], btn: 'Raise limit & resume' } : null;
}

/** Confirm dialog for Stop. Props: { name, open, onClose, onDone } */
export function StopDialog({ name, open, onClose, onDone }) {
  const toast = useToast();
  const go = async () => {
    try { await stopRun(name); onDone && onDone(); } catch (e) { toast(e.message, 'error'); }
    onClose();
  };
  return (
    <Modal open={open} title="Stop this run?" onClose={onClose}
      footer={<><button className="btn btn-secondary" onClick={onClose}>Keep running</button><button className="btn btn-primary" style={{ background: 'var(--color-accent-2-600)' }} onClick={go}>Stop run</button></>}>
      Finished work is kept. You can resume later and only the unfinished part runs again.
    </Modal>
  );
}

/** Confirm dialog for Start over / Run again. Props: { name, open, finished, onClose, onDone } */
export function StartOverDialog({ name, open, finished, onClose, onDone }) {
  const toast = useToast();
  const go = async () => {
    try {
      // A "Pause after narration" pressed on the Run tab carries over; otherwise the project setting applies.
      const cur = await getStatus(name);
      await startRun(name, { mode: 'start_over', pause_after_narration: cur.pause_after_narration ? true : undefined });
      onDone && onDone();
    } catch (e) { toast(e.message, 'error'); }
    onClose();
  };
  return (
    <Modal open={open} title="Start over?" onClose={onClose}
      footer={<><button className="btn btn-secondary" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={go}>Start over</button></>}>
      The current {finished ? 'reel, its cover, ' : ''}narration, pictures and clips move to projects/{name}/_archive/&lt;time&gt;/ and the next run begins again from step 1. They no longer show in Outputs, Reviews{finished ? ' or Cover' : ''}.
    </Modal>
  );
}

export default function RunTab({ name, project, onChange }) {
  const toast = useToast();
  const [fast, setFast] = useState(true);
  const { data: st, error, reload } = usePoll(() => getStatus(name), fast ? 1500 : 5000, [name]);
  const state = st && st.state;
  useEffect(() => { if (state) setFast(state === 'running'); }, [state]);
  // The header can start/stop runs too; re-check when the project's status changes.
  const projectStatus = project && project.status;
  useEffect(() => { reload(); }, [projectStatus]); // eslint-disable-line react-hooks/exhaustive-deps

  const [pending, setPending] = useState(!!(project && project.settings && project.settings.pause_after_narration));
  const [open, setOpen] = useState({});
  const [drawer, setDrawer] = useState(null);
  const [stopAsk, setStopAsk] = useState(false);
  const [raise, setRaise] = useState(null); // { ...target, val }

  const refresh = () => { reload(); onChange && onChange(); };
  const act = async (fn, okMsg) => {
    try { await fn(); if (okMsg) toast(okMsg); refresh(); } catch (e) { toast(e.message, 'error'); }
  };

  if (error && !st) return <div className="rs-danger-text">{error.message}</div>;
  if (!st) return <div className="rs-muted">Loading…</div>;

  // — no run yet —
  if (state === 'idle') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 15, maxWidth: 560, padding: '20px 0' }}>
        <i className="ph-duotone ph-play-circle" style={{ fontSize: 44, color: 'var(--color-accent)' }} />
        <h2 style={{ margin: 0 }}>No runs yet</h2>
        <p style={{ fontSize: 16, margin: 0 }}>A run takes about 20 minutes and usually costs $9–$16. By default it goes straight through all 6 steps. Turn on <em>Pause after narration</em> to read and edit the script before any pictures are made.</p>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button className="btn" aria-pressed={pending} onClick={() => setPending(!pending)} style={pressed(pending)}><i className="ph-duotone ph-pause-circle" />Pause after narration</button>
          <button className="btn btn-primary" onClick={() => act(() => startRun(name, { mode: 'resume', pause_after_narration: pending }))}><i className="ph-duotone ph-play" />Run</button>
        </div>
      </div>
    );
  }

  const steps = st.steps || [];
  const shots = st.shots || [];
  const cur = steps.find((s) => s.index === st.step) || {};
  const pct = Math.floor(st.percent || 0);
  const pauseOn = !!st.pause_after_narration;
  const step2 = steps.find((s) => s.index === 2) || {};
  // The pipeline only pauses when step 2 writes the script in this run, so once it's done it's too late.
  const tooLate = state !== 'paused' && (step2.state === 'done' || steps.some((s) => s.index >= 3 && s.state !== 'waiting'));
  // On a finished project the toggle is a choice for the next run (Run again).
  const pauseDisabled = tooLate && state !== 'finished';
  const dark = document.documentElement.classList.contains('rs-dark');
  const target = state === 'stopped' ? raiseTarget(st) : null;
  const approvedShots = shots.filter((s) => s.state === 'approved').length;
  const last = (st.log_tail || [])[st.log_tail ? st.log_tail.length - 1 : 0] || '';
  const heartbeat = state === 'running' && /still waiting/i.test(last) ? last.replace(/^\[[^\]]*\]\s*/, '') : null;

  const stepLine = state === 'finished' ? 'All 6 steps done · book_reel.mp4 is ready'
    : state === 'paused' ? 'Paused after step 2 — waiting for you'
    : state === 'stopped' ? `Stopped at step ${st.step} — ${cur.name || ''}`
    : `Step ${st.step} of 6 — ${cur.name || ''}`;
  const barColor = state === 'paused' ? 'var(--color-process-yellow)' : state === 'stopped' ? 'var(--color-accent-2)' : state === 'finished' ? 'var(--color-text)' : 'var(--color-accent)';
  const budget = st.budget_usd || 0;

  const isOpen = (i) => (open[i] != null ? open[i] : i === 5 && (st.step === 5 || (state !== 'running' && shots.some((s) => s.attempt))));
  const openMap = Object.fromEntries(steps.map((s) => [s.index, isOpen(s.index)]));

  const togglePause = async () => {
    try { await setPauseAfterNarration(name, !pauseOn); reload(); }
    catch (e) { toast(e.status === 409 ? 'Too late for this run' : e.message, 'error'); reload(); }
  };

  const openRaise = async () => {
    try {
      const { settings } = await getSettings(name);
      const val = target.budget ? Math.ceil((settings.max_budget_usd || budget) + 10) : ((settings.max_attempts || {})[target.key] || 3) + 1;
      setRaise({ ...target, val });
    } catch (e) { toast(e.message, 'error'); }
  };
  const raiseResume = () => act(async () => {
    const { settings } = await getSettings(name);
    const next = raise.budget
      ? { ...settings, max_budget_usd: Number(raise.val) }
      : { ...settings, max_attempts: { ...settings.max_attempts, [raise.key]: Number(raise.val) } };
    await putSettings(name, next);
    await startRun(name, { mode: 'resume', pause_after_narration: pauseOn });
    setRaise(null);
  });

  const viewDetails = () => {
    const failed = steps.find((s) => s.state === 'failed') || cur;
    if (failed.index) setOpen((o) => ({ ...o, [failed.index]: true }));
    const shot = shots.find((s) => s.state === 'failed');
    if (st.stop_kind === 'attempts' && shot) setDrawer(shot.number);
  };

  const pctStyle = { fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 104, letterSpacing: '-0.03em' };
  const drawerShot = drawer != null ? shots.find((s) => s.number === drawer) : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 35 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'auto minmax(0,1fr)', gap: '20px 45px', alignItems: 'end' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {dark
            ? <div style={{ ...pctStyle, lineHeight: 0.9 }}>{pct}%</div>
            : <div className="cmyk-num" style={pctStyle}><span className="paper">{pct}%</span><span className="plate plate-c" aria-hidden="true">{pct}%</span><span className="plate plate-m" aria-hidden="true">{pct}%</span><span className="plate plate-y" aria-hidden="true">{pct}%</span></div>}
          <div style={{ fontSize: 13, color: MUTED }}>complete · an estimate that never goes backwards</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingBottom: 4 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 15, fontSize: 15, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600 }}>{stepLine}</span>
            <span style={{ fontVariantNumeric: 'tabular-nums' }}>{(state === 'finished' ? 'Took ' : 'Elapsed ') + fmtDur(st.elapsed_s)}</span>
          </div>
          <div style={{ height: 8, background: 'var(--color-neutral-200)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: (st.percent || 0) + '%', background: barColor, transition: 'width .2s linear' }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 15, fontSize: 13, flexWrap: 'wrap', fontVariantNumeric: 'tabular-nums' }}>
            <span>{money(st.spent_usd)} of {money(budget)} budget</span>
            <span style={{ color: MUTED }}>{st.waiting_s > 1 ? 'Waiting for you: ' + fmtDur(st.waiting_s) + ' (not counted as run time)' : ''}</span>
          </div>
          <div style={{ height: 3, background: 'var(--color-neutral-200)' }}>
            <div style={{ height: '100%', width: (budget ? Math.min(100, (st.spent_usd / budget) * 100) : 0) + '%', background: 'var(--color-accent-2)' }} />
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn" aria-pressed={pauseOn} onClick={togglePause} disabled={pauseDisabled} style={pressed(pauseOn)}
          title={pauseDisabled ? 'Too late for this run' : state === 'finished' ? 'Pause the next run after step 2 so you can read and edit the narration' : 'Stop after step 2 so you can read and edit the narration'}>
          <i className="ph-duotone ph-pause-circle" />Pause after narration
        </button>
        {state === 'running' && <button className="btn btn-secondary" onClick={() => setStopAsk(true)}><i className="ph-duotone ph-stop" />Stop</button>}
        {state === 'stopped' && <button className="btn btn-primary" onClick={() => act(() => startRun(name, { mode: 'resume', pause_after_narration: pauseOn }))}><i className="ph-duotone ph-play" />Resume</button>}
        {state === 'finished' && <button className="btn btn-secondary" onClick={() => act(() => startRun(name, { mode: 'resume', retry_voice: true }), 'Redoing the voice.')}><i className="ph-duotone ph-microphone" />Redo voice only</button>}
        {pauseDisabled && state !== 'finished' && <span style={{ fontSize: 13, color: MUTED }}>Too late for this run</span>}
        <span style={{ flex: 1 }} />
        {heartbeat && <span style={{ fontSize: 14, fontStyle: 'italic', display: 'flex', gap: 6, alignItems: 'center' }}><i className="ph-duotone ph-hourglass-medium" style={{ color: 'var(--color-accent)' }} />{heartbeat}</span>}
      </div>

      {state === 'stopped' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 15, padding: '22px 25px', borderRadius: 'var(--radius-lg)', background: 'var(--color-accent-2-100)', color: 'var(--color-accent-2-900)' }}>
          <div style={{ display: 'flex', gap: 15, alignItems: 'flex-start' }}>
            <i className="ph-duotone ph-warning-octagon" style={{ fontSize: 28, color: 'var(--color-accent-2-600)' }} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <div style={{ fontSize: 20, fontWeight: 600, lineHeight: 1.25 }}>{st.stop_reason || STOP_REASON[st.stop_kind] || 'The run stopped.'}</div>
              <div style={{ fontSize: 15 }}>{STOP_HINT[st.stop_kind] || 'Resume to carry on. Finished work is kept.'}</div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', paddingLeft: 43 }}>
            <button className="btn btn-primary" onClick={() => act(() => startRun(name, { mode: 'resume', pause_after_narration: pauseOn }))}><i className="ph-duotone ph-play" />Resume</button>
            {target && <button className="btn btn-secondary" onClick={openRaise} style={{ borderColor: 'color-mix(in srgb,var(--color-accent-2-900) 30%,transparent)', color: 'inherit' }}>{target.btn}</button>}
            <button className="btn btn-ghost" onClick={viewDetails} style={{ color: 'inherit' }}>View details</button>
          </div>
          {raise && (
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', paddingLeft: 43 }}>
              <span style={{ fontSize: 14 }}>{raise.label}</span>
              <input className="input" type="number" min={1} value={raise.val} onChange={(e) => setRaise({ ...raise, val: e.target.value })} style={{ width: 100, background: 'var(--color-bg)' }} />
              <button className="btn btn-primary" disabled={!(Number(raise.val) > 0)} onClick={raiseResume}>Save &amp; resume</button>
              <button className="btn btn-ghost" onClick={() => setRaise(null)} style={{ color: 'inherit' }}>Cancel</button>
            </div>
          )}
        </div>
      )}

      {state === 'paused' && <NarrationEditor name={name} waiting onDone={refresh} />}

      <Stepper steps={steps} open={openMap} stopKind={st.stop_kind}
        pauseAfter={pauseOn && !tooLate}
        onToggle={(i) => setOpen((o) => ({ ...o, [i]: !isOpen(i) }))}
        extra={(s) => s.index === 5 && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: '110px minmax(0,1fr)', gap: 15 }}>
              <span style={{ color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' }}>Progress</span>
              <span>{approvedShots} of {shots.length} shots approved</span>
            </div>
            {shots.length > 0 && <ShotGrid shots={shots} onOpen={setDrawer} />}
          </>
        )} />

      <LogPanel name={name} live={state === 'running'} />

      {drawerShot && <ShotDrawer name={name} shot={drawerShot} onClose={() => setDrawer(null)} />}
      <StopDialog name={name} open={stopAsk} onClose={() => setStopAsk(false)} onDone={refresh} />
    </div>
  );
}
