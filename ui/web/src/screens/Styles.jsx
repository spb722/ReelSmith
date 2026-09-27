// Style library (spec section 7b, mockup <!-- STYLES -->).
import { useRef, useState } from 'react';
import * as api from '../api.js';
import { useAsync } from '../hooks.js';
import Modal from '../components/Modal.jsx';
import { useToast } from '../components/Toast.jsx';

const usedLabel = (arr) => (arr && arr.length ? arr.join(', ') : 'No projects');
const baseName = (f) => f.name.replace(/\.[^.]+$/, '');
const titleCase = (id) => id.split('_').filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(' ');

// Colour words from style JSONs that aren't CSS colour names.
const COLOURS = {
  cream: '#fff3d6', 'warm cream': '#f6e7c8', sage: '#9caf88', 'sage green': '#9caf88', mustard: '#e1ad01', coral: '#ff7f50',
  navy: '#23395b', 'deep navy': '#1b2a41', 'teal green': '#2a9d8f', 'sun yellow': '#ffd166', 'azure blue': '#3a86ff',
  peach: '#ffcba4', 'soft peach': '#ffd8be', 'warm beige': '#e8d8c3', 'burnt orange': '#cc5500', 'burnt sienna': '#e97451',
  'butter yellow': '#f8e38c', 'caramel brown': '#a8683a', 'cobalt blue': '#0047ab', 'electric pink': '#ff2e93',
  'federal blue': '#1d2a6b', 'fluorescent pink': '#ff3fa4', 'ink black': '#1e1e24', 'leaf green': '#5a9e4b', 'muted teal': '#5f9ea0',
  'off-white paper': '#f4efe6', 'pale rose': '#f4c2c2', 'powder blue': '#b0d4e8', 'sap green': '#507d2a', 'soft lavender': '#c8b6e2',
  'soft tan': '#d8bc98', 'sunflower yellow': '#ffc512', 'tomato red': '#e5412d', 'warm paper white': '#f8f1e5', 'deep indigo': '#2e2a6b',
  paper: '#f4efe6', beige: '#e8d8c3', tan: '#d2b48c', blue: '#3a86ff', green: '#2a9d8f', yellow: '#ffd166', red: '#d7263d', pink: '#f7a1c4',
};
const isCss = (w) => typeof CSS !== 'undefined' && CSS.supports && CSS.supports('color', w);
function colourFor(word) {
  const w = String(word).toLowerCase().trim();
  if (COLOURS[w]) return COLOURS[w];
  if (isCss(w.replace(/[\s-]+/g, ''))) return w.replace(/[\s-]+/g, '');
  const parts = w.split(/[\s-]+/).reverse(); // "one coral accent" -> accent, coral, one
  for (const p of parts) { if (COLOURS[p]) return COLOURS[p]; if (isCss(p)) return p; }
  return '#9b9797';
}

const bust = (url, v) => url + (url.includes('?') ? '&' : '?') + 'v=' + v;

export default function Styles() {
  const toast = useToast();
  const { data, error, loading, reload } = useAsync(() => api.listStyles(), []);
  const [search, setSearch] = useState('');
  const [over, setOver] = useState(false);
  const [msgs, setMsgs] = useState([]);
  const [avoidOpen, setAvoidOpen] = useState({});
  const [dialog, setDialog] = useState(null); // {kind:'sheet'} | {kind:'remove', style} | {kind:'conflict', queue:[{json,image,id}], msgs}
  const [imgV, setImgV] = useState(Date.now());
  const fileRef = useRef(null);
  const imgRef = useRef(null);
  const replaceFor = useRef(null);
  const styles = data || [];

  const refresh = () => { setImgV(Date.now()); reload(); };

  // Upload one JSON (+ image). Returns a message, or { conflict } on 409.
  const upload = async ({ json, image }, onConflict) => {
    const fd = new FormData();
    fd.append('json', json);
    if (image) fd.append('image', image);
    try {
      const s = await api.uploadStyle(fd, onConflict);
      const name = s.name || titleCase(s.id);
      const verb = onConflict === 'replace' ? 'Replaced ' : 'Added ';
      return { t: verb + name + (onConflict === 'replace' ? '.' : image ? ' with its sample image.' : '. Add a sample image any time.') };
    } catch (e) {
      if (e.status === 409 && e.body && e.body.conflict) return { conflict: true };
      return { err: true, t: json.name + ': ' + e.message };
    }
  };

  const handleFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    const out = [];
    const jsons = files.filter((f) => /\.json$/i.test(f.name));
    const imgs = files.filter((f) => /^image\/(png|jpeg)$/.test(f.type));
    files.filter((f) => !jsons.includes(f) && !imgs.includes(f))
      .forEach((f) => out.push({ err: true, t: '“' + f.name + '” is not a style JSON or a PNG/JPG image.' }));
    const conflicts = [];
    for (const json of jsons) {
      const image = imgs.find((f) => baseName(f) === baseName(json));
      const r = await upload({ json, image });
      if (r.conflict) conflicts.push({ json, image, id: baseName(json) });
      else out.push(r);
    }
    for (const f of imgs) {
      if (jsons.some((j) => baseName(j) === baseName(f))) continue;
      const s = styles.find((x) => x.id === baseName(f));
      if (!s) { out.push({ err: true, t: '“' + f.name + '” has no matching style. Name it like the JSON, e.g. pixel_art.png.' }); continue; }
      try { await api.setStyleImage(s.id, f); out.push({ t: 'Sample image added to ' + (s.name || titleCase(s.id)) + '.' }); }
      catch (e) { out.push({ err: true, t: f.name + ': ' + e.message }); }
    }
    setMsgs(out);
    if (conflicts.length) setDialog({ kind: 'conflict', queue: conflicts });
    refresh();
  };

  const resolveConflict = async (choice) => {
    const [first, ...rest] = dialog.queue;
    const r = choice ? await upload(first, choice) : null;
    if (r) setMsgs((m) => [...m, r.conflict ? { err: true, t: first.json.name + ': still conflicts.' } : r]);
    setDialog(rest.length ? { kind: 'conflict', queue: rest } : null);
    refresh();
  };

  const pickImage = async (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = '';
    const s = replaceFor.current;
    if (!f || !s) return;
    try { await api.setStyleImage(s.id, f); toast('Sample image saved for ' + (s.name || titleCase(s.id)) + '.'); refresh(); }
    catch (err) { toast(err.message, 'error'); }
  };

  const doRemove = async () => {
    const s = dialog.style;
    setDialog(null);
    try { await api.deleteStyle(s.id); toast('Removed ' + (s.name || titleCase(s.id)) + '.'); refresh(); }
    catch (e) { toast(e.message, 'error'); }
  };

  const q = search.trim().toLowerCase();
  const cards = styles
    .map((s) => ({ ...s, title: s.name || titleCase(s.id) }))
    .filter((s) => !q || s.title.toLowerCase().includes(q) || s.id.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q));

  const danger = { background: 'var(--color-accent-2-600)' };
  let modal = null;
  if (dialog && dialog.kind === 'sheet') {
    modal = { title: 'Modern Illustration Styles', width: 'min(1000px,100%)',
      footer: <button className="btn btn-secondary" onClick={() => setDialog(null)}>Close</button>,
      body: <>
        <div style={{ marginBottom: 12 }}>The reference sheet from styles/: 15 styles as labelled tiles. Ten have a JSON today.</div>
        <img src={api.styleReferenceSheetUrl} alt="Modern Illustration Styles reference sheet" style={{ width: '100%', maxHeight: '68vh', objectFit: 'contain', display: 'block' }} />
      </> };
  } else if (dialog && dialog.kind === 'remove') {
    const s = dialog.style;
    const used = s.used_by && s.used_by.length;
    modal = { title: 'Remove ' + (s.name || titleCase(s.id)) + '?',
      footer: <><button className="btn btn-secondary" onClick={() => setDialog(null)}>Cancel</button><button className="btn btn-primary" style={danger} onClick={doRemove}>Remove style</button></>,
      body: used ? <>
        <div>These projects use this style. Pick another style for them before their next run:</div>
        <ul style={{ margin: '8px 0 0', paddingLeft: 20, fontSize: 14 }}>{s.used_by.map((p) => <li key={p}>{p}</li>)}</ul>
      </> : 'No projects use this style. Its JSON and sample image are removed from styles/.' };
  } else if (dialog && dialog.kind === 'conflict') {
    const id = dialog.queue[0].id;
    modal = { title: 'A style called “' + id + '” already exists',
      footer: <>
        <button className="btn btn-secondary" onClick={() => resolveConflict(null)}>Cancel</button>
        <button className="btn btn-secondary" onClick={() => resolveConflict('keep_both')}>Keep both</button>
        <button className="btn btn-primary" onClick={() => resolveConflict('replace')}>Replace</button>
      </>,
      body: 'Replace the existing style, or keep both? Keeping both saves the new one under a new name, e.g. ' + id + '_2.' };
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 35 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 20, flexWrap: 'wrap' }}>
        <div><h1 style={{ margin: 0 }}>Styles</h1><p style={{ fontSize: 15, margin: '6px 0 0', maxWidth: 640 }}>Art styles are small JSON files in <code>styles/</code>, each with a sample image of the same name. Make them elsewhere and drop them in here.</p></div>
        <span style={{ flex: 1 }}></span>
        <button className="btn btn-secondary" onClick={() => setDialog({ kind: 'sheet' })}><i className="ph-duotone ph-squares-four"></i>View reference sheet</button>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); handleFiles(e.dataTransfer.files); }}
        style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap', padding: '20px 25px', borderRadius: 'var(--radius-lg)',
          border: '1.5px dashed ' + (over ? 'var(--color-accent)' : 'var(--color-divider)'), background: over ? 'var(--color-accent-100)' : 'var(--color-surface)' }}
      >
        <i className="ph-duotone ph-file-arrow-down" style={{ fontSize: 30, color: 'var(--color-accent)' }}></i>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div style={{ fontWeight: 600 }}>Add a style</div>
          <div className="rs-dim" style={{ fontSize: 13 }}>Drop the <code>.json</code> and its sample <code>.png</code> together, or the image later. The JSON is checked on drop.</div>
        </div>
        <button className="btn btn-secondary" onClick={() => fileRef.current && fileRef.current.click()}>Browse files</button>
        <input ref={fileRef} type="file" multiple accept=".json,image/png,image/jpeg" style={{ display: 'none' }} onChange={(e) => { handleFiles(e.target.files); e.target.value = ''; }} />
      </div>

      {msgs.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {msgs.map((m, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 14, color: m.err ? 'var(--color-accent-2-700)' : 'var(--color-text)' }}>
              <i className={m.err ? 'ph-duotone ph-x-circle' : 'ph-duotone ph-check-circle'}></i>{m.t}
            </div>
          ))}
        </div>
      )}

      <div style={{ position: 'relative', width: 280, maxWidth: '100%' }}>
        <i className="ph-duotone ph-magnifying-glass" style={{ position: 'absolute', left: 10, top: 10, fontSize: 16, opacity: 0.6 }}></i>
        <input className="input" placeholder="Search styles" value={search} onChange={(e) => setSearch(e.target.value)} style={{ paddingLeft: 32 }} />
      </div>

      {error && <div className="rs-danger-text" style={{ fontSize: 14 }}>Couldn’t load styles: {error.message}</div>}
      {loading && !data && <div className="rs-muted" style={{ fontSize: 14 }}>Loading styles…</div>}
      {data && cards.length === 0 && <div className="rs-muted" style={{ fontSize: 14 }}>{styles.length ? 'No style matches “' + search + '”.' : 'No styles yet. Drop a style JSON above.'}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(270px,1fr))', gap: '50px 35px' }}>
        {cards.map((s) => {
          const palette = (s.palette || []).map((name) => ({ name, hex: colourFor(name) }));
          const avoid = s.avoid || [];
          const open = !!avoidOpen[s.id];
          return (
            <div key={s.id} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ position: 'relative', width: '100%', aspectRatio: '4/3', borderRadius: 'var(--radius-md)', overflow: 'hidden', background: 'var(--color-surface)' }}>
                {s.image_url
                  ? <img src={bust(s.image_url, imgV)} alt={s.title + ' sample image'} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
                  : <>
                    <div style={{ display: 'flex', height: '100%' }}>{palette.map((p, i) => <span key={i} style={{ flex: 1, background: p.hex }}></span>)}</div>
                    <span style={{ position: 'absolute', left: 10, bottom: 10, fontSize: 12, padding: '3px 8px', borderRadius: 'var(--radius-md)', background: 'var(--color-bg)' }}>No sample image yet</span>
                  </>}
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 21, fontWeight: 600 }}>{s.title}</span>
                <span className="rs-mono" style={{ fontSize: 12, color: 'color-mix(in srgb,var(--color-text) 58%,transparent)' }}>{s.id}.json</span>
              </div>
              <div style={{ fontSize: 14, lineHeight: 1.45, textWrap: 'pretty' }}>{s.description}</div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                {palette.map((p, i) => <span key={i} title={p.name} style={{ width: 18, height: 18, borderRadius: '50%', background: p.hex, boxShadow: 'inset 0 0 0 1px color-mix(in srgb,var(--color-text) 12%,transparent)' }}></span>)}
                <span className="rs-muted" style={{ fontSize: 12 }}>{palette.map((p) => p.name).join(' · ')}</span>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {s.mood && <span className="tag tag-accent">{s.mood}</span>}
                {s.lighting && <span className="tag tag-neutral">{s.lighting}</span>}
              </div>
              <div>
                <button className="btn btn-ghost" onClick={() => setAvoidOpen((x) => ({ ...x, [s.id]: !open }))} style={{ paddingLeft: 0, fontSize: 13, color: 'var(--color-text)' }}>
                  <i className={open ? 'ph-duotone ph-caret-down' : 'ph-duotone ph-caret-right'}></i>Avoid ({avoid.length})
                </button>
                {open && <ul style={{ margin: '4px 0 0', paddingLeft: 20, fontSize: 13 }}>{avoid.map((a, i) => <li key={i}>{a}</li>)}</ul>}
              </div>
              <div style={{ fontSize: 13 }}><span className="rs-muted">Used by:</span> {usedLabel(s.used_by)}</div>
              <div style={{ display: 'flex', gap: 2, marginLeft: -5 }}>
                <button className="btn btn-ghost" onClick={() => { replaceFor.current = s; imgRef.current && imgRef.current.click(); }}>{s.image_url ? 'Replace sample image' : 'Add sample image'}</button>
                <button className="btn btn-ghost" onClick={() => setDialog({ kind: 'remove', style: s })} style={{ color: 'var(--color-accent-2-700)' }}>Remove</button>
              </div>
            </div>
          );
        })}
      </div>
      <input ref={imgRef} type="file" accept="image/png,image/jpeg" style={{ display: 'none' }} onChange={pickImage} />

      <Modal open={!!modal} title={modal ? modal.title : ''} onClose={() => setDialog(null)} width={modal ? modal.width : undefined} footer={modal && modal.footer}>
        {modal && modal.body}
      </Modal>
    </div>
  );
}
