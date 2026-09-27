// Defaults screen: presets, the full settings panel, cover defaults. Saved with putDefaults.
import { useEffect, useState } from 'react';
import { deletePreset, getDefaults, getMeta, listPresets, putDefaults, savePreset } from '../api.js';
import { useAsync } from '../hooks.js';
import SettingsPanel from '../components/SettingsPanel.jsx';
import { useToast } from '../components/Toast.jsx';

const H6 = { margin: 0, color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' };
const ROW = { display: 'grid', gridTemplateColumns: 'minmax(180px,250px) minmax(0,1fr)', gap: '8px 40px' };
const COVER_ROWS = [
  ['hook_mode', 'Hook text', 'The headline on the cover.', [['claude', 'Let Claude write it'], ['own', 'Use my own text']]],
  ['shot_mode', 'Base artwork', 'Which shot the cover is built from.', [['claude', 'Let Claude choose'], ['pick', 'I’ll pick a shot']]],
  ['image_provider', 'Image maker', 'Defaults to the reel’s image maker.', [['', 'Same as the reel'], ['gemini', 'Gemini'], ['codex', 'Codex']]],
];

export default function Defaults() {
  const toast = useToast();
  const meta = useAsync(getMeta, []);
  const loaded = useAsync(getDefaults, []);
  const presets = useAsync(listPresets, []);
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [presetDraft, setPresetDraft] = useState(null);
  const [activePreset, setActivePreset] = useState(null);

  useEffect(() => { if (loaded.data) { setSaved(loaded.data); setDraft(loaded.data); } }, [loaded.data]);

  const dirty = saved && JSON.stringify(saved) !== JSON.stringify(draft);

  const save = async () => {
    setSaving(true);
    try {
      const res = await putDefaults(draft);
      setSaved(res); setDraft(res);
      toast('Defaults saved. New projects will start with these.', 'success');
    } catch (e) { toast(e.message, 'error'); } finally { setSaving(false); }
  };
  const addPreset = async () => {
    const name = presetDraft.trim();
    try {
      await savePreset(name, draft);
      setPresetDraft(null); setActivePreset(name);
      presets.reload();
      toast(`Preset “${name}” saved`, 'success');
    } catch (e) { toast(e.message, 'error'); }
  };
  const removePreset = async (name) => {
    try {
      await deletePreset(name);
      if (activePreset === name) setActivePreset(null);
      presets.reload();
    } catch (e) { toast(e.message, 'error'); }
  };
  const applyPreset = (p) => {
    setDraft({ ...draft, ...p.settings });
    setActivePreset(p.name);
    toast(`Applied “${p.name}”. Save to keep it as the defaults.`);
  };
  const cover = draft?.cover || {};
  const setCover = (k, val) => setDraft({ ...draft, cover: { ...cover, [k]: val } });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 40 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 30 }}>
        <div style={{ display: 'flex', gap: 20, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div>
            <h1 style={{ margin: 0 }}>Defaults</h1>
            <p style={{ fontSize: 15, margin: '6px 0 0', maxWidth: 620 }}>These values fill every new project. Changing them does not change existing projects.</p>
          </div>
          <span style={{ flex: 1 }} />
          {dirty && <span className="rs-muted" style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--color-accent)' }} />Unsaved changes</span>}
          <button className="btn btn-secondary" disabled={!dirty || saving} onClick={() => { setDraft(saved); setActivePreset(null); }}>Discard</button>
          <button className="btn btn-primary" disabled={!dirty || saving} onClick={save}><i className="ph-duotone ph-floppy-disk" />{saving ? 'Saving…' : 'Save defaults'}</button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <h6 style={H6}>Presets</h6>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            {(presets.data || []).map((p) => {
              const on = activePreset === p.name;
              return (
                <span key={p.name} style={{ display: 'flex', alignItems: 'center', borderRadius: 'var(--radius-md)', border: '1px solid ' + (on ? 'var(--color-accent)' : 'var(--color-divider)'), background: on ? 'var(--color-accent-100)' : 'transparent', color: on ? 'var(--color-accent-800)' : 'inherit' }}>
                  <button className="rs-link-btn" title="Apply this preset" disabled={!draft} onClick={() => applyPreset(p)} style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '7px 4px 7px 12px', fontSize: 13 }}>
                    <i className="ph-duotone ph-bookmark-simple" />{p.name}
                  </button>
                  <button className="rs-link-btn" aria-label={`Delete preset ${p.name}`} title="Delete preset" onClick={() => removePreset(p.name)} style={{ padding: '7px 10px 7px 6px', fontSize: 13 }}>
                    <i className="ph-duotone ph-x" />
                  </button>
                </span>
              );
            })}
            {presetDraft != null ? (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input className="input" placeholder="Preset name" value={presetDraft} autoFocus onChange={(e) => setPresetDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && presetDraft.trim()) addPreset(); }} style={{ width: 240 }} />
                <button className="btn btn-primary" onClick={addPreset} disabled={!presetDraft.trim() || !draft}>Save</button>
                <button className="btn btn-secondary" onClick={() => setPresetDraft(null)}>Cancel</button>
              </div>
            ) : (
              <button className="btn btn-ghost" onClick={() => setPresetDraft('')}><i className="ph-duotone ph-plus" />Save current settings as a preset</button>
            )}
          </div>
        </div>
      </div>

      {loaded.error && !draft && <div className="rs-danger-text">Could not load defaults: {loaded.error.message}</div>}
      {draft && <SettingsPanel value={draft} defaults={null} meta={meta.data} onChange={setDraft} showRawConfig />}

      {draft && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: 25, maxWidth: 1000 }}>
          <h6 style={H6}>Cover defaults</h6>
          {COVER_ROWS.map(([key, label, help, options]) => (
            <div key={key} style={ROW}>
              <div><div style={{ fontWeight: 600 }}>{label}</div><div style={{ fontSize: 13, color: 'color-mix(in srgb,var(--color-text) 62%,transparent)' }}>{help}</div></div>
              <div className="seg" style={{ alignSelf: 'start' }}>
                {options.map(([val, lab]) => (
                  <label key={val || 'same'} className="seg-opt"><input type="radio" name={'cd-' + key} checked={(cover[key] ?? '') === val} onChange={() => setCover(key, val)} />{lab}</label>
                ))}
              </div>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
