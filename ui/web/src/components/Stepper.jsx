// The 6-step vertical stepper on the Run tab (design: RUN TAB, R.steps).
// Props: { steps, open: {index: bool}, onToggle(index), pauseAfter, stopKind, extra(step) -> node }
// Also exports small formatting helpers used across the project page.

export const money = (n) => '$' + (Number(n) || 0).toFixed(2);
export const fmtDur = (t) => {
  t = Math.round(Number(t) || 0);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return h ? h + 'h ' + m + 'm' : m ? m + 'm ' + String(s).padStart(2, '0') + 's' : s + 's';
};
export const MUTED = 'color-mix(in srgb,var(--color-text) 62%,transparent)';

function meta(s, stopKind) {
  const m = {
    waiting: ['Waiting', 'ph-circle', 'transparent', 'var(--color-neutral-500)', '1.5px solid var(--color-neutral-300)', MUTED],
    running: ['Running', 'ph-circle-notch', 'var(--color-accent-100)', 'var(--color-accent)', 'none', 'var(--color-accent-700)'],
    retrying: ['Retrying · attempt ' + s.attempt + ' of ' + s.max_attempts, 'ph-arrows-clockwise', 'var(--color-accent-100)', 'var(--color-accent)', 'none', 'var(--color-accent-700)'],
    done: ['Done', 'ph-check', 'var(--color-text)', 'var(--color-bg)', 'none', MUTED],
    failed: stopKind === 'operator'
      ? ['Stopped by you', 'ph-stop', 'var(--color-neutral-200)', 'var(--color-text)', 'none', MUTED]
      : ['Stopped here', 'ph-x', 'var(--color-accent-2-100)', 'var(--color-accent-2-600)', 'none', 'var(--color-accent-2-700)'],
    waiting_for_you: ['Waiting for you', 'ph-pause', 'var(--color-process-yellow)', '#201e1d', 'none', 'var(--color-text)'],
  };
  return m[s.state] || m.waiting;
}

export default function Stepper({ steps = [], open = {}, onToggle, pauseAfter, stopKind, extra }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {steps.map((s, i) => {
        const [label, icon, bg, fg, border, lc] = meta(s, stopKind);
        const isOpen = !!open[s.index];
        const details = [
          ['Attempts', s.attempt ? s.attempt + ' of ' + s.max_attempts : '—'],
          ['Time', s.duration_s ? fmtDur(s.duration_s) : '—'],
          ['Cost', s.cost_usd ? money(s.cost_usd) : '—'],
        ];
        return (
          <div key={s.index} style={{ display: 'grid', gridTemplateColumns: '28px minmax(0,1fr)', gap: '0 18px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <span style={{ width: 28, height: 28, borderRadius: '50%', display: 'grid', placeItems: 'center', flex: 'none', background: bg, border, color: fg }}>
                <i className={'ph-duotone ' + icon} style={{ fontSize: 14, animation: s.state === 'running' ? 'rs-spin 1s linear infinite' : undefined }} />
              </span>
              <span style={{ flex: 1, width: 1, minHeight: 14, background: i === steps.length - 1 ? 'transparent' : 'var(--color-divider)' }} />
            </div>
            <div style={{ paddingBottom: 22, minWidth: 0 }}>
              <button onClick={() => onToggle && onToggle(s.index)} style={{ width: '100%', display: 'flex', alignItems: 'baseline', gap: '6px 15px', border: 0, background: 'none', padding: '3px 0 0', font: 'inherit', color: 'inherit', cursor: 'pointer', textAlign: 'left', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'color-mix(in srgb,var(--color-text) 55%,transparent)', fontVariantNumeric: 'tabular-nums' }}>{String(s.index).padStart(2, '0')}</span>
                <span style={{ fontSize: 18, fontWeight: 600 }}>{s.name}</span>
                <span style={{ fontSize: 13, color: lc, fontWeight: s.state === 'waiting_for_you' ? 600 : 400 }}>
                  {label}{s.state === 'done' && s.index === 2 && s.attempt > 1 ? ' · ' + s.attempt + ' attempts' : ''}
                </span>
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 13, fontVariantNumeric: 'tabular-nums', color: MUTED }}>{s.duration_s ? fmtDur(s.duration_s) : '—'}</span>
                <span style={{ fontSize: 13, fontVariantNumeric: 'tabular-nums', minWidth: 52, textAlign: 'right' }}>{s.cost_usd ? money(s.cost_usd) : ''}</span>
                <i className={'ph-duotone ' + (isOpen ? 'ph-caret-up' : 'ph-caret-down')} style={{ fontSize: 14, opacity: 0.6 }} />
              </button>
              {isOpen && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 12, fontSize: 14 }}>
                  {details.map(([k, v]) => (
                    <div key={k} style={{ display: 'grid', gridTemplateColumns: '110px minmax(0,1fr)', gap: 15 }}>
                      <span style={{ color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' }}>{k}</span><span>{v}</span>
                    </div>
                  ))}
                  {extra && extra(s)}
                </div>
              )}
              {s.index === 2 && pauseAfter && s.state !== 'done' && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginTop: 14 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 2, background: 'var(--color-process-yellow)' }} />Pause here to review the narration
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
