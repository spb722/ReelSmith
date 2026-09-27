// Setup screen: Google Cloud settings (putSetup), logins/keys status (masked), health check.
import { useEffect, useState } from 'react';
import { getSetup, putSetup, runHealth } from '../api.js';
import { useAsync } from '../hooks.js';
import { useToast } from '../components/Toast.jsx';

const H6 = { margin: 0, color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' };
const MONO = { fontFamily: 'ui-monospace,Menlo,Consolas,monospace', fontSize: 13 };
const REGIONS = ['global', 'us-central1', 'europe-west4', 'asia-south1'];
const tagStyle = (ok) => (ok ? { background: 'var(--color-accent-100)', color: 'var(--color-accent-800)' } : { background: 'var(--color-accent-2-100)', color: 'var(--color-accent-2-800)' });

export default function Setup() {
  const toast = useToast();
  const setup = useAsync(getSetup, []);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [checks, setChecks] = useState(null);
  const [checking, setChecking] = useState(false);

  const fromData = (d) => ({ project_id: d.project_id || '', location: d.location || '', gcs_bucket_uri: d.gcs_bucket_uri || '' });
  useEffect(() => { if (setup.data) setForm(fromData(setup.data)); }, [setup.data]);

  const d = setup.data;
  const dirty = d && form && JSON.stringify(fromData(d)) !== JSON.stringify(form);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const save = async () => {
    setSaving(true);
    try { await putSetup(form); await setup.reload(); toast('Setup saved to .env', 'success'); }
    catch (e) { toast(e.message, 'error'); }
    finally { setSaving(false); }
  };
  const check = async () => {
    setChecking(true); setChecks(null);
    try { setChecks(await runHealth()); }
    catch (e) { toast(e.message, 'error'); }
    finally { setChecking(false); }
  };
  const fails = (checks || []).filter((c) => !c.ok).length;
  const summary = checks ? (fails ? `${fails} ${fails === 1 ? 'problem' : 'problems'} to fix` : 'All checks passed') : '';
  const rows = d ? [
    ...(d.logins || []).map((l) => ({ name: l.name, help: l.ok ? 'Login' : (l.hint ? 'Fix: ' + l.hint : 'Not signed in'), masked: l.ok ? 'Signed in' : 'Not signed in', ok: l.ok, status: l.ok ? 'Signed in' : 'Not signed in' })),
    ...(d.keys || []).map((k) => ({ name: k.name, help: 'Never shown in full', masked: k.set ? k.masked : '', ok: k.set, status: k.set ? 'Saved' : 'Not set' })),
  ] : [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 45, maxWidth: 980 }}>
      <div><h1 style={{ margin: 0 }}>Setup</h1><p style={{ fontSize: 15, margin: '6px 0 0' }}>One-time settings for this machine. They apply to every project.</p></div>

      {setup.error && !d && <div className="rs-danger-text">Could not load setup: {setup.error.message}</div>}

      {form && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div style={{ display: 'flex', gap: 15, alignItems: 'center' }}>
            <h6 style={H6}>Google Cloud</h6>
            <span style={{ flex: 1 }} />
            <button className="btn btn-primary" disabled={!dirty || saving} onClick={save}><i className="ph-duotone ph-floppy-disk" />{saving ? 'Saving…' : 'Save'}</button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 20 }}>
            <div className="field"><label>Project ID</label><input className="input" value={form.project_id} onChange={set('project_id')} style={MONO} /></div>
            <div className="field"><label>Region</label>
              <select className="input" value={form.location} onChange={set('location')}>
                {(form.location && !REGIONS.includes(form.location) ? [form.location, ...REGIONS] : REGIONS).map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div className="field"><label>Storage bucket for video</label><input className="input" value={form.gcs_bucket_uri} onChange={set('gcs_bucket_uri')} placeholder="gs://bucket/path/" style={MONO} /></div>
          </div>
        </section>
      )}

      {d && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <h6 style={H6}>Logins and keys</h6>
          {rows.length === 0 && <div className="rs-muted" style={{ fontSize: 14 }}>Nothing to show.</div>}
          {rows.map((k) => (
            <div key={k.name} style={{ display: 'grid', gridTemplateColumns: 'minmax(180px,230px) minmax(0,1fr)', gap: 15, alignItems: 'center' }}>
              <div><div style={{ fontWeight: 600 }}>{k.name}</div><div style={{ fontSize: 12, color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' }}>{k.help}</div></div>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', minWidth: 0 }}>
                <input className="input" type="text" readOnly value={k.masked} placeholder="—" style={{ ...MONO, maxWidth: 320 }} />
                <span className="tag" style={tagStyle(k.ok)}>{k.status}</span>
              </div>
            </div>
          ))}
        </section>
      )}

      <section style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <div style={{ display: 'flex', gap: 15, alignItems: 'center', flexWrap: 'wrap' }}>
          <h6 style={H6}>Health check</h6>
          <span style={{ flex: 1 }} />
          {summary && <span style={{ fontSize: 14 }}>{summary}</span>}
          <button className="btn btn-primary" onClick={check} disabled={checking}>
            <i className={checking ? 'ph-duotone ph-circle-notch rs-spinner' : 'ph-duotone ph-stethoscope'} />
            {checking ? 'Checking…' : checks ? 'Check again' : 'Run health check'}
          </button>
        </div>
        {!checks && !checking && <p style={{ margin: 0, fontSize: 15, color: 'color-mix(in srgb,var(--color-text) 70%,transparent)' }}>Runs the same pre-flight checks as the terminal tool: tools installed, logins valid, bucket writable.</p>}
        {(checks || []).map((c) => (
          <div key={c.name} style={{ display: 'grid', gridTemplateColumns: '26px minmax(0,1fr)', gap: 12 }}>
            <i className={c.ok ? 'ph-duotone ph-check-circle' : 'ph-duotone ph-x-circle'} style={{ fontSize: 24, color: c.ok ? 'var(--color-accent)' : 'var(--color-accent-2-600)' }} aria-label={c.ok ? '✓ passed' : '✗ failed'} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'baseline' }}>
                <span style={{ fontWeight: 600 }}>{c.name}</span>
                <span style={{ fontSize: 13, color: 'color-mix(in srgb,var(--color-text) 62%,transparent)' }}>{c.detail}</span>
              </div>
              {!c.ok && c.hint && (
                <div style={{ fontSize: 13, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  Fix: run <code style={{ ...MONO, padding: '2px 8px', borderRadius: 'var(--radius-md)', background: 'var(--color-surface)' }}>{c.hint}</code> in a terminal, then check again.
                </div>
              )}
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}
