// Step 5 shot grid + the shot detail drawer (design: RUN TAB shots, Dr drawer).
// ShotGrid props: { shots, onOpen(number) }. ShotDrawer props: { name, shot, onClose }.
import { useEffect } from 'react';
import { useAsync } from '../hooks.js';
import { getReviews } from '../api.js';
import { MUTED } from './Stepper.jsx';
import { Media, Motion } from './review/Media.jsx';

const SHOT_STATE = {
  queued: ['Queued', MUTED], making: ['Making', 'var(--color-accent-700)'], reviewing: ['Being reviewed', 'var(--color-accent-700)'],
  redo: ['Redo requested', 'var(--color-accent-2-700)'], approved: ['Approved', 'var(--color-text)'], failed: ['Failed', 'var(--color-accent-2-700)'],
};
const kicker = { fontSize: 12, letterSpacing: '.08em', textTransform: 'uppercase', color: MUTED };
const isVideoUrl = (u) => /\.(mp4|mov|webm)(\?|$)/i.test(u || '');

export default function ShotGrid({ shots = [], onOpen }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(112px,1fr))', gap: '18px 15px', paddingTop: 10 }}>
      {shots.map((s) => {
        const [label, color] = SHOT_STATE[s.state] || SHOT_STATE.queued;
        return (
          <div key={s.number} style={{ display: 'flex', flexDirection: 'column', gap: 6, opacity: s.state === 'queued' ? 0.6 : 1 }}>
            <div onClick={() => onOpen(s.number)} style={{ position: 'relative', aspectRatio: '9/16', borderRadius: 'var(--radius-md)', overflow: 'hidden', background: 'var(--color-surface)', cursor: 'pointer' }}>
              {s.preview_url && s.state !== 'making' && (
                isVideoUrl(s.preview_url)
                  ? <Media url={s.preview_url} radius={0} style={{ width: '100%', height: '100%' }} />
                  : <img src={s.preview_url} alt={'Shot ' + s.number} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
              )}
              {s.state === 'making' && (
                <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, fontSize: 12 }}>
                  <i className="ph-duotone ph-circle-notch" style={{ fontSize: 26, color: 'var(--color-accent)', animation: 'rs-spin 1s linear infinite' }} />Making
                </div>
              )}
              {s.state === 'queued' && !s.attempt && (
                <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', fontSize: 12, color: 'color-mix(in srgb,var(--color-text) 55%,transparent)' }}>Queued</div>
              )}
              <span style={{ position: 'absolute', top: 6, left: 6, padding: '1px 7px', borderRadius: 9, fontSize: 11, fontWeight: 600, background: 'var(--color-bg)', pointerEvents: 'none' }}>{String(s.number).padStart(2, '0')}</span>
              {s.type === 'video' && (
                <span style={{ position: 'absolute', top: 6, right: 6, width: 22, height: 22, borderRadius: '50%', display: 'grid', placeItems: 'center', background: 'var(--color-bg)', pointerEvents: 'none' }}>
                  <i className="ph-duotone ph-film-strip" style={{ fontSize: 13 }} />
                </span>
              )}
            </div>
            <button onClick={() => onOpen(s.number)} style={{ display: 'flex', flexDirection: 'column', gap: 1, border: 0, background: 'none', padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer', textAlign: 'left' }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>Shot {s.number} · {s.type === 'video' ? 'Video clip' : 'Still'}</span>
              <span style={{ fontSize: 12, color, fontWeight: s.state === 'approved' ? 600 : 400 }}>{label}</span>
              <span style={{ fontSize: 12, color: 'color-mix(in srgb,var(--color-text) 58%,transparent)' }}>{s.attempt ? 'attempt ' + s.attempt + ' of ' + s.max_attempts : 'not started'}</span>
            </button>
          </div>
        );
      })}
    </div>
  );
}

function verdictOf(v) {
  const x = String(v || '').toLowerCase();
  if (x.startsWith('approv')) return ['Approved', { background: 'var(--color-text)', color: 'var(--color-bg)' }];
  if (x) return ['Redo requested', { background: 'var(--color-accent-2-100)', color: 'var(--color-accent-2-800)' }];
  return ['In progress', { background: 'var(--color-accent-100)', color: 'var(--color-accent-800)' }];
}

export function ShotDrawer({ name, shot, onClose }) {
  const { data } = useAsync(() => getReviews(name), [name, shot && shot.number, shot && shot.attempt, shot && shot.state]);
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  if (!shot) return null;
  const rev = ((data && data.shots) || []).find((x) => x.number === shot.number);
  const attempts = (rev ? rev.attempts : []).map((a) => ({ ...a, label: verdictOf(a.verdict) }));
  if (shot.attempt > attempts.length && shot.state !== 'queued' && (shot.state === 'making' || shot.state === 'reviewing')) {
    attempts.push({
      attempt: shot.attempt, label: verdictOf(''), media_url: shot.state === 'reviewing' ? shot.preview_url : null,
      comments: [shot.state === 'making' ? (shot.type === 'video' ? 'Waiting for Veo.' : 'Generating the picture.') : 'The visual reviewer is checking it.'],
    });
    attempts[attempts.length - 1].label = [shot.state === 'making' ? 'Making…' : 'Being reviewed', verdictOf('')[1]];
  }
  const [stateLabel] = SHOT_STATE[shot.state] || SHOT_STATE.queued;
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 40, background: 'color-mix(in srgb,var(--color-neutral-900) 28%,transparent)' }} />
      <aside style={{ position: 'fixed', top: 0, right: 0, bottom: 0, zIndex: 41, width: 'min(500px,94vw)', overflow: 'auto', background: 'var(--color-bg)', color: 'var(--color-text)', boxShadow: 'var(--shadow-lg)', padding: '30px 32px 50px', display: 'flex', flexDirection: 'column', gap: 28 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 15 }}>
          <div style={{ flex: 1 }}>
            <div style={kicker}>Shot {shot.number} · {shot.type === 'video' ? 'Video clip' : 'Still picture'}</div>
            <h2 style={{ margin: '4px 0 0' }}>Shot {shot.number}</h2>
            <div style={{ fontSize: 14, marginTop: 4 }}>{stateLabel}{shot.attempt ? ' · attempt ' + shot.attempt + ' of ' + shot.max_attempts : ''}</div>
          </div>
          <button className="btn btn-icon" onClick={onClose} aria-label="Close"><i className="ph-duotone ph-x" style={{ fontSize: 18 }} /></button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={kicker}>Prompt used</div>
          <p style={{ margin: 0, fontSize: 16, lineHeight: 1.55 }}>{shot.prompt || 'Not planned yet.'}</p>
        </div>
        {attempts.length === 0 && <div className="rs-muted" style={{ fontSize: 14 }}>No attempts yet.</div>}
        {attempts.map((a) => (
          <div key={a.attempt} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <span style={{ fontWeight: 600 }}>Attempt {a.attempt}</span>
              <span className="tag" style={a.label[1]}>{a.label[0]}</span>
            </div>
            {a.media_url && (
              isVideoUrl(a.media_url)
                ? <Media url={a.media_url} width={200} controls />
                : <img src={a.media_url} alt={'Attempt ' + a.attempt} style={{ width: 200, aspectRatio: '9/16', objectFit: 'cover', borderRadius: 'var(--radius-md)' }} />
            )}
            {(a.comments || []).map((c, i) => <div key={i} style={{ fontSize: 14, lineHeight: 1.5 }}>{c}</div>)}
            <Motion value={a.camera_motion} />
          </div>
        ))}
      </aside>
    </>
  );
}

