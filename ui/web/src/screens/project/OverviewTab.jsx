// Project overview (design: OVERVIEW; spec 5.1).
// Props: { name, project (ProjectSummary & { settings, reel_url, cover_url }) }
import { useAsync } from '../../hooks.js';
import { getMeta } from '../../api.js';
import { navigate } from '../../router.js';
import { money, fmtDur, MUTED } from '../../components/Stepper.jsx';

const kicker = { fontSize: 12, letterSpacing: '.08em', textTransform: 'uppercase', color: MUTED };
const labelOf = (list, id) => {
  const x = (list || []).find((i) => i.id === id);
  return x ? x.label || x.name : id;
};

export default function OverviewTab({ name, project }) {
  const { data: meta } = useAsync(getMeta, []);
  if (!project) return <div className="rs-muted">Loading…</div>;
  const p = project, s = p.settings || {}, st = p.status;
  const finished = st === 'finished' && p.reel_url;
  const hasRun = !!p.last_run_at;

  const reasons = {
    draft: ['Not run yet', 'Press Run to make the reel. It takes about 20 minutes.'],
    running: ['Running now · ' + Math.floor(p.percent || 0) + '%', 'Open the Run tab to watch it.'],
    paused: ['Waiting for you', 'The run paused after the narration. Open the Run tab to read it and continue.'],
    stopped: ['Stopped', p.stop_reason || 'The run stopped. Resume from the Run tab.'],
    finished: ['Finished', 'book_reel.mp4 is ready' + (p.duration_s ? ' · took ' + fmtDur(p.duration_s) : '') + '.'],
  };
  const [title, detail] = reasons[st] || reasons.draft;
  const character = s.character_id == null ? 'No character' : labelOf(meta && meta.characters, s.character_id) || p.character;
  const imageMaker = s.image_provider === 'codex' ? 'Codex' : 'Gemini';
  const facts = [
    ['Story style', labelOf(meta && meta.story_skills, s.story_skill) || '—'],
    ['Art style', labelOf(meta && meta.styles, s.style || p.style) || '—'],
    ['Character', character || '—'],
    ['Pictures', labelOf(meta && meta.source_modes, s.source_mode) || '—'],
    ['Narrator voice', s.voice || '—'],
    ['Models', [s.claude_model, imageMaker, s.veo_model].filter(Boolean).join(' · ') || '—'],
    ['Total cost', hasRun ? money(p.spent_usd) + ' of ' + money(p.budget_usd) : '—'],
    ['Total time', p.duration_s ? fmtDur(p.duration_s) : '—'],
    ['Date', p.last_run_at ? new Date(p.last_run_at).toLocaleString() : '—'],
    ['Screenshots', String(p.screenshot_count ?? (p.screenshots || []).length)],
  ];

  return (
    <div style={{ display: 'flex', gap: 45, flexWrap: 'wrap', alignItems: 'flex-start' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: 270 }}>
        <div style={kicker}>Final reel</div>
        {finished ? (
          <>
            <video src={p.reel_url} controls poster={p.thumbnail_url || undefined}
              style={{ width: 270, aspectRatio: '9/16', borderRadius: 'var(--radius-md)', background: 'var(--color-surface)', objectFit: 'cover' }} />
            <div style={{ display: 'flex', gap: 8 }}>
              <a className="btn btn-primary" href={p.reel_url} download={name + '.mp4'}><i className="ph-duotone ph-download-simple" />Download</a>
            </div>
          </>
        ) : (
          <div style={{ width: 270, aspectRatio: '9/16', borderRadius: 'var(--radius-md)', background: 'var(--color-surface)', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 8, padding: 25, fontSize: 14, boxSizing: 'border-box' }}>
            <i className="ph-duotone ph-film-reel" style={{ fontSize: 32, color: 'var(--color-neutral-500)' }} />
            <span style={{ fontWeight: 600 }}>No finished reel yet</span>
            <span style={{ color: MUTED }}>{detail}</span>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: 210 }}>
        <div style={kicker}>Cover</div>
        {p.has_cover && p.cover_url ? (
          <>
            <img src={p.cover_url} alt="Cover" onClick={() => navigate(`/p/${encodeURIComponent(name)}/cover`)}
              style={{ width: 210, aspectRatio: '4/5', objectFit: 'cover', borderRadius: 'var(--radius-md)', cursor: 'pointer' }} />
            <span style={{ fontSize: 13 }}>Latest version</span>
          </>
        ) : (
          <div style={{ width: 210, aspectRatio: '4/5', borderRadius: 'var(--radius-md)', background: 'var(--color-surface)', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 10, padding: 20, fontSize: 14, boxSizing: 'border-box' }}>
            <span style={{ fontWeight: 600 }}>No cover yet</span>
            <span style={{ color: MUTED }}>{st === 'finished' ? 'Pick a hook and an artwork from the reel.' : 'Available once the reel is finished.'}</span>
            <button className="btn btn-secondary" disabled={st !== 'finished'} onClick={() => navigate(`/p/${encodeURIComponent(name)}/cover`)} style={{ alignSelf: 'flex-start' }}>Make cover</button>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 30, flex: 1, minWidth: 280 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={kicker}>Last run</div>
          <div style={{ fontFamily: 'var(--font-heading)', fontSize: 26, fontWeight: 600, lineHeight: 1.2, textWrap: 'pretty' }}>{title}</div>
          <div style={{ fontSize: 15, textWrap: 'pretty' }}>{detail}</div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(190px,1fr))', gap: '18px 30px' }}>
          {facts.map(([k, v]) => (
            <div key={k}>
              <div style={{ fontSize: 12, color: 'color-mix(in srgb,var(--color-text) 60%,transparent)' }}>{k}</div>
              <div style={{ fontSize: 15, overflowWrap: 'anywhere' }}>{v}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
