// Project Settings tab: the shared panel (with raw config) + an unsaved-changes bar with Discard / Save.
// Props: { name } (project name)
import { useEffect, useState } from 'react';
import { getMeta, getProject, getSettings, putSettings } from '../../api.js';
import { useAsync } from '../../hooks.js';
import SettingsPanel, { describeAffected } from '../../components/SettingsPanel.jsx';
import { useToast } from '../../components/Toast.jsx';

export default function SettingsTab({ name }) {
  const toast = useToast();
  const meta = useAsync(getMeta, []);
  const project = useAsync(() => getProject(name), [name]);
  const loaded = useAsync(() => getSettings(name), [name]);
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState(null);
  const [defaults, setDefaults] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!loaded.data) return;
    setSaved(loaded.data.settings);
    setDraft(loaded.data.settings);
    setDefaults(loaded.data.defaults);
  }, [loaded.data]);

  if (loaded.error && !draft) return <div className="rs-danger-text">Could not load settings: {loaded.error.message}</div>;
  if (!draft) return <div className="rs-muted">Loading settings…</div>;

  const dirty = JSON.stringify(saved) !== JSON.stringify(draft);
  const status = project.data?.status;
  const hasRun = status && status !== 'draft';
  const charSwapWarn = status === 'finished' && (saved.character_id ?? null) !== (draft.character_id ?? null);

  const save = async () => {
    setSaving(true);
    try {
      const res = await putSettings(name, draft);
      setSaved(res.settings);
      setDraft(res.settings);
      setDefaults(res.defaults);
      toast('Settings saved', 'success');
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 40 }}>
      {dirty && (
        <div style={{ display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap', padding: '15px 20px', background: 'var(--color-accent-100)', color: 'var(--color-accent-900)', borderRadius: 'var(--radius-md)', maxWidth: 1000 }}>
          <i className="ph-duotone ph-info" style={{ fontSize: 22 }} />
          <div style={{ flex: 1, minWidth: 240 }}>
            <div style={{ fontWeight: 600 }}>{hasRun ? 'Next run will use new settings' : 'Unsaved changes'}</div>
            {hasRun && <div style={{ fontSize: 13 }}>Affects: {describeAffected(saved, draft)}</div>}
            {charSwapWarn && <div style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center', color: 'var(--color-accent-2-700)', marginTop: 4 }}><i className="ph-duotone ph-warning" />This reel is finished. Swapping the character means every picture is remade on the next run.</div>}
          </div>
          <button className="btn btn-secondary" onClick={() => setDraft(saved)} disabled={saving}>Discard</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save settings'}</button>
        </div>
      )}
      <SettingsPanel value={draft} defaults={defaults} meta={meta.data} onChange={setDraft} showRawConfig projectName={name} />
    </div>
  );
}
