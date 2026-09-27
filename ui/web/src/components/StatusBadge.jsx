// Status tag for a project or run. Props: { status, percent? }
// status: draft | running | paused (waiting for you) | stopped | finished (RunStatus "idle" shows as Draft).
const STYLES = {
  draft: { background: 'var(--color-neutral-200)', color: 'var(--color-neutral-800)' },
  running: { background: 'var(--color-accent-100)', color: 'var(--color-accent-800)' },
  paused: { background: 'color-mix(in srgb,var(--color-process-yellow) 35%,var(--color-bg))', color: 'var(--color-text)' },
  stopped: { background: 'var(--color-accent-2-100)', color: 'var(--color-accent-2-800)' },
  finished: { background: 'var(--color-text)', color: 'var(--color-bg)' },
};

export function normStatus(status) {
  if (status === 'waiting' || status === 'paused_for_narration') return 'paused';
  return STYLES[status] ? status : 'draft';
}

export function statusLabel(status, percent) {
  const st = normStatus(status);
  if (st === 'running') return 'Running · ' + Math.floor(percent || 0) + '%';
  return { draft: 'Draft', paused: 'Waiting for you', stopped: 'Stopped', finished: 'Finished' }[st];
}

export default function StatusBadge({ status, percent }) {
  const st = normStatus(status);
  return <span className="tag" style={{ ...STYLES[st], gap: 5 }}>{statusLabel(st, percent)}</span>;
}
