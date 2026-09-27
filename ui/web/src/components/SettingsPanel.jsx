// The shared Settings panel (settings_panel.dc.html): Basic fields, collapsible Advanced, optional raw config view.
// Props: { value, defaults, meta, onChange(next), showRawConfig?, projectName? }
// - value/defaults are settings objects (CONTRACT.md section 4). When `defaults` is given, changed fields
//   get a dot + Reset. The raw view edits the same settings object as JSON, so form and raw stay in sync.
import { useRef, useState } from 'react';
import { getConfig, listCharacters, listStyles } from '../api.js';
import { useAsync } from '../hooks.js';
import StylePicker from './StylePicker.jsx';
import CharacterPicker from './CharacterPicker.jsx';

const MUTED = 'color-mix(in srgb,var(--color-text) 62%,transparent)';
const H6 = { margin: 0, color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' };
const ROW = { display: 'grid', gridTemplateColumns: 'minmax(180px,250px) minmax(0,1fr)', gap: '8px 40px' };
const DOT = { width: 7, height: 7, borderRadius: '50%', background: 'var(--color-accent)', flex: 'none' };
const MONO = 'ui-monospace,Menlo,Consolas,monospace';

const STEP_NAMES = ['Looking at your screenshots', 'Writing the narration', 'Planning the shots', 'Recording the voice and building subtitles', 'Making the video clips and still pictures', 'Putting the final video together'];
const AFFECTS = { source_mode: [1, 3, 5], story_skill: [2, 3, 4, 5, 6], style: [5], character_id: [5], voice: [4, 6], claude_model: [1, 2, 3, 5], reviewer_model: [2, 5], image_provider: [5], image_fallback_provider: [5], image_model: [5], veo_model: [5], veo_resolution: [5, 6] };

/** "Affects: …" text for the steps a settings change will redo on the next run. */
export function describeAffected(saved, next) {
  const steps = new Set();
  Object.keys(AFFECTS).forEach((k) => { if (!same(saved?.[k], next?.[k])) AFFECTS[k].forEach((n) => steps.add(n)); });
  if (!steps.size) return 'Retry limits and speed only; no steps redone';
  return [...steps].sort().map((n) => `${n}. ${STEP_NAMES[n - 1]}`).join(' · ');
}

const FALLBACK_SKILLS = [
  { id: 'story-craft', label: 'Dense & punchy', words: '90–115', seconds: '40–50' },
  { id: 'story-craft-easy', label: 'Easy to follow', words: '90–140', seconds: '40–60' },
];
const FALLBACK_MODES = [
  { id: 'content_and_images', label: "Use the screenshot's artwork" },
  { id: 'content', label: 'Invent new pictures from the text' },
];

const NUM_GROUPS = [
  { title: 'Budget & limits', help: 'Running out of attempts, or reaching the budget, stops the run with a reason.', fields: [
    ['max_budget_usd', 'Budget ceiling for the whole run', { prefix: '$', step: 1 }],
    ['max_attempts.asset_analyst', 'Max attempts — screenshots'],
    ['max_attempts.story_agent', 'Max attempts — narration'],
    ['max_attempts.visual_agent', 'Max attempts — shot planning'],
    ['max_attempts.voice_agent', 'Max attempts — voice'],
    ['max_attempts.veo_agent', 'Max attempts — each video clip'],
    ['max_attempts.stills_agent', 'Max attempts — each still picture'],
    ['claude_shot_attempt_cap_usd', 'Claude spend cap per shot attempt', { prefix: '$', step: 0.5 }],
    ['claude_reviewer_cap_usd', 'Claude spend cap per review', { prefix: '$', step: 0.5 }],
  ] },
  { title: 'Speed', help: 'How much runs at the same time.', fields: [
    ['shot_concurrency', 'Shots in production at once'],
    ['veo_concurrency', 'Video clips generating at once'],
    ['gemini_image_concurrency', 'Gemini images at once'],
    ['codex_image_concurrency', 'Codex images at once'],
    ['codex_image_timeout_seconds', 'Codex image timeout', { unit: 's', step: 30 }],
    ['veo_poll_seconds', 'Video status check interval', { unit: 's' }],
    ['veo_max_poll_seconds', 'Video max wait', { unit: 's', step: 30 }],
  ] },
  { title: 'Cost estimates', help: 'Rarely touched. Used only to reserve budget before a call.', fields: [
    ['image_call_cost_usd', 'Estimated cost per image', { prefix: '$', step: 0.01 }],
    ['veo_call_cost_usd', 'Estimated cost per video clip', { prefix: '$', step: 0.1 }],
  ] },
];
const BASIC_PATHS = ['source_mode', 'story_skill', 'style', 'pause_after_narration', 'character_id', 'voice', 'claude_model', 'reviewer_model', 'image_provider', 'image_fallback_provider', 'image_model', 'veo_model', 'veo_resolution'];
const ALL_PATHS = [...BASIC_PATHS, ...NUM_GROUPS.flatMap((g) => g.fields.map((f) => f[0]))];

function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function getIn(obj, path) { return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj); }
function setIn(obj, path, val) {
  const [k, ...rest] = path.split('.');
  return { ...obj, [k]: rest.length ? setIn(obj?.[k] || {}, rest.join('.'), val) : val };
}
function range(x) { return Array.isArray(x) ? x.join('–') : String(x ?? ''); }
/** Options list that always contains the current value. */
function withCurrent(list, current) { const l = list || []; return current && !l.includes(current) ? [current, ...l] : l; }

const ENUMS = { source_mode: ['content_and_images', 'content'], image_provider: ['gemini', 'codex'], image_fallback_provider: ['', 'gemini', 'codex'], veo_resolution: ['720p', '1080p'] };

/** Throws a plain-words Error when the raw text is not a usable settings object. */
function parseRaw(text, ref) {
  let obj;
  try { obj = JSON.parse(text); } catch (e) {
    const msg = e.message.replace(/ in JSON.*$/, '').replace(/^JSON\.parse: /, '');
    const lc = /line (\d+)/.exec(e.message);
    const pos = /position (\d+)/.exec(e.message);
    const line = lc ? +lc[1] : pos ? text.slice(0, +pos[1]).split('\n').length : null;
    throw new Error((line ? `Line ${line}: ` : '') + msg + '.');
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error('The config must be one JSON object { … }.');
  for (const [k, rv] of Object.entries(ref || {})) {
    if (!(k in obj)) throw new Error(`"${k}" is missing.`);
    const v = obj[k];
    if (k === 'character_id') { if (v !== null && typeof v !== 'string') throw new Error('"character_id" should be a character id or null.'); continue; }
    if (rv !== null && typeof v !== typeof rv) throw new Error(`"${k}" should be a ${typeof rv}.`);
    if (rv && typeof rv === 'object') for (const [kk, rvv] of Object.entries(rv)) {
      if (typeof v[kk] !== typeof rvv) throw new Error(`"${k}.${kk}" should be a ${typeof rvv}.`);
    }
  }
  for (const [k, allowed] of Object.entries(ENUMS)) {
    if (k in obj && !allowed.includes(obj[k])) throw new Error(`"${k}" must be one of: ${allowed.map((a) => JSON.stringify(a)).join(', ')}.`);
  }
  return obj;
}

function highlight(text) {
  const out = [];
  const re = /("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?)/g;
  let last = 0, m, i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) {
      out.push(<span key={i++} style={{ color: m[2] ? 'var(--color-accent-700)' : 'var(--color-accent-2-700)' }}>{m[1]}</span>);
      if (m[2]) out.push(m[2]);
    } else out.push(<span key={i++} style={{ color: 'var(--color-neutral-700)', fontWeight: 600 }}>{m[0]}</span>);
    last = re.lastIndex;
  }
  out.push(text.slice(last));
  return out;
}

export default function SettingsPanel({ value, defaults, meta, onChange, showRawConfig, projectName }) {
  const [view, setView] = useState('form');
  const [advanced, setAdvanced] = useState(false);
  const [raw, setRaw] = useState(null);
  const [rawError, setRawError] = useState(null);
  const preRef = useRef(null);
  const styles = useAsync(listStyles, []);
  const chars = useAsync(listCharacters, []);
  const config = useAsync(() => (showRawConfig && projectName ? getConfig(projectName) : Promise.resolve(null)), [showRawConfig, projectName]);

  if (!value) return <div className="rs-muted">Loading settings…</div>;
  const M = meta || {};
  const v = value;
  const set = (path, val) => onChange(setIn(v, path, val));
  const changed = (path) => !!defaults && !same(getIn(v, path), getIn(defaults, path));
  const changedCount = defaults ? ALL_PATHS.filter(changed).length : 0;

  const Head = ({ path, label, help }) => (
    <div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontWeight: 600 }}>
        {label}
        {changed(path) && <><span style={DOT} /><button type="button" className="btn btn-ghost" style={{ fontSize: 12, padding: '2px 5px' }} onClick={() => set(path, getIn(defaults, path))}>Reset</button></>}
      </div>
      <div style={{ fontSize: 13, color: MUTED }}>{help}</div>
    </div>
  );

  const skills = M.story_skills?.length ? M.story_skills : FALLBACK_SKILLS;
  const modes = M.source_modes?.length ? M.source_modes : FALLBACK_MODES;
  const artDisabled = v.source_mode !== 'content';
  const styleList = styles.data || M.styles || [];
  const charList = chars.data || M.characters || [];
  const card = (sel) => ({ position: 'relative', display: 'flex', flexDirection: 'column', gap: 4, padding: 15, borderRadius: 'var(--radius-md)', cursor: 'pointer', background: 'var(--color-surface)', outline: sel ? '2px solid var(--color-accent)' : '1px solid transparent', outlineOffset: -1 });

  const claudeModels = withCurrent(M.claude_models, v.claude_model);
  const selectFields = [
    ['claude_model', 'Claude model', 'Writes the story, plans the shots and directs.', claudeModels],
    ['reviewer_model', 'Critic / reviewer model', 'Reviews the narration and every shot.', withCurrent(M.claude_models, v.reviewer_model)],
    ['image_model', 'Image model', 'Model used by the image maker.', withCurrent(M.image_models, v.image_model)],
    ['veo_model', 'Video model', 'Veo model for video clips.', withCurrent(M.veo_models, v.veo_model)],
  ];
  const other = v.image_provider === 'codex' ? 'gemini' : 'codex';
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const segFields = [
    ['image_provider', 'Image maker', 'Codex uses the ChatGPT subscription: $0 per image, but slower.', (M.image_providers || ['gemini', 'codex']).map((p) => [p, cap(p)])],
    ['image_fallback_provider', 'Backup image maker', 'Used if the image maker fails.', [['', 'None'], [other, cap(other)]]],
    ['veo_resolution', 'Video resolution', '1080p costs more and takes longer.', (M.veo_resolutions || ['720p', '1080p']).map((r) => [r, r])],
  ];
  const pickSeg = (key, val) => {
    let next = setIn(v, key, val);
    if (key === 'image_provider' && next.image_fallback_provider === val) next = { ...next, image_fallback_provider: '' };
    onChange(next);
  };

  // — raw view —
  const rawText = raw != null ? raw : JSON.stringify(v, null, 2);
  const onRawChange = (e) => {
    const t = e.target.value;
    setRaw(t);
    try { parseRaw(t, defaults || v); setRawError(null); } catch (x) { setRawError(x.message); }
  };
  const applyRaw = () => {
    try { onChange(parseRaw(raw, defaults || v)); setRaw(null); setRawError(null); } catch (x) { setRawError(x.message); }
  };
  const usage = config.data?.usage || null;
  const attempts = Object.entries(usage?.attempts_used || {});
  const configPath = projectName ? `projects/${projectName}/ui_settings.json` : 'ui/data/defaults.json';

  const isRaw = showRawConfig && view === 'raw';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 40, maxWidth: 1000, color: 'var(--color-text)', fontFamily: 'var(--font-body)' }}>
      {showRawConfig && (
        <div style={{ display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="seg">
            <label className="seg-opt"><input type="radio" name="sp-view" checked={!isRaw} onChange={() => { setView('form'); setRaw(null); setRawError(null); }} /><i className="ph-duotone ph-sliders-horizontal" />Form</label>
            <label className="seg-opt"><input type="radio" name="sp-view" checked={isRaw} onChange={() => setView('raw')} /><i className="ph-duotone ph-brackets-curly" />Raw config</label>
          </div>
          {defaults && <span style={{ fontSize: 13, color: 'color-mix(in srgb,var(--color-text) 60%,transparent)', display: 'flex', alignItems: 'center', gap: 6 }}><span style={DOT} />{changedCount} {changedCount === 1 ? 'value differs' : 'values differ'} from Defaults</span>}
        </div>
      )}

      {!isRaw && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 40 }}>
          <section style={{ display: 'flex', flexDirection: 'column', gap: 30 }}>
            <h6 style={H6}>Content &amp; story</h6>

            <div style={ROW}>
              <Head path="source_mode" label="Where the pictures come from" help="Keep the book's own illustrations, or have new ones drawn from the text." />
              <div className="seg" style={{ alignSelf: 'start', flexDirection: 'column', width: 'fit-content' }}>
                {modes.map((m, i) => (
                  <label key={m.id} className="seg-opt" style={i ? { borderLeft: 0, borderTop: '1px solid var(--color-divider)' } : undefined}>
                    <input type="radio" name="sp-pics" checked={v.source_mode === m.id} onChange={() => set('source_mode', m.id)} />{m.label}
                  </label>
                ))}
              </div>
            </div>

            <div style={ROW}>
              <Head path="story_skill" label="Story style" help="How the narration is written and how long it runs." />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(210px,1fr))', gap: 15 }}>
                {skills.map((s) => (
                  <label key={s.id} style={card(v.story_skill === s.id)}>
                    <input type="radio" name="sp-story" checked={v.story_skill === s.id} onChange={() => set('story_skill', s.id)} style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }} />
                    <span style={{ fontWeight: 600, fontSize: 16 }}>{s.label}</span>
                    <span style={{ fontSize: 13 }}>{range(s.words)} words · {range(s.seconds)} s</span>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: 'color-mix(in srgb,var(--color-text) 55%,transparent)' }}>{s.id}</span>
                  </label>
                ))}
              </div>
            </div>

            <div style={ROW}>
              <Head path="style" label="Art style" help="From the Style library." />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {artDisabled && <div style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center', color: 'color-mix(in srgb,var(--color-text) 70%,transparent)' }}><i className="ph-duotone ph-lock-simple" />Art style is only used when pictures are invented. The screenshot's own artwork is used instead.</div>}
                <div style={{ opacity: artDisabled ? 0.45 : 1, pointerEvents: artDisabled ? 'none' : 'auto' }} aria-disabled={artDisabled}>
                  <StylePicker value={v.style} onChange={(id) => set('style', id)} styles={styleList} />
                </div>
              </div>
            </div>

            <div style={ROW}>
              <Head path="pause_after_narration" label="Pause after narration" help="Stop after step 2 so you can read and edit the narration. Same as the button on the Run screen." />
              <button type="button" role="switch" aria-checked={!!v.pause_after_narration} onClick={() => set('pause_after_narration', !v.pause_after_narration)}
                style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'none', border: 0, padding: 0, cursor: 'pointer', font: 'inherit', color: 'inherit', alignSelf: 'start' }}>
                <span style={{ width: 40, height: 22, borderRadius: 11, position: 'relative', background: v.pause_after_narration ? 'var(--color-accent)' : 'var(--color-neutral-400)', transition: 'background .15s' }}>
                  <span style={{ position: 'absolute', top: 3, left: v.pause_after_narration ? 21 : 3, width: 16, height: 16, borderRadius: '50%', background: 'var(--color-bg)', boxShadow: 'var(--shadow-sm)', transition: 'left .15s' }} />
                </span>
                <span style={{ fontSize: 14 }}>{v.pause_after_narration ? 'On — run stops after step 2' : 'Off — run goes straight through'}</span>
              </button>
            </div>

            <div style={ROW}>
              <Head path="character_id" label="Character" help="One character per reel, from the Character pool." />
              <CharacterPicker value={v.character_id} onChange={(id) => set('character_id', id)} characters={charList} />
            </div>

            <div style={ROW}>
              <Head path="voice" label="Narrator voice" help="Gemini text-to-speech voice." />
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', maxWidth: 360 }}>
                <select className="input" value={v.voice || ''} onChange={(e) => set('voice', e.target.value)}>
                  {withCurrent(M.voices, v.voice).map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
                <button type="button" className="btn btn-secondary" disabled title="Voice samples are not available yet" style={{ whiteSpace: 'nowrap' }}><i className="ph-duotone ph-play" />Play sample</button>
              </div>
            </div>
          </section>

          <section style={{ display: 'flex', flexDirection: 'column', gap: 30 }}>
            <h6 style={H6}>Models</h6>
            {selectFields.map(([key, label, help, options]) => (
              <div key={key} style={ROW}>
                <Head path={key} label={label} help={help} />
                <select className="input" style={{ maxWidth: 360, alignSelf: 'start' }} value={v[key] || ''} onChange={(e) => set(key, e.target.value)}>
                  {options.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </div>
            ))}
            {segFields.map(([key, label, help, options]) => (
              <div key={key} style={ROW}>
                <Head path={key} label={label} help={help} />
                <div className="seg" style={{ alignSelf: 'start' }}>
                  {options.map(([val, lab]) => (
                    <label key={val || 'none'} className="seg-opt"><input type="radio" name={'sp-' + key} checked={(v[key] ?? '') === val} onChange={() => pickSeg(key, val)} />{lab}</label>
                  ))}
                </div>
              </div>
            ))}
          </section>

          <div style={{ display: 'flex' }}>
            <button type="button" className="btn btn-ghost" onClick={() => setAdvanced((a) => !a)}>
              <i className={advanced ? 'ph-duotone ph-caret-up' : 'ph-duotone ph-caret-down'} />
              {advanced ? 'Hide advanced settings' : 'Advanced: budget, attempt limits, speed, cost estimates'}
            </button>
          </div>

          {advanced && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 40 }}>
              {NUM_GROUPS.map((g) => (
                <section key={g.title} style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                  <div>
                    <h6 style={H6}>{g.title}</h6>
                    <div style={{ fontSize: 13, color: MUTED }}>{g.help}</div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(270px,1fr))', gap: '20px 40px' }}>
                    {g.fields.map(([path, label, o = {}]) => (
                      <div key={path} className="field">
                        <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                          {label}
                          {changed(path) && <><span style={DOT} /><button type="button" className="btn btn-ghost" style={{ fontSize: 11, padding: '0 4px' }} onClick={() => set(path, getIn(defaults, path))}>Reset</button></>}
                        </label>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{ fontSize: 13, minWidth: 10, color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' }}>{o.prefix || ''}</span>
                          <input className="input" type="number" min="0" step={o.step || 1} value={getIn(v, path) ?? ''} onChange={(e) => { const n = parseFloat(e.target.value); set(path, Number.isNaN(n) ? 0 : n); }} style={{ maxWidth: 130 }} />
                          <span style={{ fontSize: 13, color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' }}>{o.unit || ''}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>
      )}

      {isRaw && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontFamily: MONO, fontSize: 13 }}>{configPath}</span>
            <span style={{ flex: 1 }} />
            {defaults && <button type="button" className="btn btn-secondary" onClick={() => { onChange(defaults); setRaw(null); setRawError(null); }}><i className="ph-duotone ph-arrow-counter-clockwise" />Reset to defaults</button>}
            <button type="button" className="btn btn-primary" disabled={!!rawError || raw == null} onClick={applyRaw}>Apply</button>
          </div>
          <div style={{ position: 'relative', height: 460, background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
            <pre ref={preRef} aria-hidden="true" style={{ position: 'absolute', inset: 0, margin: 0, padding: '15px 20px', fontFamily: MONO, fontSize: 13, lineHeight: '20px', whiteSpace: 'pre', overflow: 'hidden', tabSize: 2, pointerEvents: 'none', color: 'var(--color-text)' }}>{highlight(rawText + '\n')}</pre>
            <textarea spellCheck="false" value={rawText} onChange={onRawChange} aria-label="Raw config JSON"
              onScroll={(e) => { if (preRef.current) { preRef.current.scrollTop = e.target.scrollTop; preRef.current.scrollLeft = e.target.scrollLeft; } }}
              style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', resize: 'none', border: 0, outline: 'none', background: 'transparent', color: 'transparent', caretColor: 'var(--color-text)', fontFamily: MONO, fontSize: 13, lineHeight: '20px', padding: '15px 20px', whiteSpace: 'pre', overflow: 'auto', tabSize: 2, boxSizing: 'border-box' }} />
          </div>
          {rawError
            ? <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, color: 'var(--color-accent-2-700)' }}><i className="ph-duotone ph-x-circle" />{rawError} Apply is off until this is fixed.</div>
            : <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, color: 'color-mix(in srgb,var(--color-text) 65%,transparent)' }}><i className="ph-duotone ph-check-circle" />Valid. Form and raw config stay in sync.</div>}

          {projectName && (
            <section style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 20 }}>
              <h6 style={H6}>Usage so far</h6>
              {!usage
                ? <div style={{ fontSize: 14, color: MUTED }}>{config.loading ? 'Loading…' : 'No runs yet.'}</div>
                : <>
                  <div style={{ fontSize: 14 }}>${Number(usage.budget_spent_usd || 0).toFixed(2)} spent of ${Number(v.max_budget_usd || 0).toFixed(2)} budget</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(230px,1fr))', gap: '4px 30px', fontFamily: MONO, fontSize: 12 }}>
                    {attempts.map(([k, n]) => <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}><span>{k}</span><span>{n}</span></div>)}
                  </div>
                </>}
            </section>
          )}
        </div>
      )}
    </div>
  );
}
