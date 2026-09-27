// Character pool (spec section 7, mockup <!-- CHARACTERS -->).
import { useEffect, useRef, useState } from 'react';
import * as api from '../api.js';
import { useAsync } from '../hooks.js';
import Modal from '../components/Modal.jsx';
import { useToast } from '../components/Toast.jsx';

const usedLabel = (arr) => (arr && arr.length ? arr.join(', ') : 'No projects');
const isImage = (f) => /^image\/(png|jpeg)$/.test(f.type);

// A labelled drop target for one image. Shows `file` (new pick) or `url` (saved image).
function ImageSlot({ file, url, placeholder, onFile, fit = 'cover' }) {
  const [over, setOver] = useState(false);
  const [preview, setPreview] = useState(null);
  const input = useRef(null);
  useEffect(() => {
    if (!file) { setPreview(null); return undefined; }
    const u = URL.createObjectURL(file);
    setPreview(u);
    return () => URL.revokeObjectURL(u);
  }, [file]);
  const src = preview || url;
  const pick = (files) => { const f = Array.from(files || []).find(isImage); if (f) onFile(f); };
  return (
    <div
      onClick={() => input.current && input.current.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files); }}
      title="Drop an image here or click to browse"
      style={{
        width: '100%', height: '100%', borderRadius: 'var(--radius-md)', overflow: 'hidden', cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: src ? 0 : 10, boxSizing: 'border-box',
        border: src ? 0 : '1.5px dashed ' + (over ? 'var(--color-accent)' : 'var(--color-divider)'),
        background: over ? 'var(--color-accent-100)' : 'var(--color-bg)', fontSize: 13,
      }}
    >
      {src
        ? <img src={src} alt={placeholder} style={{ width: '100%', height: '100%', objectFit: fit, display: 'block' }} />
        : <span className="rs-muted"><i className="ph-duotone ph-image" style={{ fontSize: 22, display: 'block', marginBottom: 4 }}></i>{placeholder}</span>}
      <input ref={input} type="file" accept="image/png,image/jpeg" style={{ display: 'none' }}
        onClick={(e) => e.stopPropagation()} onChange={(e) => { pick(e.target.files); e.target.value = ''; }} />
    </div>
  );
}

function SlotBlock({ label, tag, tagClass, width, boxWidth = width, height, hint, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 14, fontWeight: 600 }}>{label}<span className={'tag ' + tagClass}>{tag}</span></div>
      <div style={{ width: boxWidth, height }}>{children}</div>
      {hint}
    </div>
  );
}

function CharacterForm({ editing, onDone, onCancel }) {
  const toast = useToast();
  const [name, setName] = useState(editing ? editing.name : '');
  const [desc, setDesc] = useState(editing ? editing.description || '' : '');
  const [files, setFiles] = useState({ main: null, turnaround: null, face: null });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const setFile = (slot) => (f) => setFiles((x) => ({ ...x, [slot]: f }));
  const hasMain = files.main || (editing && editing.main_url);
  const canSave = name.trim() && hasMain && !saving;

  const save = async () => {
    setSaving(true); setError(null);
    const fd = new FormData();
    fd.append('name', name.trim());
    fd.append('description', desc.trim());
    for (const slot of ['main', 'turnaround', 'face']) if (files[slot]) fd.append(slot, files[slot]);
    try {
      const c = editing ? await api.updateCharacter(editing.id, fd) : await api.createCharacter(fd);
      toast((editing ? 'Saved ' : 'Added ') + c.name + '.');
      onDone();
    } catch (e) {
      setError(e.message);
      setSaving(false);
    }
  };

  const [v] = useState(() => Date.now());
  const saved = (slot) => editing && editing[slot + '_url'] ? editing[slot + '_url'] + (editing[slot + '_url'].includes('?') ? '&' : '?') + 'v=' + v : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 25, padding: 30, borderRadius: 'var(--radius-lg)', background: 'var(--color-surface)' }}>
      <h3 style={{ margin: 0 }}>{editing ? 'Edit ' + editing.name : 'New character'}</h3>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: 20, maxWidth: 760 }}>
        <div className="field"><label>Name</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Sachin" style={{ background: 'var(--color-bg)' }} /></div>
        <div className="field"><label>Short description (optional)</label><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="man in his 30s, round glasses, green jacket" style={{ background: 'var(--color-bg)' }} /></div>
      </div>
      <div style={{ display: 'flex', gap: 30, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <SlotBlock label="Main reference image" tag="Required" tagClass="tag-accent-2" width={200} height={266}
          hint={<div className="rs-dim">Full body on a plain background.</div>}>
          <ImageSlot file={files.main} url={saved('main')} placeholder="Full body" onFile={setFile('main')} />
        </SlotBlock>
        <SlotBlock label="Turnaround sheet" tag="Recommended" tagClass="tag-neutral" width={320} height={266}
          hint={<div className="rs-dim">The character from several angles.</div>}>
          <ImageSlot file={files.turnaround} url={saved('turnaround')} placeholder="Front, side, back" onFile={setFile('turnaround')} fit="contain" />
        </SlotBlock>
        <SlotBlock label="Face close-up" tag="Optional" tagClass="tag-neutral" width={190} boxWidth={160} height={160}
          hint={<div style={{ fontSize: 12, lineHeight: 1.4, display: 'flex', gap: 6, color: 'var(--color-accent-2-700)' }}><i className="ph-duotone ph-warning" style={{ marginTop: 2 }}></i>Photoreal face close-ups can be blocked by the image model. Use an illustrated face.</div>}>
          <ImageSlot file={files.face} url={saved('face')} placeholder="Face" onFile={setFile('face')} />
        </SlotBlock>
      </div>
      {error && <div className="rs-danger-text" style={{ fontSize: 14, display: 'flex', gap: 8, alignItems: 'center' }}><i className="ph-duotone ph-x-circle"></i>{error}</div>}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="btn btn-primary" onClick={save} disabled={!canSave}>{saving ? 'Saving…' : 'Save character'}</button>
        <button className="btn btn-secondary" onClick={onCancel} disabled={saving}>Cancel</button>
        {!hasMain && name.trim() && <span className="rs-dim">Add the main reference image to save.</span>}
      </div>
    </div>
  );
}

export default function Characters() {
  const toast = useToast();
  const { data, error, loading, reload } = useAsync(() => api.listCharacters(), []);
  const [form, setForm] = useState(null); // null | { editing: Character|null }
  const [removing, setRemoving] = useState(null);
  const [imgVersion, setImgVersion] = useState(Date.now());
  const chars = data || [];

  const act = async (fn, okMsg) => {
    try { await fn(); if (okMsg) toast(okMsg); reload(); } catch (e) { toast(e.message, 'error'); }
  };
  const doneForm = () => { setForm(null); setImgVersion(Date.now()); reload(); };
  const doRemove = async () => {
    const c = removing;
    setRemoving(null);
    try {
      const r = await api.deleteCharacter(c.id);
      const n = (r && r.affected && r.affected.length) || 0;
      toast('Removed ' + c.name + '.' + (n ? ' ' + n + (n === 1 ? ' project now uses' : ' projects now use') + ' no character.' : ''));
      reload();
    } catch (e) { toast(e.message, 'error'); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 40 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 20, flexWrap: 'wrap' }}>
        <div><h1 style={{ margin: 0 }}>Characters</h1><p style={{ fontSize: 15, margin: '6px 0 0' }}>Each reel uses one character, or none. The character appears in that reel’s pictures.</p></div>
        <span style={{ flex: 1 }}></span>
        <button className="btn btn-primary" onClick={() => setForm({ editing: null })}><i className="ph-duotone ph-plus"></i>Add character</button>
      </div>

      {form && <CharacterForm key={form.editing ? form.editing.id : 'new'} editing={form.editing} onDone={doneForm} onCancel={() => setForm(null)} />}

      {error && <div className="rs-danger-text" style={{ fontSize: 14 }}>Couldn’t load characters: {error.message}</div>}
      {loading && !data && <div className="rs-muted" style={{ fontSize: 14 }}>Loading characters…</div>}

      {data && chars.length === 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 520 }}>
          <i className="ph-duotone ph-user-circle-plus" style={{ fontSize: 44, color: 'var(--color-accent)' }}></i>
          <h2 style={{ margin: 0 }}>No characters yet</h2>
          <p style={{ margin: 0, fontSize: 15 }}>Add a recurring character with a full-body reference image. Reels can also run with no character.</p>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(230px,1fr))', gap: '45px 30px' }}>
        {chars.map((c) => (
          <div key={c.id} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ width: '100%', aspectRatio: '3/4', borderRadius: 'var(--radius-md)', overflow: 'hidden', background: 'var(--color-surface)' }}>
              {c.main_url
                ? <img src={c.main_url + (c.main_url.includes('?') ? '&' : '?') + 'v=' + imgVersion} alt={c.name + ' · full body'} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
                : <div className="rs-muted" style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center', fontSize: 13 }}>{c.name} · full body</div>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 20, fontWeight: 600 }}>{c.name}</span>
              <button className="btn btn-icon" title={c.is_default ? 'Default for new projects' : 'Set as default'} style={{ width: 30, height: 30 }}
                onClick={() => !c.is_default && act(() => api.setDefaultCharacter(c.id), c.name + ' is now the default for new projects.')}>
                <i className={c.is_default ? 'ph-fill ph-star' : 'ph-duotone ph-star'} style={{ fontSize: 18, color: 'var(--color-accent)' }}></i>
              </button>
              {c.is_default && <span className="tag tag-accent">Default</span>}
            </div>
            <div style={{ fontSize: 14, color: 'color-mix(in srgb,var(--color-text) 70%,transparent)' }}>{c.description || 'No description'}</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <span className="tag tag-neutral">{c.turnaround_url ? 'Turnaround sheet ✓' : 'No turnaround sheet'}</span>
              <span className="tag tag-neutral">{c.face_url ? 'Face close-up ✓' : 'No face close-up'}</span>
            </div>
            <div style={{ fontSize: 13 }}><span className="rs-muted">Used by:</span> {usedLabel(c.used_by)}</div>
            <div style={{ display: 'flex', gap: 2, marginLeft: -5 }}>
              <button className="btn btn-ghost" onClick={() => setForm({ editing: c })}>Edit</button>
              <button className="btn btn-ghost" onClick={() => act(() => api.duplicateCharacter(c.id), 'Duplicated ' + c.name + '.')}>Duplicate</button>
              <button className="btn btn-ghost" onClick={() => setRemoving(c)} style={{ color: 'var(--color-accent-2-700)' }}>Remove</button>
            </div>
          </div>
        ))}
      </div>

      <Modal
        open={!!removing}
        title={removing ? 'Remove ' + removing.name + '?' : ''}
        onClose={() => setRemoving(null)}
        footer={<>
          <button className="btn btn-secondary" onClick={() => setRemoving(null)}>Cancel</button>
          <button className="btn btn-primary" style={{ background: 'var(--color-accent-2-600)' }} onClick={doRemove}>Remove character</button>
        </>}
      >
        {removing && (removing.used_by && removing.used_by.length ? (
          <>
            <div>These projects use {removing.name}. They will fall back to “No character”:</div>
            <ul style={{ margin: '8px 0 0', paddingLeft: 20, fontSize: 14 }}>{removing.used_by.map((p) => <li key={p}>{p}</li>)}</ul>
          </>
        ) : 'No projects use this character.')}
      </Modal>
    </div>
  );
}
