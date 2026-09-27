import { useRef, useState } from 'react';
import Modal from './Modal.jsx';

// Drag-drop area + reorderable thumbnail strip (the order is the reading order).
// Props:
//   files: [{ file, url }]        (Screenshot shape; `file` is the name/key)
//   onAdd(File[])                 only PNG/JPG files are passed on; others get an inline message here
//   onRemove(file)                file name
//   onReorder(order[])            file names in the new order
//   readOnlyWarning?              true or a string: shows the "next run starts over" warning
//   message?                      extra inline message from the parent (e.g. server rejections)
const OK_TYPE = /^image\/(png|jpeg)$/;
const OK_EXT = /\.(png|jpe?g)$/i;

export default function ScreenshotDrop({ files = [], onAdd, onRemove, onReorder, readOnlyWarning, message }) {
  const [active, setActive] = useState(false);
  const [localMsg, setLocalMsg] = useState(null);
  const [view, setView] = useState(null);
  const dragIdx = useRef(null);
  const fileRef = useRef(null);

  const add = (list) => {
    const arr = Array.from(list || []);
    const good = arr.filter((f) => OK_TYPE.test(f.type) || OK_EXT.test(f.name));
    const bad = arr.filter((f) => !good.includes(f));
    setLocalMsg(bad.length ? bad.map((f) => `“${f.name}” isn’t a PNG or JPG.`).join(' ') : null);
    if (good.length) onAdd(good);
  };

  const moveTo = (i) => {
    const from = dragIdx.current;
    dragIdx.current = null;
    if (from == null || from === i) return;
    const order = files.map((f) => f.file);
    const [m] = order.splice(from, 1);
    order.splice(i, 0, m);
    onReorder(order);
  };

  const n = files.length;
  const countLabel = n === 0 ? 'No screenshots yet' : n + (n === 1 ? ' screenshot' : ' screenshots');
  const countWarn = n > 0 && n < 3 ? 'Most reels need at least 3 screenshots.' : n > 15 ? 'More than 15 screenshots makes the reel run long.' : '';
  const msg = [localMsg, message].filter(Boolean).join(' ');
  const errLine = { fontSize: 13, color: 'var(--color-accent-2-700)', display: 'flex', gap: 5, alignItems: 'center' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {readOnlyWarning && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '12px 18px', borderRadius: 'var(--radius-md)', background: 'var(--color-accent-2-100)', color: 'var(--color-accent-2-900)', fontSize: 14 }}>
          <i className="ph-duotone ph-warning" style={{ fontSize: 20 }} />
          {typeof readOnlyWarning === 'string' ? readOnlyWarning : 'Changing screenshots means the next run starts over.'}
        </div>
      )}
      <div
        onDragOver={(e) => { if (dragIdx.current != null) return; e.preventDefault(); if (!active) setActive(true); }}
        onDragLeave={() => setActive(false)}
        onDrop={(e) => { if (dragIdx.current != null) return; e.preventDefault(); setActive(false); add(e.dataTransfer.files); }}
        style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, minHeight: 230, padding: 30, textAlign: 'center', borderRadius: 'var(--radius-lg)', border: '1.5px dashed ' + (active ? 'var(--color-accent)' : 'var(--color-divider)'), background: active ? 'var(--color-accent-100)' : 'var(--color-surface)' }}
      >
        <i className="ph-duotone ph-images" style={{ fontSize: 40, color: 'var(--color-accent)' }} />
        <div style={{ fontSize: 20, fontWeight: 600 }}>Drop book screenshots here</div>
        <div className="rs-dim" style={{ fontSize: 14 }}>PNG or JPG · the order below is the reading order · 3–15 images</div>
        <button className="btn btn-secondary" onClick={() => fileRef.current && fileRef.current.click()} style={{ marginTop: 6 }}><i className="ph-duotone ph-folder-open" />Browse files</button>
        <input ref={fileRef} type="file" multiple accept="image/png,image/jpeg,.png,.jpg,.jpeg" onChange={(e) => { add(e.target.files); e.target.value = ''; }} style={{ display: 'none' }} />
      </div>
      <div style={{ display: 'flex', gap: 15, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>{countLabel}</span>
        {countWarn && <span style={errLine}><i className="ph-duotone ph-info" />{countWarn}</span>}
        {msg && <span style={errLine}><i className="ph-duotone ph-x-circle" />{msg}</span>}
        <span className="rs-spacer" />
        {n > 1 && <span className="rs-muted" style={{ fontSize: 13 }}>Drag to reorder</span>}
      </div>
      <div style={{ display: 'flex', gap: 18, overflowX: 'auto', paddingBottom: 12 }}>
        {files.map((s, i) => (
          <div
            key={s.file}
            draggable
            onDragStart={(e) => { dragIdx.current = i; e.dataTransfer.effectAllowed = 'move'; }}
            onDragOver={(e) => { if (dragIdx.current != null) e.preventDefault(); }}
            onDrop={(e) => { if (dragIdx.current == null) return; e.preventDefault(); e.stopPropagation(); moveTo(i); }}
            onDragEnd={() => { dragIdx.current = null; }}
            style={{ flex: 'none', width: 128, display: 'flex', flexDirection: 'column', gap: 6, cursor: 'grab' }}
          >
            <div style={{ position: 'relative', width: 128, aspectRatio: '9/16', borderRadius: 'var(--radius-md)', overflow: 'hidden', background: 'var(--color-surface)' }}>
              <div role="img" aria-label={s.file} style={{ width: '100%', height: '100%', backgroundSize: 'cover', backgroundPosition: 'top center', backgroundImage: `url("${s.url}")` }} />
              <span style={{ position: 'absolute', top: 6, left: 6, minWidth: 22, height: 22, padding: '0 6px', borderRadius: 11, display: 'grid', placeItems: 'center', fontSize: 12, fontWeight: 600, background: 'var(--color-bg)', color: 'var(--color-text)', pointerEvents: 'none' }}>{i + 1}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <i className="ph-duotone ph-dots-six-vertical" style={{ opacity: 0.5 }} />
              <span title={s.file} style={{ flex: 1, minWidth: 0, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.file}</span>
              <button className="btn btn-icon" style={{ width: 26, height: 26 }} onClick={() => setView({ ...s, n: i + 1 })} aria-label="View large"><i className="ph-duotone ph-arrows-out-simple" /></button>
              <button className="btn btn-icon" style={{ width: 26, height: 26 }} onClick={() => onRemove(s.file)} aria-label="Remove"><i className="ph-duotone ph-trash" /></button>
            </div>
          </div>
        ))}
      </div>
      <Modal
        open={!!view}
        plain
        width="min(560px,100%)"
        title={view ? view.n + ' · ' + view.file : ''}
        onClose={() => setView(null)}
        footer={<button className="btn btn-secondary" onClick={() => setView(null)}>Close</button>}
      >
        {view && <div role="img" aria-label={view.file} style={{ width: '100%', height: '70vh', backgroundSize: 'contain', backgroundRepeat: 'no-repeat', backgroundPosition: 'center', backgroundImage: `url("${view.url}")` }} />}
      </Modal>
    </div>
  );
}
