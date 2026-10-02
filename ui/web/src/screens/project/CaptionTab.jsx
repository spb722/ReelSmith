// Caption tab: write the Instagram caption, edit it by hand, revise with feedback, version history. Props: { name }
import { useEffect, useState } from 'react';
import { getCaption, runCaption, reviseCaption, saveCaption, restoreCaption } from '../../api.js';
import { usePoll } from '../../hooks.js';
import { useToast } from '../../components/Toast.jsx';
import { muted, kicker, Empty, LoadState } from '../../components/review/Media.jsx';

// Instagram's limits; the server re-checks the saved caption (orchestrator/contracts/caption.py).
const MAX_CHARS = 2200;
const MAX_HOOK = 125;
const TAGS = 5;
const CHANGE = { fresh: 'Fresh caption', feedback: 'Feedback', edit: 'Edited by hand', restore: 'Restored' };

function Count({ label, value, max, exact }) {
  const bad = exact ? value !== max : value > max;
  return (
    <div>
      <div style={{ fontSize: 12, ...muted }}>{label}</div>
      <div className={bad ? 'rs-danger-text' : ''} style={{ fontVariantNumeric: 'tabular-nums' }}>{value} / {max}</div>
    </div>
  );
}

export default function CaptionTab({ name }) {
  const toast = useToast();
  const [pollMs, setPollMs] = useState(0);
  const { data, error, loading, reload } = usePoll(() => getCaption(name), pollMs, [name]);
  const [draft, setDraft] = useState(null);
  const [feedback, setFeedback] = useState('');
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);

  // Poll every 1.5 s while a caption run is going.
  const running = data?.status?.state === 'running';
  useEffect(() => { setPollMs(running ? 1500 : 0); }, [running]);
  // A new saved caption replaces the editor's text (hand edits are saved before anything else can change it).
  useEffect(() => { setDraft(null); }, [data?.text]);

  if (!data) return <LoadState loading={loading} error={error} />;
  if (!data.enabled && !data.exists) {
    return (
      <Empty icon="ph-text-align-left" title="The caption comes after the narration" maxWidth={540}>
        The caption is written from the reel’s narration, so this tab opens once a run has written it.
      </Empty>
    );
  }

  const text = draft ?? data.text;
  const dirty = draft !== null && draft.trim() !== data.text;
  const hook = text.split('\n')[0] || '';
  const tags = (text.match(/#[^\s#]+/g) || []).length;
  const status = data.status || {};
  const pct = Math.floor(Number(status.percent) || 0);
  const log = (status.log_tail || []).slice(-6);
  const versions = data.versions || [];
  const previewV = versions.find((v) => v.version === preview);

  const act = async (fn, done) => {
    setBusy(true);
    try { await fn(); await reload(); if (done) toast(done); return true; }
    catch (e) { toast(e.message, 'error'); return false; }
    finally { setBusy(false); }
  };
  const write = () => act(() => runCaption(name, { force: data.exists || undefined }));
  const revise = () => act(() => reviseCaption(name, feedback.trim())).then((ok) => ok && setFeedback(''));
  const save = () => act(() => saveCaption(name, draft), 'Caption saved');
  const restore = (v) => act(() => restoreCaption(name, v), `Using caption v${v}`).then((ok) => ok && setPreview(null));
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); toast('Caption copied'); }
    catch { toast('Could not copy. Select the text and copy it yourself.', 'error'); }
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px,310px) minmax(0,1fr)', gap: 55, alignItems: 'start' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 25 }}>
        <h6 style={{ margin: 0, ...muted }}>Instagram caption</h6>
        <p style={{ margin: 0, fontSize: 14, lineHeight: 1.5 }}>
          Claude writes the caption and 5 hashtags from the reel’s narration. You can edit it by hand, or tell Claude what to change.
        </p>
        <button className="btn btn-primary" onClick={write} disabled={busy || running || dirty}>
          <i className="ph-duotone ph-sparkle" />{data.exists ? 'Write a fresh caption' : 'Write caption'}
        </button>
        {running && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}><span>Writing the caption</span><span>{pct}%</span></div>
            <div style={{ height: 4, background: 'var(--color-neutral-200)' }}><div style={{ height: '100%', width: `${pct}%`, background: 'var(--color-accent)' }} /></div>
          </div>
        )}
        {!running && status.stop_reason && <div className="rs-danger-text" style={{ fontSize: 13 }}>{status.stop_reason}</div>}
        {log.length > 0 && (
          <div style={{ fontFamily: 'ui-monospace,Menlo,Consolas,monospace', fontSize: 11, lineHeight: 1.7, color: 'color-mix(in srgb,var(--color-text) 70%,transparent)', overflowWrap: 'anywhere' }}>
            {log.map((l, i) => <div key={i}>{l}</div>)}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 35, minWidth: 0 }}>
        {!data.exists ? (
          <div style={{ paddingTop: 10 }}>
            <Empty icon="ph-text-align-left" title="No caption yet" maxWidth={480}>
              Press Write caption. You’ll get the caption and hashtags ready to paste into Instagram.
            </Empty>
          </div>
        ) : (
          <>
            <div className="field" style={{ maxWidth: 680 }}>
              <label>Caption</label>
              <textarea className="input" style={{ minHeight: 420, lineHeight: 1.55, fontFamily: 'inherit' }} value={text}
                disabled={running} onChange={(e) => setDraft(e.target.value)} />
            </div>
            <div style={{ display: 'flex', gap: 30, flexWrap: 'wrap', fontSize: 15 }}>
              <Count label="Characters" value={text.trim().length} max={MAX_CHARS} />
              <Count label="First line" value={hook.length} max={MAX_HOOK} />
              <Count label="Hashtags" value={tags} max={TAGS} exact />
            </div>
            <div style={{ maxWidth: 680 }}>
              <div style={{ fontSize: 12, ...muted }}>What people see before “…more”</div>
              <div style={{ fontStyle: 'italic' }}>{text.slice(0, MAX_HOOK)}{text.length > MAX_HOOK ? '…' : ''}</div>
            </div>
            {!dirty && data.problems?.length > 0 && (
              <div className="rs-danger-text" style={{ fontSize: 13, maxWidth: 680 }}>
                {data.problems.map((p) => <div key={p}>· {p}</div>)}
              </div>
            )}
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <button className="btn btn-primary" onClick={copy}><i className="ph-duotone ph-copy" />Copy caption</button>
              <button className="btn btn-secondary" onClick={save} disabled={busy || running || !dirty}><i className="ph-duotone ph-floppy-disk" />Save edits</button>
              {dirty && <button className="btn btn-ghost" onClick={() => setDraft(null)} disabled={busy}>Discard edits</button>}
            </div>
            <div className="field" style={{ maxWidth: 680 }}>
              <label>What should change?</label>
              <textarea className="input" placeholder="e.g. make the hook shorter and use a friendlier question" value={feedback} onChange={(e) => setFeedback(e.target.value)} />
              <button className="btn btn-primary" style={{ marginTop: 10, alignSelf: 'flex-start' }} onClick={revise} disabled={busy || running || dirty || !feedback.trim()}>
                <i className="ph-duotone ph-arrows-clockwise" />Revise caption
              </button>
              {dirty && <span style={{ fontSize: 12, marginTop: 6, ...muted }}>Save or discard your edits first.</span>}
            </div>
            {versions.length > 1 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 680 }}>
                <div style={kicker}>Version history</div>
                {versions.map((v) => (
                  <div key={v.version} style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap', fontSize: 14 }}>
                    <span style={{ fontWeight: 600 }}>v{v.version}</span>
                    {v.current && <span className="tag tag-accent">Current</span>}
                    <span style={muted}>{CHANGE[v.change] || 'First caption'}{v.feedback ? `: “${v.feedback}”` : ''}</span>
                    {!v.current && <button className="btn btn-ghost" onClick={() => setPreview(v.version)} style={{ fontSize: 12, paddingLeft: 0 }}>View</button>}
                  </div>
                ))}
              </div>
            )}
            {previewV && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 15, padding: 25, borderRadius: 'var(--radius-lg)', background: 'var(--color-surface)', maxWidth: 680 }}>
                <span style={{ fontSize: 14, fontWeight: 600 }}>v{previewV.version}</span>
                <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.55, fontSize: 14 }}>{previewV.text}</div>
                <div style={{ display: 'flex', gap: 10 }}>
                  <button className="btn btn-primary" onClick={() => restore(previewV.version)} disabled={busy || running || dirty}>Use this version</button>
                  <button className="btn btn-secondary" onClick={() => setPreview(null)}>Close</button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
