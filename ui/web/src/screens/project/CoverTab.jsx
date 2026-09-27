// Cover tab: settings, result in feed/grid crops, feedback → revise, version history. Props: { name }
import { useEffect, useState } from 'react';
import { getCover, getSettings, runCover, reviseCover, restoreCover } from '../../api.js';
import { useAsync, usePoll } from '../../hooks.js';
import { useToast } from '../../components/Toast.jsx';
import { Media, muted, kicker, Empty, LoadState } from '../../components/review/Media.jsx';

const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : '');

export default function CoverTab({ name }) {
  const toast = useToast();
  const [pollMs, setPollMs] = useState(0);
  const { data, error, loading, reload } = usePoll(() => getCover(name), pollMs, [name]);
  const { data: sdata } = useAsync(() => getSettings(name), [name]);
  const [set, setSet] = useState(null);
  const [feedback, setFeedback] = useState('');
  const [compare, setCompare] = useState(null);
  const [busy, setBusy] = useState(false);

  // Poll every 1.5 s while a cover run is going.
  const running = data?.status?.state === 'running';
  useEffect(() => { setPollMs(running ? 1500 : 0); }, [running]);

  // Start the cover settings from the project's cover defaults once they load.
  const project = sdata?.settings || {};
  useEffect(() => {
    if (!sdata || set) return;
    const c = project.cover || {};
    setSet({ hook: c.hook_mode === 'own' ? 'own' : 'claude', hookText: '', art: c.shot_mode === 'pick' ? 'pick' : 'claude', shot: null, maker: c.image_provider || '', force: false });
  }, [sdata]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!data) return <LoadState loading={loading} error={error} />;
  if (!data.enabled && !data.exists) {
    return (
      <Empty icon="ph-image-square" title="The cover comes after the reel" maxWidth={540}>
        Finish a reel run first. The cover picks a hook line and one of the reel’s shots, so this tab opens once book_reel.mp4 exists.
      </Empty>
    );
  }

  const cs = set || { hook: 'claude', hookText: '', art: 'claude', shot: null, maker: '', force: false };
  const upd = (patch) => setSet({ ...cs, ...patch });
  const shots = data.shots || [];
  const pickedShot = cs.shot ?? shots[0]?.number ?? null;
  const versions = data.versions || [];
  const cur = versions.find((v) => v.current) || versions.find((v) => v.url === data.cover_url) || versions[versions.length - 1] || null;
  const curN = cur ? cur.version : null;
  const status = data.status || {};
  const pct = Math.floor(Number(status.percent) || 0);
  const log = (status.log_tail || []).slice(-6);

  const act = async (fn) => {
    setBusy(true);
    try {
      await fn();
      await reload();
      return true;
    } catch (e) {
      toast(e.message, 'error');
      return false;
    } finally {
      setBusy(false);
    }
  };

  const make = () => act(() => runCover(name, {
    hook: cs.hook === 'own' && cs.hookText.trim() ? cs.hookText.trim() : undefined,
    shot: cs.art === 'pick' && pickedShot != null ? pickedShot : undefined,
    force: cs.force || undefined,
    image_provider: cs.maker || undefined,
  }));
  const revise = () => act(() => reviseCover(name, feedback.trim())).then((ok) => ok && setFeedback(''));
  const restoreVersion = async (v) => {
    setBusy(true);
    try { await restoreCover(name, v); setCompare(null); await reload(); toast(`Using cover v${v}`); }
    catch (e) { toast(e.message, 'error'); }
    finally { setBusy(false); }
  };

  const makeDisabled = busy || running || (data.exists && !cs.force);
  const providerLabel = `Project default · ${cap(project.image_provider || 'gemini')}${project.image_model ? ` (${project.image_model})` : ''}`;
  const compareV = versions.find((v) => v.version === compare);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px,310px) minmax(0,1fr)', gap: 55, alignItems: 'start' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 25 }}>
        <h6 style={{ margin: 0, ...muted }}>Cover settings</h6>
        <div className="field"><label>Hook text</label>
          <div className="seg" style={{ flexWrap: 'wrap' }}>
            <label className="seg-opt"><input type="radio" name="rs-hook" checked={cs.hook === 'claude'} onChange={() => upd({ hook: 'claude' })} />Let Claude write it</label>
            <label className="seg-opt"><input type="radio" name="rs-hook" checked={cs.hook === 'own'} onChange={() => upd({ hook: 'own' })} />Use my own text</label>
          </div>
          {cs.hook === 'own' && <textarea className="input" style={{ marginTop: 10, minHeight: 70 }} placeholder="e.g. Get 1% better. Every day." value={cs.hookText} onChange={(e) => upd({ hookText: e.target.value })} />}
        </div>
        <div className="field"><label>Base artwork</label>
          <div className="seg" style={{ flexWrap: 'wrap' }}>
            <label className="seg-opt"><input type="radio" name="rs-art" checked={cs.art === 'claude'} onChange={() => upd({ art: 'claude' })} />Let Claude choose</label>
            <label className="seg-opt"><input type="radio" name="rs-art" checked={cs.art === 'pick'} onChange={() => upd({ art: 'pick' })} />Pick a shot</label>
          </div>
          {cs.art === 'pick' && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
              {shots.map((s) => {
                const on = s.number === pickedShot;
                return (
                  <button key={s.number} onClick={() => upd({ shot: s.number })} style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center', border: 0, background: 'none', padding: 0, font: 'inherit', cursor: 'pointer' }}>
                    <Media url={s.url} label={String(s.number)} width={44} radius="var(--radius-sm)" style={{ outline: on ? '2px solid var(--color-accent)' : 'none', outlineOffset: 2 }} />
                    <span style={{ fontSize: 12, color: on ? 'var(--color-accent-700)' : 'inherit', fontWeight: on ? 600 : 400 }}>Shot {s.number}</span>
                  </button>
                );
              })}
              {!shots.length && <span style={{ fontSize: 13, ...muted }}>No shots found in the reel.</span>}
            </div>
          )}
        </div>
        <div className="field"><label>Image maker</label>
          <select className="input" value={cs.maker} onChange={(e) => upd({ maker: e.target.value })}>
            <option value="">{providerLabel}</option>
            <option value="gemini">Gemini</option>
            <option value="codex">Codex (ChatGPT subscription, $0)</option>
          </select>
        </div>
        <label className="radio" style={{ fontSize: 14 }}>
          <input type="checkbox" checked={cs.force} onChange={() => upd({ force: !cs.force })} style={{ accentColor: 'var(--color-accent)', width: 16, height: 16, position: 'static', opacity: 1, pointerEvents: 'auto' }} />
          Make a fresh cover even if one exists
        </label>
        <button className="btn btn-primary" onClick={make} disabled={makeDisabled}>
          <i className="ph-duotone ph-sparkle" />{data.exists ? 'Make a fresh cover' : 'Make cover'}
        </button>
        {running && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}><span>Making the cover</span><span>{pct}%</span></div>
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
        {!data.exists || !data.cover_url ? (
          <div style={{ paddingTop: 10 }}>
            <Empty icon="ph-image-square" title="No cover yet" maxWidth={480}>
              Choose the settings on the left and press Make cover. You’ll see it in Instagram’s feed and grid crops, then refine it with written feedback.
            </Empty>
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', gap: 35, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <Media url={data.cover_url} label="Cover · feed crop" width={300} ratio="4/5" />
                <span style={{ fontSize: 12, ...muted }}>Feed · 4:5</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <Media url={data.grid_url || data.cover_url} label="Grid preview · 1:1" width={220} ratio="1/1" />
                <span style={{ fontSize: 12, ...muted }}>Grid · 1:1</span>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 30, flexWrap: 'wrap', fontSize: 15 }}>
              {data.hook_text && <div><div style={{ fontSize: 12, ...muted }}>Hook</div><div style={{ fontStyle: 'italic' }}>“{data.hook_text}”</div></div>}
              {data.shot != null && <div><div style={{ fontSize: 12, ...muted }}>Artwork</div><div>Shot {data.shot}</div></div>}
              {curN != null && <div><div style={{ fontSize: 12, ...muted }}>Version</div><div>v{curN}</div></div>}
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <a className="btn btn-primary" href={data.cover_url} download><i className="ph-duotone ph-download-simple" />Download cover</a>
              {data.grid_url && <a className="btn btn-secondary" href={data.grid_url} download><i className="ph-duotone ph-grid-four" />Download grid preview</a>}
            </div>
            <div className="field" style={{ maxWidth: 640 }}>
              <label>What should change?</label>
              <textarea className="input" placeholder="e.g. make the headline bigger and stylish" value={feedback} onChange={(e) => setFeedback(e.target.value)} />
              <button className="btn btn-primary" style={{ marginTop: 10, alignSelf: 'flex-start' }} onClick={revise} disabled={busy || running || !feedback.trim()}>
                <i className="ph-duotone ph-arrows-clockwise" />Revise cover
              </button>
            </div>
            {versions.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div style={kicker}>Version history</div>
                <div style={{ display: 'flex', gap: 25, flexWrap: 'wrap' }}>
                  {versions.map((v) => {
                    const isCur = v.version === curN;
                    return (
                      <div key={v.version} style={{ display: 'flex', flexDirection: 'column', gap: 6, width: 140 }}>
                        <Media url={v.url} label={`v${v.version}`} width={140} ratio="4/5"
                          style={{ outline: isCur ? '2px solid var(--color-accent)' : compare === v.version ? '2px solid var(--color-neutral-500)' : 'none', outlineOffset: 2 }} />
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                          <span style={{ fontWeight: 600 }}>v{v.version}</span>
                          {isCur && <span className="tag tag-accent">Current</span>}
                        </div>
                        {v.feedback && <div style={{ fontSize: 12, lineHeight: 1.4, color: 'color-mix(in srgb,var(--color-text) 65%,transparent)' }}>Feedback: <em>“{v.feedback}”</em></div>}
                        {!isCur && <button className="btn btn-ghost" onClick={() => setCompare(v.version)} style={{ fontSize: 12, alignSelf: 'flex-start', paddingLeft: 0 }}>Compare</button>}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {compareV && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 15, padding: 25, borderRadius: 'var(--radius-lg)', background: 'var(--color-surface)' }}>
                <div style={{ display: 'flex', gap: 40, flexWrap: 'wrap' }}>
                  {[{ url: data.cover_url, label: curN != null ? `v${curN} · current` : 'Current' }, { url: compareV.url, label: `v${compareV.version}` }].map((v) => (
                    <div key={v.label} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      <Media url={v.url} label={v.label} width={240} ratio="4/5" />
                      <span style={{ fontSize: 14, fontWeight: 600 }}>{v.label}</span>
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 10 }}>
                  <button className="btn btn-primary" onClick={() => restoreVersion(compareV.version)} disabled={busy || running}>Use this version</button>
                  <button className="btn btn-secondary" onClick={() => setCompare(null)}>Close</button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
