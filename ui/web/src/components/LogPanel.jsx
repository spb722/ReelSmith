// Collapsible, auto-scrolling live log (design: RUN TAB "Live log").
// Props: { name, live } — polls getLog by byte offset every 1.5s while `live`.
import { useEffect, useRef, useState } from 'react';
import { getLog } from '../api.js';
import Modal from './Modal.jsx';

export default function LogPanel({ name, live }) {
  const [text, setText] = useState('');
  const [open, setOpen] = useState(true);
  const [copied, setCopied] = useState(false);
  const [full, setFull] = useState(false);
  const pos = useRef({ offset: 0, file: null });
  const boxRef = useRef(null);

  useEffect(() => {
    let stop = false;
    pos.current = { offset: 0, file: null };
    setText('');
    const tick = async () => {
      try {
        const r = await getLog(name, pos.current.offset);
        if (stop || !r) return;
        // A new run log (different file, or a shorter one) starts the view over.
        const reset = (pos.current.file && r.name !== pos.current.file) || r.offset < pos.current.offset;
        if (reset) {
          pos.current = { offset: 0, file: r.name };
          const again = await getLog(name, 0);
          if (stop) return;
          pos.current.offset = again.offset;
          setText(again.text || '');
          return;
        }
        pos.current = { offset: r.offset, file: r.name };
        if (r.text) setText((t) => t + r.text);
      } catch { /* no log yet */ }
    };
    tick();
    const t = live ? setInterval(tick, 1500) : null;
    return () => { stop = true; if (t) clearInterval(t); };
  }, [name, live]);

  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [text, open]);

  const lines = text.split('\n').filter((l) => l !== '');
  const copy = () => {
    try { navigator.clipboard.writeText(text); } catch { /* clipboard blocked */ }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <button className="btn btn-ghost" onClick={() => setOpen(!open)} style={{ fontSize: 15, color: 'var(--color-text)' }}>
          <i className={'ph-duotone ' + (open ? 'ph-caret-down' : 'ph-caret-right')} />Live log
        </button>
        <span style={{ fontSize: 12, color: 'color-mix(in srgb,var(--color-text) 58%,transparent)' }}>{lines.length} lines{live ? ' · auto-scrolling' : ''}</span>
        <span style={{ flex: 1 }} />
        <button className="btn btn-secondary" onClick={copy} style={{ fontSize: 13, padding: '6px 12px' }}><i className="ph-duotone ph-copy" />{copied ? 'Copied' : 'Copy'}</button>
        <button className="btn btn-secondary" onClick={() => setFull(true)} style={{ fontSize: 13, padding: '6px 12px' }}><i className="ph-duotone ph-arrow-square-out" />Open full log</button>
      </div>
      {open && (
        <div ref={boxRef} style={{ maxHeight: 240, overflow: 'auto', background: 'var(--color-surface)', padding: '14px 18px', borderRadius: 'var(--radius-md)', fontFamily: 'ui-monospace,Menlo,Consolas,monospace', fontSize: 12, lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>
          {lines.length === 0 && <div className="rs-muted">No log yet.</div>}
          {lines.slice(-300).map((l, i) => <div key={i}>{l}</div>)}
        </div>
      )}
      <Modal open={full} title={'Full log · ' + name} onClose={() => setFull(false)}
        footer={<button className="btn btn-secondary" onClick={() => setFull(false)}>Close</button>}>
        <pre style={{ margin: 0, maxHeight: '60vh', width: 'min(840px,80vw)', overflow: 'auto', padding: '15px 18px', background: 'var(--color-bg)', borderRadius: 'var(--radius-md)', fontFamily: 'ui-monospace,Menlo,Consolas,monospace', fontSize: 12, lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>{text || 'No log yet.'}</pre>
      </Modal>
    </div>
  );
}
