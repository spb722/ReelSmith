import { useEffect, useState } from 'react';
import {
  characterImageUrl, createProject, getDefaults, getMeta, getSettings, listPresets, listProjects,
  putSettings, reorderScreenshots, startRun, uploadScreenshots,
} from '../api.js';
import { useAsync } from '../hooks.js';
import { navigate } from '../router.js';
import ScreenshotDrop from '../components/ScreenshotDrop.jsx';
import SettingsPanel from '../components/SettingsPanel.jsx';
import { useToast } from '../components/Toast.jsx';

const STEPS = ['Name', 'Screenshots', 'Settings', 'Review & run'];
const NAME_RE = /^[A-Za-z0-9_-]+$/;
const muted = 'color-mix(in srgb,var(--color-text) 62%,transparent)';
// A typical run costs about $9–$16 (the design's figure); the high end is capped at the budget.
const COST_LO = 9, COST_HI = 16;

const labelOf = (list, id) => (list || []).find((x) => x.id === id)?.label || id;

export default function Wizard() {
  const toast = useToast();
  const { data: projects } = useAsync(listProjects, []);
  const { data: meta } = useAsync(getMeta, []);
  const { data: defaults } = useAsync(getDefaults, []);
  const { data: presets } = useAsync(listPresets, []);
  const [step, setStep] = useState(0);
  const [name, setName] = useState('');
  const [from, setFrom] = useState('defaults');
  const [copyFrom, setCopyFrom] = useState('');
  const [presetName, setPresetName] = useState('');
  const [files, setFiles] = useState([]); // [{ file, url, blob }]
  const [dropMsg, setDropMsg] = useState(null);
  const [settings, setSettings] = useState(null);
  const [busy, setBusy] = useState(false);

  const names = (projects || []).map((p) => p.name);
  const nameOk = NAME_RE.test(name);
  const nameTaken = names.includes(name);
  const nameError = !name ? '' : !nameOk ? 'Use only letters, numbers, - and _. No spaces.' : nameTaken ? `A project called “${name}” already exists.` : '';
  const nameValid = nameOk && !nameTaken;

  useEffect(() => { if (!copyFrom && names.length) setCopyFrom(names[0]); }, [names.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const presetList = presets || [];
  useEffect(() => { if (!presetName && presetList.length) setPresetName(presetList[0].name); }, [presetList.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // Settings start from Defaults, a preset over the Defaults, or the chosen project's settings.
  useEffect(() => {
    let dead = false;
    if (from === 'copy' && copyFrom) {
      getSettings(copyFrom).then((r) => { if (!dead) setSettings(r.settings); }).catch((e) => toast(e.message, 'error'));
    } else if (from === 'preset' && defaults) {
      const preset = presetList.find((x) => x.name === presetName);
      setSettings(preset ? { ...defaults, ...preset.settings } : defaults);
    } else if (defaults) {
      setSettings(defaults);
    }
    return () => { dead = true; };
  }, [from, copyFrom, presetName, presets, defaults]); // eslint-disable-line react-hooks/exhaustive-deps

  // — screenshots (kept in the browser until the project is created) —
  const addFiles = (list) => {
    const msgs = [];
    const next = files.slice();
    for (const f of list) {
      if (next.some((x) => x.file === f.name)) { msgs.push(`“${f.name}” is already in the list.`); continue; }
      next.push({ file: f.name, url: URL.createObjectURL(f), blob: f });
    }
    setFiles(next);
    setDropMsg(msgs.join(' ') || null);
  };
  const removeFile = (file) => {
    const f = files.find((x) => x.file === file);
    if (f) URL.revokeObjectURL(f.url);
    setFiles(files.filter((x) => x.file !== file));
  };
  const reorder = (order) => setFiles(order.map((k) => files.find((x) => x.file === k)));

  // — create —
  const create = async (run) => {
    setBusy(true);
    let created = false;
    try {
      await createProject(name, from === 'copy' ? copyFrom : null);
      created = true;
      // Upload one at a time so we know each file's server name, then fix the reading order.
      const order = [];
      const rejected = [];
      for (const f of files) {
        const before = new Set(order);
        const res = await uploadScreenshots(name, [f.blob]);
        const added = (res.screenshots || []).map((s) => s.file).filter((x) => !before.has(x));
        order.push(...added.filter((x) => !order.includes(x)));
        rejected.push(...(res.rejected || []));
      }
      if (order.length > 1) await reorderScreenshots(name, order);
      if (settings) await putSettings(name, settings);
      if (rejected.length) toast(rejected.map((r) => `“${r.file}”: ${r.reason}`).join(' '), 'error');
      if (run) {
        await startRun(name, { mode: 'resume', pause_after_narration: !!(settings && settings.pause_after_narration) });
        toast(`Started ${name}.`, 'success');
      } else {
        toast(`Saved ${name} as a draft.`, 'success');
      }
      navigate(`/p/${encodeURIComponent(name)}/${run ? 'run' : 'overview'}`);
    } catch (e) {
      toast(e.message, 'error');
      // The project exists already (a later step failed): open it so the operator can fix it there.
      if (created) navigate(`/p/${encodeURIComponent(name)}/overview`);
    } finally {
      setBusy(false);
    }
  };

  // — review summary —
  const s = settings || {};
  const budget = Number(s.max_budget_usd || 0);
  const hi = budget ? Math.min(COST_HI, budget) : COST_HI;
  const lo = Math.min(COST_LO, hi);
  const story = (meta?.story_skills || []).find((x) => x.id === s.story_skill);
  const charName = s.character_id ? (meta?.characters || []).find((c) => c.id === s.character_id)?.name || s.character_id : 'No character';
  const summary = [
    ['Name', name || '—'],
    ['Screenshots', files.length + ' in reading order'],
    ['Settings from', from === 'copy' ? copyFrom : from === 'preset' ? 'Preset “' + presetName + '”' : 'Defaults'],
    ['Pictures', labelOf(meta?.source_modes, s.source_mode) || '—'],
    ...(s.source_mode === 'content' ? [['Art style', (meta?.styles || []).find((x) => x.id === s.style)?.name || s.style]] : []),
    ['Story style', story ? story.label + (story.words ? ' · ' + story.words + ' words' : '') : s.story_skill || '—'],
    ['Narrator voice', s.voice || '—'],
    ['Models', [s.claude_model, s.image_provider === 'codex' ? 'Codex' : 'Gemini', s.veo_model].filter(Boolean).join(' · ')],
    ['Budget', '$' + budget.toFixed(2)],
    ['Pause after narration', s.pause_after_narration ? 'On' : 'Off'],
  ];

  return (
    <>
      {/* WIZARD HEAD */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 25 }}>
        <div><button className="btn btn-ghost" onClick={() => navigate('/')} style={{ fontSize: 13 }}><i className="ph-duotone ph-arrow-left" />Projects</button></div>
        <h1 style={{ margin: 0 }}>New project</h1>
        <div style={{ display: 'flex', gap: 30, flexWrap: 'wrap' }}>
          {STEPS.map((label, i) => (
            <button key={label} className="rs-link-btn" disabled={i > step && !nameValid} onClick={() => setStep(i)} style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'inherit' }}>
              <span style={{ width: 26, height: 26, borderRadius: '50%', display: 'grid', placeItems: 'center', fontSize: 13, fontWeight: 600, background: i === step ? 'var(--color-accent)' : i < step ? 'var(--color-text)' : 'var(--color-neutral-200)', color: i <= step ? 'var(--color-bg)' : 'var(--color-neutral-700)' }}>{i < step ? '✓' : i + 1}</span>
              <span style={{ fontSize: 15, fontWeight: i === step ? 600 : 400, color: i === step ? 'var(--color-text)' : muted }}>{label}</span>
            </button>
          ))}
        </div>
      </div>

      {step === 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 30, maxWidth: 560 }}>
          <div className="field">
            <label>Project name</label>
            <input className="input rs-mono" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. tiny_changes" style={{ fontSize: 18, minHeight: 44 }} />
            <div style={{ fontSize: 13, marginTop: 6, color: muted }}>Becomes the folder name. Letters, numbers, <code>-</code> and <code>_</code> only.</div>
            {nameError && <div className="rs-danger-text" style={{ fontSize: 13, marginTop: 4, display: 'flex', gap: 6, alignItems: 'center' }}><i className="ph-duotone ph-warning-circle" />{nameError}</div>}
          </div>
          <div className="field">
            <label>Start from</label>
            <div className="seg">
              <label className="seg-opt"><input type="radio" name="rs-from" checked={from === 'defaults'} onChange={() => setFrom('defaults')} />Defaults</label>
              <label className="seg-opt"><input type="radio" name="rs-from" checked={from === 'copy'} disabled={!names.length} onChange={() => setFrom('copy')} />Copy settings from a project</label>
              <label className="seg-opt"><input type="radio" name="rs-from" checked={from === 'preset'} disabled={!presetList.length} onChange={() => setFrom('preset')} />A preset</label>
            </div>
            {from === 'preset' && (
              <select className="input" style={{ marginTop: 10, maxWidth: 320 }} value={presetName} onChange={(e) => setPresetName(e.target.value)}>
                {presetList.map((o) => <option key={o.name} value={o.name}>{o.name}</option>)}
              </select>
            )}
            {from === 'copy' && (
              <select className="input" style={{ marginTop: 10, maxWidth: 320 }} value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
                {names.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            )}
          </div>
        </div>
      )}

      {step === 1 && <ScreenshotDrop files={files} onAdd={addFiles} onRemove={removeFile} onReorder={reorder} message={dropMsg} />}

      {step === 2 && (settings && meta
        ? <SettingsPanel value={settings} defaults={defaults || settings} meta={meta} onChange={setSettings} />
        : <div className="rs-muted">Loading settings…</div>)}

      {step === 3 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.3fr) minmax(260px,1fr)', gap: 50, alignItems: 'start' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <h6 style={{ margin: 0, color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' }}>Summary</h6>
            {summary.map(([k, v]) => (
              <div key={k} style={{ display: 'grid', gridTemplateColumns: '170px minmax(0,1fr)', gap: 20, fontSize: 15 }}>
                <span style={{ color: muted }}>{k}</span><span style={{ overflowWrap: 'anywhere' }}>{v}</span>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 30 }}>
            <div style={{ display: 'flex', gap: 15, alignItems: 'center' }}>
              <div style={{ width: 88, height: 88, flex: 'none', borderRadius: '50%', overflow: 'hidden', background: 'var(--color-surface)', display: 'grid', placeItems: 'center' }}>
                {s.character_id
                  ? <img src={characterImageUrl(s.character_id, 'main')} alt={charName} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                  : <i className="ph-duotone ph-user" style={{ fontSize: 36, opacity: 0.5 }} />}
              </div>
              <div><div style={{ fontSize: 12, color: muted }}>Character</div><div style={{ fontSize: 20, fontWeight: 600 }}>{charName}</div></div>
            </div>
            <div>
              <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 44, lineHeight: 1, letterSpacing: '-0.02em' }}>${lo} – ${hi}</div>
              <div style={{ fontSize: 14, marginTop: 6 }}>Estimated cost · about 20 minutes</div>
              <div style={{ fontSize: 13, color: muted }}>Stops before going over the ${budget % 1 ? budget.toFixed(2) : budget} budget.</div>
            </div>
          </div>
        </div>
      )}

      {/* WIZARD FOOTER */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', paddingTop: 10 }}>
        <button className="btn btn-secondary" onClick={() => (step === 0 ? navigate('/') : setStep(step - 1))} disabled={busy}><i className="ph-duotone ph-arrow-left" />Back</button>
        <span className="rs-spacer" />
        {step < 3 && <button className="btn btn-primary" onClick={() => setStep(step + 1)} disabled={step === 0 && !nameValid}>Next<i className="ph-duotone ph-arrow-right" /></button>}
        {step === 3 && (
          <>
            <button className="btn btn-secondary" onClick={() => create(false)} disabled={busy || !nameValid}>Save as draft</button>
            <button className="btn btn-primary" onClick={() => create(true)} disabled={busy || !nameValid || files.length === 0} title={files.length === 0 ? 'Add screenshots first' : undefined}>
              {busy ? <i className="ph-duotone ph-circle-notch rs-spinner" /> : <i className="ph-duotone ph-play" />}Create and run
            </button>
          </>
        )}
      </div>
    </>
  );
}
