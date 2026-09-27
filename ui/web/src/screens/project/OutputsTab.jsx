// Outputs tab: file list on the left, preview on the right. Props: { name }
import { useState } from 'react';
import { getOutputs } from '../../api.js';
import { useAsync } from '../../hooks.js';
import { Media, muted, Empty, LoadState } from '../../components/review/Media.jsx';

const base = (url) => decodeURIComponent(String(url || '').split('?')[0].split('/').pop() || '');
const human = (k) => { const s = String(k).replace(/_/g, ' '); return s.charAt(0).toUpperCase() + s.slice(1); };
const isPlain = (v) => v !== null && v !== undefined && typeof v !== 'object';

function fmtTime(t) {
  const n = Number(t) || 0;
  const m = Math.floor(n / 60), s = n - m * 60;
  return `${m}:${s.toFixed(3).padStart(6, '0')}`;
}

function fmtAt(at) {
  if (!at) return '';
  const d = new Date(at);
  return isNaN(d) ? String(at) : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// Readable rows for a plan object: { title, sub, line, prompt }.
function planRows(obj, kind) {
  if (!obj || typeof obj !== 'object') return [];
  if (kind === 'shots') {
    const shots = Array.isArray(obj) ? obj : obj.shots || [];
    return shots.map((s, i) => {
      const n = s.sequence ?? s.number ?? s.n ?? i + 1;
      const mode = String(s.generation_mode || s.kind || s.type || '').toLowerCase();
      const secs = s.end_seconds != null && s.start_seconds != null ? (s.end_seconds - s.start_seconds).toFixed(1) : s.seconds;
      return {
        title: `Shot ${n}`,
        sub: [mode ? (mode.startsWith('vid') ? 'Video clip' : 'Still') : '', secs ? `${secs} s` : ''].filter(Boolean).join(' · '),
        line: s.narration ? `“${s.narration}”` : s.shot_goal || s.text_overlay || '',
        prompt: s.prompt || s.frame_composition || s.motion_plan || '',
      };
    });
  }
  const rows = [];
  for (const [k, v] of Object.entries(obj)) {
    if (isPlain(v)) {
      if (String(v).length) rows.push({ title: human(k), sub: '', line: String(v), prompt: '' });
    } else if (Array.isArray(v)) {
      v.forEach((it, i) => {
        if (isPlain(it)) rows.push({ title: i === 0 ? human(k) : '', sub: '', line: String(it), prompt: '' });
        else if (it && typeof it === 'object') {
          rows.push({
            title: `${human(k).replace(/s$/, '')} ${it.sequence ?? i + 1}`,
            sub: [it.role, it.estimated_duration_seconds ? `${it.estimated_duration_seconds} s` : ''].filter(Boolean).map(String).join(' · '),
            line: it.narration ? `“${it.narration}”` : String(Object.values(it).find(isPlain) ?? ''),
            prompt: it.visual_intent || '',
          });
        }
      });
    } else if (v && typeof v === 'object') {
      for (const [k2, v2] of Object.entries(v)) if (isPlain(v2)) rows.push({ title: human(k2), sub: human(k), line: String(v2), prompt: '' });
    }
  }
  return rows;
}

function buildGroups(d, name) {
  const groups = [];
  const add = (title, files) => { if (files.length) groups.push({ title, files }); };
  add('Final reel', d.reel_url ? [{ key: 'reel', name: base(d.reel_url) || 'book_reel.mp4', icon: 'ph-film-strip', kind: 'media', url: d.reel_url, meta: [{ k: 'File', v: 'The finished reel, 9:16, with narration and subtitles' }] }] : []);
  const narr = [];
  if (d.audio_url) narr.push({ key: 'audio', name: base(d.audio_url) || 'narration.wav', icon: 'ph-waveform', kind: 'audio', url: d.audio_url });
  if (d.script) narr.push({ key: 'script', name: 'script.txt', icon: 'ph-file-text', kind: 'text' });
  if (d.subtitles && d.subtitles.length) narr.push({ key: 'subs', name: 'subtitles', icon: 'ph-file-text', kind: 'subs' });
  add('Narration', narr);
  add('Still pictures', (d.stills || []).map((f) => ({ key: 'still:' + f.name, name: f.name, icon: 'ph-image', kind: 'media', url: f.url, meta: [{ k: 'Shot', v: 'Still picture' }] })));
  add('Video clips & seed images', [
    ...(d.clips || []).map((f) => ({ key: 'clip:' + f.name, name: f.name, icon: 'ph-film-strip', kind: 'media', url: f.url, previews: f.previews || [], meta: [{ k: 'Shot', v: 'Video clip' }] })),
    ...(d.seeds || []).map((f) => ({ key: 'seed:' + f.name, name: f.name, icon: 'ph-image', kind: 'media', url: f.url, meta: [{ k: 'Shot', v: 'Seed image for the clip' }] })),
  ]);
  const plans = [];
  if (d.story_plan) plans.push({ key: 'story', name: 'story_plan.json', icon: 'ph-brackets-curly', kind: 'json', json: d.story_plan, plan: 'story' });
  if (d.visual_plan) plans.push({ key: 'shots', name: 'shot_plan.json', icon: 'ph-brackets-curly', kind: 'json', json: d.visual_plan, plan: 'shots' });
  add('Plans', plans);
  const logs = [...(d.logs || [])].sort((a, b) => String(b.at || b.name).localeCompare(String(a.at || a.name)));
  add('Run logs', logs.map((l) => ({ key: 'log:' + l.name, name: l.name, icon: 'ph-terminal-window', kind: 'log', url: l.url, size: fmtAt(l.at) })));
  return groups;
}

function displayPath(f, name) {
  if (f.url) {
    const prefix = `/files/${encodeURIComponent(name)}/`;
    if (f.url.startsWith(prefix)) return `projects/${name}/` + decodeURIComponent(f.url.slice(prefix.length));
    return f.url;
  }
  return f.name;
}

function Preview({ f, d, raw }) {
  if (f.kind === 'media') {
    return (
      <div style={{ display: 'flex', gap: 30, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <Media url={f.url} label={f.name} width={300} controls />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 14, maxWidth: 360 }}>
          {(f.meta || []).map((m) => <div key={m.k}><div style={{ fontSize: 12, ...muted }}>{m.k}</div><div>{m.v}</div></div>)}
          {f.previews && f.previews.length > 0 && (
            <div>
              <div style={{ fontSize: 12, marginBottom: 6, ...muted }}>Preview frames</div>
              <div style={{ display: 'flex', gap: 8 }}>{f.previews.map((u) => <Media key={u} url={u} width={70} />)}</div>
            </div>
          )}
        </div>
      </div>
    );
  }
  if (f.kind === 'audio') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
        <audio src={f.url} controls style={{ width: '100%', maxWidth: 760 }} />
        {d.script && <p style={{ margin: 0, fontSize: 18, lineHeight: 1.6, maxWidth: 760, whiteSpace: 'pre-wrap' }}>{d.script}</p>}
        <Subs list={d.subtitles} size={12} />
      </div>
    );
  }
  if (f.kind === 'text') return <p style={{ margin: 0, fontSize: 18, lineHeight: 1.6, maxWidth: 760, whiteSpace: 'pre-wrap' }}>{d.script}</p>;
  if (f.kind === 'subs') return <Subs list={d.subtitles} size={13} />;
  if (f.kind === 'json' && !raw) {
    const rows = planRows(f.json, f.plan);
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20, maxWidth: 880 }}>
        {rows.map((row, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: '120px minmax(0,1fr)', gap: 20 }}>
            <div><div style={{ fontWeight: 600 }}>{row.title}</div><div style={{ fontSize: 13, ...muted }}>{row.sub}</div></div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ fontSize: 16, whiteSpace: 'pre-wrap' }}>{row.line}</div>
              {row.prompt && <div style={{ fontSize: 13, color: 'color-mix(in srgb,var(--color-text) 65%,transparent)' }}>{row.prompt}</div>}
            </div>
          </div>
        ))}
        {!rows.length && <div style={muted}>Nothing readable in this file — switch to Raw JSON.</div>}
      </div>
    );
  }
  if (f.kind === 'json') {
    const text = typeof f.json === 'string' ? f.json : JSON.stringify(f.json, null, 2);
    return <pre style={{ margin: 0, padding: '18px 20px', background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', fontFamily: 'ui-monospace,Menlo,Consolas,monospace', fontSize: 12, lineHeight: 1.7, overflow: 'auto', maxHeight: 560, whiteSpace: 'pre-wrap' }}>{text}</pre>;
  }
  return null;
}

function Subs({ list, size }) {
  if (!list || !list.length) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, fontFamily: 'ui-monospace,Menlo,Consolas,monospace', fontSize: size }}>
      {list.map((s, i) => <div key={i}>{fmtTime(s.start)}  →  {s.text}</div>)}
    </div>
  );
}

export default function OutputsTab({ name }) {
  const { data, error, loading } = useAsync(() => getOutputs(name), [name]);
  const [selKey, setSelKey] = useState(null);
  const [raw, setRaw] = useState(false);
  if (!data) return <LoadState loading={loading} error={error} />;

  const groups = buildGroups(data, name);
  if (!groups.length) {
    return (
      <Empty icon="ph-folder-dashed" title="Nothing made yet">
        Files appear here as each step finishes: the story plan, the shot plan, narration audio, pictures, clips and the final reel.
      </Empty>
    );
  }
  const all = groups.flatMap((g) => g.files).filter((f) => f.kind !== 'log');
  const sel = all.find((f) => f.key === selKey) || all[0];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px,280px) minmax(0,1fr)', gap: 45, alignItems: 'start' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
        {groups.map((g) => (
          <div key={g.title} style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            <div style={{ fontSize: 12, letterSpacing: '.08em', textTransform: 'uppercase', marginBottom: 5, ...muted }}>{g.title}</div>
            {g.files.map((f) => {
              const on = sel && f.key === sel.key;
              const style = { display: 'flex', gap: 10, alignItems: 'center', padding: '6px 10px', border: 0, borderRadius: 'var(--radius-md)', font: 'inherit', fontSize: 14, cursor: 'pointer', textAlign: 'left', textDecoration: 'none', color: on ? 'var(--color-accent-800)' : 'inherit', background: on ? 'var(--color-accent-100)' : 'transparent' };
              const inner = (
                <>
                  <i className={`ph-duotone ${f.icon}`} style={{ fontSize: 16 }} />
                  <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</span>
                  {f.size && <span style={{ fontSize: 11, opacity: 0.65 }}>{f.size}</span>}
                  {f.kind === 'log' && <i className="ph-duotone ph-arrow-square-out" style={{ fontSize: 13, opacity: 0.65 }} />}
                </>
              );
              return f.kind === 'log'
                ? <a key={f.key} href={f.url} target="_blank" rel="noreferrer" className="rs-hover-accent" style={style}>{inner}</a>
                : <button key={f.key} onClick={() => setSelKey(f.key)} className={on ? '' : 'rs-hover-accent'} style={style}>{inner}</button>;
            })}
          </div>
        ))}
      </div>

      {sel ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18, minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontFamily: 'ui-monospace,Menlo,Consolas,monospace', fontSize: 13, overflowWrap: 'anywhere' }}>{displayPath(sel, name)}</span>
            <span style={{ flex: 1 }} />
            {sel.kind === 'json' && (
              <div className="seg">
                <label className="seg-opt"><input type="radio" name="rs-json" checked={!raw} onChange={() => setRaw(false)} />Readable</label>
                <label className="seg-opt"><input type="radio" name="rs-json" checked={raw} onChange={() => setRaw(true)} />Raw JSON</label>
              </div>
            )}
            {sel.url && (
              <a className="btn btn-secondary" href={sel.url} target="_blank" rel="noreferrer" style={{ fontSize: 13, padding: '6px 12px' }}>
                <i className="ph-duotone ph-arrow-square-out" />Open in new tab
              </a>
            )}
            {sel.url && (sel.kind === 'media' || sel.kind === 'audio') && (
              <a className="btn btn-secondary" href={sel.url} download={sel.name} style={{ fontSize: 13, padding: '6px 12px' }}>
                <i className="ph-duotone ph-download-simple" />Download
              </a>
            )}
          </div>
          <Preview f={sel} d={data} raw={raw} />
        </div>
      ) : (
        <div style={muted}>Pick a run log on the left to open it in a new tab.</div>
      )}
    </div>
  );
}
