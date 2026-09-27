// Narration review & edit (design: RUN TAB, N.* panel; spec 5.8).
// Props: { name, waiting (run paused at the checkpoint), fromReviews (opened via #/p/:name/narration), onDone() }
import { useEffect, useState } from 'react';
import { useAsync } from '../../hooks.js';
import { getNarration, putNarration, discardNarration, restoreNarration, continueNarration, rewriteNarration } from '../../api.js';
import { navigate } from '../../router.js';
import { useToast } from '../../components/Toast.jsx';
import { MUTED } from '../../components/Stepper.jsx';

const kicker = { fontSize: 12, letterSpacing: '.08em', textTransform: 'uppercase', color: MUTED };
const countWords = (t) => (t.trim() ? t.trim().split(/\s+/).length : 0);

function wordDiff(a, b) {
  const A = a.split(/\s+/).filter(Boolean), B = b.split(/\s+/).filter(Boolean), m = A.length, n = B.length;
  const L = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = []; let i = 0, j = 0;
  while (i < m && j < n) {
    if (A[i] === B[j]) { out.push(['same', B[j]]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push(['del', A[i++]]);
    else out.push(['add', B[j++]]);
  }
  while (i < m) out.push(['del', A[i++]]);
  while (j < n) out.push(['add', B[j++]]);
  return out;
}
const DIFF_STYLE = {
  same: {},
  add: { background: 'var(--color-accent-200)', color: 'var(--color-accent-900)', borderRadius: 2, padding: '0 2px' },
  del: { textDecoration: 'line-through', color: 'var(--color-accent-2-700)' },
};

export default function NarrationEditor({ name, waiting = false, fromReviews = false, onDone }) {
  const toast = useToast();
  const { data, error, setData } = useNarration(name);
  const [text, setText] = useState('');
  const [instr, setInstr] = useState('');
  const [sel, setSel] = useState(null);
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (data) setText(data.script || ''); }, [data]);

  if (error) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
        <div className="rs-danger-text">{error.message}</div>
        {fromReviews && <button className="btn btn-secondary" onClick={() => navigate(`/p/${encodeURIComponent(name)}/reviews`)}><i className="ph-duotone ph-arrow-left" />Back to Reviews</button>}
      </div>
    );
  }
  if (!data) return <div className="rs-muted">Loading the narration…</div>;

  const run = async (fn, after) => {
    setBusy(true);
    try { const r = await fn(); if (after) after(r); } catch (e) { toast(e.message, 'error'); }
    setBusy(false);
  };
  const saveIfChanged = async () => { if (text !== data.script) setData(await putNarration(name, text)); };

  const words = countWords(text);
  const rate = data.word_count ? data.est_seconds / data.word_count : 1 / 2.45;
  const secs = Math.round(words * rate);
  const lim = data.limits || {};
  const hasLim = lim.max_words != null;
  const out = hasLim && (words < lim.min_words || words > lim.max_words || secs < lim.min_s || secs > lim.max_s);
  const history = data.history || [];
  const original = history.length ? history[0].script : data.script;
  const pristine = text === original && history.length <= 1;
  const critic = data.critic || {};
  const approved = String(critic.verdict || '').toLowerCase().startsWith('approv');
  const outline = data.outline || {};
  const selected = sel != null ? history.find((h) => h.id === sel) : null;

  const primary = waiting
    ? () => run(async () => { await saveIfChanged(); return continueNarration(name); }, () => { toast('Continuing from step 3.'); onDone && onDone(); })
    : () => run(saveIfChanged, () => navigate(`/p/${encodeURIComponent(name)}/reviews`));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 25, padding: '30px 32px', borderRadius: 'var(--radius-lg)', background: waiting ? 'color-mix(in srgb,var(--color-process-yellow) 16%,var(--color-bg))' : 'var(--color-surface)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 15 }}>
        {waiting && <span style={{ width: 12, height: 12, borderRadius: 2, background: 'var(--color-process-yellow)', marginTop: 9, flex: 'none' }} />}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={kicker}>{waiting ? 'Step 2 · Waiting for you' : 'Narration review & edit'}</div>
          <h2 style={{ margin: '4px 0 0', fontSize: 28, textWrap: 'pretty' }}>{waiting ? 'The narration is ready. Read it, edit it if you like, then continue.' : 'The narration for ' + name}</h2>
        </div>
        {fromReviews && <button className="btn btn-secondary" onClick={() => navigate(`/p/${encodeURIComponent(name)}/reviews`)}><i className="ph-duotone ph-x" />Close</button>}
      </div>

      {data.downstream_done && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 14, padding: '10px 15px', borderRadius: 'var(--radius-md)', background: 'var(--color-accent-2-100)', color: 'var(--color-accent-2-900)' }}>
          <i className="ph-duotone ph-warning" style={{ fontSize: 18 }} />Later steps already ran. If you change the narration, the shots, voice and video will be remade on the next run.
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 35, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, gridColumn: 'span 2', minWidth: 0 }}>
          <textarea className="input" value={text} onChange={(e) => setText(e.target.value)} spellCheck
            style={{ minHeight: 330, fontSize: 20, lineHeight: 1.6, padding: '20px 22px', fontFamily: 'var(--font-body)', background: 'var(--color-bg)', resize: 'vertical' }} />
          <div style={{ display: 'flex', gap: '6px 15px', alignItems: 'center', fontSize: 13, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{words} words · ~{secs} s</span>
            {hasLim && <span style={{ color: MUTED }}>Limits: {lim.min_words}–{lim.max_words} words, {lim.min_s}–{lim.max_s} s</span>}
            {out && <span style={{ color: 'var(--color-accent-2-700)', display: 'flex', gap: 5, alignItems: 'center' }}><i className="ph-duotone ph-warning" />Outside the style’s word range. The pipeline won’t accept it, so saving will be refused.</span>}
            <span style={{ flex: 1 }} />
            <button className="btn btn-ghost" disabled={busy || text === data.script} onClick={() => run(() => putNarration(name, text), (r) => { setData(r); setSel(null); })} style={{ fontSize: 13 }}>
              <i className="ph-duotone ph-floppy-disk" />Save edit
            </button>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 28, minWidth: 0 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={kicker}>Critic’s verdict</div>
            <div>
              <span className="tag" style={approved ? { background: 'var(--color-text)', color: 'var(--color-bg)' } : { background: 'var(--color-accent-2-100)', color: 'var(--color-accent-2-800)' }}>
                {critic.verdict ? (approved ? 'Approved' : 'Rejected') : 'No verdict'}
              </span>
            </div>
            {(critic.issues || []).map((t, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, fontSize: 14, lineHeight: 1.45 }}>
                <i className={'ph-duotone ' + (approved ? 'ph-info' : 'ph-x-circle')} style={{ marginTop: 3, flex: 'none' }} /><span>{t}</span>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <button className="btn btn-ghost" onClick={() => setOutlineOpen(!outlineOpen)} style={{ alignSelf: 'flex-start', paddingLeft: 0, color: 'var(--color-text)', fontSize: 14 }}>
              <i className={'ph-duotone ' + (outlineOpen ? 'ph-caret-down' : 'ph-caret-right')} />Story outline
            </button>
            {outlineOpen && [['Hook', outline.hook], ['Main point', outline.main_point], ['Ending', outline.ending]].map(([k, v]) => (
              <div key={k} style={{ fontSize: 14, lineHeight: 1.45 }}><span style={{ fontWeight: 600 }}>{k}.</span> {v || '—'}</div>
            ))}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={kicker}>Edit history</div>
            {history.length === 0 && <span style={{ fontSize: 13, color: MUTED }}>No saved edits yet.</span>}
            {history.map((h, i) => (
              <button key={h.id} onClick={() => setSel(sel === h.id ? null : h.id)} className="rs-hover-accent"
                style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '7px 10px', border: 0, borderRadius: 'var(--radius-md)', font: 'inherit', fontSize: 14, cursor: 'pointer', textAlign: 'left', color: 'inherit', background: sel === h.id ? 'var(--color-accent-100)' : 'transparent' }}>
                <i className={'ph-duotone ' + (i === 0 ? 'ph-robot' : /^rewrite/i.test(h.label) ? 'ph-magic-wand' : 'ph-pencil-simple')} />
                <span style={{ flex: 1 }}>{h.label}</span>
                <span style={{ fontSize: 12, opacity: 0.7 }}>{countWords(h.script || '')} words</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {selected && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 14, fontWeight: 600 }}>Compared with “{selected.label}”</span>
            <span style={{ fontSize: 12, color: MUTED }}>Highlighted: added now · struck through: in that version only</span>
            <span style={{ flex: 1 }} />
            <button className="btn btn-secondary" disabled={busy} onClick={() => run(() => restoreNarration(name, selected.id), (r) => { setData(r); setSel(null); })}>
              <i className="ph-duotone ph-arrow-counter-clockwise" />Restore this version
            </button>
          </div>
          <p style={{ fontSize: 16, lineHeight: 1.7, margin: 0, maxWidth: 900 }}>
            {wordDiff(selected.script || '', text).map(([k, w], i) => <span key={i}><span style={DIFF_STYLE[k]}>{w}</span> </span>)}
          </p>
        </div>
      )}

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-primary" disabled={busy} onClick={primary} style={{ fontSize: 15, padding: '11px 18px' }}>
          <i className="ph-duotone ph-arrow-right" />{waiting ? 'Continue with this narration' : 'Save narration'}
        </button>
        {waiting && (
          <div style={{ display: 'flex', gap: 8, flex: 1, minWidth: 300 }}>
            <input className="input" placeholder="Rewrite with instructions, e.g. make the ending punchier" value={instr} onChange={(e) => setInstr(e.target.value)} style={{ background: 'var(--color-bg)' }} />
            <button className="btn btn-secondary" disabled={busy || !instr.trim()} style={{ whiteSpace: 'nowrap' }}
              onClick={() => run(async () => { await saveIfChanged(); return rewriteNarration(name, instr.trim()); }, () => { setInstr(''); toast('Sent back to Claude. The run will pause again with the new version.'); onDone && onDone(); })}>
              <i className="ph-duotone ph-magic-wand" />Rewrite
            </button>
          </div>
        )}
        <button className="btn btn-ghost" disabled={busy || pristine} onClick={() => run(() => discardNarration(name), (r) => { setData(r); setSel(null); })}>Discard my edits</button>
      </div>
    </div>
  );
}

// useAsync plus a way to replace the data with a server response.
function useNarration(name) {
  const res = useAsync(() => getNarration(name), [name]);
  const [override, setOverride] = useState(null);
  useEffect(() => { setOverride(null); }, [res.data]);
  return { data: override || res.data, error: res.error, setData: setOverride };
}
