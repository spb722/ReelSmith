// Small shared pieces for the Reviews, Outputs and Cover tabs.

export const isVideo = (url) => /\.(mp4|mov|webm|m4v)(\?|#|$)/i.test(url || '');

export const muted = { color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' };
export const kicker = { fontSize: 12, letterSpacing: '.08em', textTransform: 'uppercase', color: 'color-mix(in srgb,var(--color-text) 62%,transparent)' };

/** An image or video filling a fixed-ratio frame (default 9:16). `controls` shows the video player. */
export function Media({ url, label, width, ratio = '9/16', controls = false, radius = 'var(--radius-md)', style }) {
  const frame = { width, flex: 'none', aspectRatio: ratio, borderRadius: radius, overflow: 'hidden', background: 'var(--color-surface)', ...style };
  const fill = { width: '100%', height: '100%', objectFit: 'cover', display: 'block' };
  if (!url) {
    return <div style={{ ...frame, display: 'grid', placeItems: 'center', fontSize: 12, ...muted }}>{label}</div>;
  }
  return (
    <div style={frame}>
      {isVideo(url)
        ? <video src={controls ? url : url + '#t=0.1'} style={fill} controls={controls} muted={!controls} preload="metadata" playsInline />
        : <img src={url} alt={label || ''} style={fill} loading="lazy" />}
    </div>
  );
}

/** Reviewer verdict → { label, style } for a `.tag`. */
export function verdictTag(v) {
  const s = String(v || '').toLowerCase();
  if (s.startsWith('appr') || s === 'pass' || s === 'ok') return { kind: 'approved', label: 'Approved', style: { background: 'var(--color-text)', color: 'var(--color-bg)' } };
  if (s.startsWith('rej') || s.startsWith('redo') || s.startsWith('revis') || s === 'fail') return { kind: 'rejected', label: 'Rejected', style: { background: 'var(--color-accent-2-100)', color: 'var(--color-accent-2-800)' } };
  return { kind: 'other', label: 'In progress', style: { background: 'var(--color-accent-100)', color: 'var(--color-accent-800)' } };
}

// camera_motion may be a plain sentence or an object of measurements.
export function motionText(m) {
  if (!m) return '';
  if (typeof m === 'string') return m;
  if (typeof m !== 'object') return String(m);
  if (m.summary || m.note || m.verdict) return String(m.summary || m.note || m.verdict);
  return Object.entries(m).filter(([, v]) => v !== null && typeof v !== 'object').map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`).join(' · ');
}

export function Motion({ value }) {
  const t = motionText(value);
  if (!t) return null;
  return <div style={{ fontSize: 13, display: 'flex', gap: 6, color: 'color-mix(in srgb,var(--color-text) 65%,transparent)' }}><i className="ph-duotone ph-video-camera" style={{ marginTop: 2 }} />{t}</div>;
}

export function Empty({ icon, title, children, maxWidth = 520 }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth }}>
      <i className={`ph-duotone ${icon}`} style={{ fontSize: 40, color: 'var(--color-neutral-500)' }} />
      <h3 style={{ margin: 0 }}>{title}</h3>
      <p style={{ margin: 0, fontSize: 15 }}>{children}</p>
    </div>
  );
}

export function LoadState({ loading, error }) {
  if (error) return <p className="rs-danger-text" style={{ margin: 0 }}>{error.message}</p>;
  if (loading) return <p className="rs-muted" style={{ margin: 0 }}>Loading…</p>;
  return null;
}
