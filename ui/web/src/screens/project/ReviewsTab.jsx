// Reviews tab: story critic attempts, final script, shot reviews per attempt. Props: { name }
import { useState } from 'react';
import { getReviews } from '../../api.js';
import { useAsync } from '../../hooks.js';
import { navigate } from '../../router.js';
import { Media, Motion, verdictTag, muted, kicker, Empty, LoadState } from '../../components/review/Media.jsx';

const FILTERS = [['all', 'All'], ['rejected', 'Rejected only'], ['approved', 'Approved only']];

function Comments({ list, size = 14 }) {
  if (!list || !list.length) return <div style={{ fontSize: size, ...muted }}>No comments.</div>;
  return list.map((c, i) => <div key={i} style={{ fontSize: size, lineHeight: 1.45 }}>{c}</div>);
}

function StoryAttempt({ a }) {
  const [open, setOpen] = useState(false);
  const v = verdictTag(a.verdict);
  const issues = a.issues && a.issues.length ? a.issues : [];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '130px minmax(0,1fr)', gap: 25 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
        <span style={{ fontSize: 13, ...muted }}>Attempt {a.attempt}</span>
        <span className="tag" style={v.style}>{v.label}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
        {issues.map((t, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, fontSize: 14, lineHeight: 1.45 }}>
            <i className={`ph-duotone ${v.kind === 'approved' ? 'ph-check-circle' : 'ph-x-circle'}`} style={{ marginTop: 3, flex: 'none' }} /><span>{t}</span>
          </div>
        ))}
        {!issues.length && (
          <div style={{ display: 'flex', gap: 8, fontSize: 14, lineHeight: 1.45 }}>
            <i className="ph-duotone ph-check-circle" style={{ marginTop: 3, flex: 'none' }} /><span>No issues listed.</span>
          </div>
        )}
        {a.script && (
          <button className="btn btn-ghost" onClick={() => setOpen(!open)} style={{ alignSelf: 'flex-start', paddingLeft: 0, fontSize: 13 }}>
            <i className={`ph-duotone ${open ? 'ph-caret-down' : 'ph-caret-right'}`} />{open ? 'Hide script' : 'Show this attempt’s script'}
          </button>
        )}
        {open && <p style={{ margin: 0, fontSize: 15, lineHeight: 1.6, maxWidth: 760, whiteSpace: 'pre-wrap', color: 'color-mix(in srgb,var(--color-text) 80%,transparent)' }}>{a.script}</p>}
      </div>
    </div>
  );
}

function ShotReview({ s, keep }) {
  const all = s.attempts || [];
  const [comparing, setComparing] = useState(false);
  const [pairNums, setPairNums] = useState(null);
  const shown = all.filter((a) => keep(a.verdict));

  const last = all[all.length - 1];
  const lastV = last ? verdictTag(last.verdict).kind : 'other';
  const summary = `${all.length} ${all.length === 1 ? 'attempt' : 'attempts'} · ${lastV === 'approved' ? 'approved' : lastV === 'rejected' ? 'redo requested' : 'in progress'}`;
  const pn = pairNums || [all[0]?.attempt, last?.attempt];
  const pair = pn.map((n) => all.find((a) => a.attempt === n)).filter(Boolean);
  const type = s.type === 'video' ? 'Video clip' : 'Still';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 17, fontWeight: 600 }}>Shot {s.number} · {type}</span>
        <span style={{ fontSize: 13, ...muted }}>{summary}</span>
        <span style={{ flex: 1 }} />
        {all.length > 1 && (
          <button className="btn btn-ghost" onClick={() => setComparing(!comparing)} style={{ fontSize: 13 }}>
            <i className="ph-duotone ph-columns" />{comparing ? 'Back to list' : 'Compare attempts side by side'}
          </button>
        )}
      </div>
      {comparing ? (
        <div style={{ display: 'flex', gap: 40, flexWrap: 'wrap' }}>
          {pair.map((a, idx) => {
            const v = verdictTag(a.verdict);
            return (
              <div key={idx} style={{ display: 'flex', flexDirection: 'column', gap: 8, width: 250 }}>
                <Media url={a.media_url} label={`Attempt ${a.attempt}`} width={250} controls />
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  {all.length > 2 ? (
                    <select className="input" style={{ width: 'auto', padding: '4px 8px', fontWeight: 600 }} value={a.attempt}
                      onChange={(e) => { const next = [...pn]; next[idx] = Number(e.target.value); setPairNums(next); }}>
                      {all.map((x) => <option key={x.attempt} value={x.attempt}>Attempt {x.attempt}</option>)}
                    </select>
                  ) : <span style={{ fontWeight: 600 }}>Attempt {a.attempt}</span>}
                  <span className="tag" style={v.style}>{v.label}</span>
                </div>
                <Comments list={a.comments} />
                <Motion value={a.camera_motion} />
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 30, overflowX: 'auto', paddingBottom: 6 }}>
          {shown.map((a) => {
            const v = verdictTag(a.verdict);
            return (
              <div key={a.attempt} style={{ display: 'flex', gap: 14, width: 340, flex: 'none' }}>
                <a href={a.media_url || undefined} target="_blank" rel="noreferrer" style={{ flex: 'none' }}>
                  <Media url={a.media_url} label={String(a.attempt)} width={84} />
                </a>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ fontSize: 13, ...muted }}>Attempt {a.attempt}</span>
                    <span className="tag" style={v.style}>{v.label}</span>
                  </div>
                  <Comments list={a.comments} />
                  <Motion value={a.camera_motion} />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function ReviewsTab({ name }) {
  const { data, error, loading } = useAsync(() => getReviews(name), [name]);
  const [filter, setFilter] = useState('all');
  if (!data) return <LoadState loading={loading} error={error} />;

  const keep = (v) => filter === 'all' || verdictTag(v).kind === filter;
  const story = (data.story || []).filter((a) => keep(a.verdict));
  const shots = (data.shots || []).filter((s) => s.attempts && s.attempts.length);
  const none = !(data.story || []).length && !shots.length;
  const finalScript = filter !== 'rejected' ? data.final_script : '';
  const visibleShots = shots.filter((s) => s.attempts.some((a) => keep(a.verdict)));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 45 }}>
      <div style={{ display: 'flex', gap: 15, alignItems: 'center', flexWrap: 'wrap' }}>
        <div className="seg">
          {FILTERS.map(([k, label]) => (
            <label key={k} className="seg-opt"><input type="radio" name="rs-rf" checked={filter === k} onChange={() => setFilter(k)} />{label}</label>
          ))}
        </div>
      </div>

      {none && (
        <Empty icon="ph-chat-circle-text" title="No reviews yet">
          The critic reads the narration during step 2, and the visual reviewer checks each shot during step 5. Their verdicts collect here.
        </Empty>
      )}

      {(data.story || []).length > 0 && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 15, flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0 }}>Story review</h3>
            <button className="btn btn-ghost" onClick={() => navigate(`/p/${encodeURIComponent(name)}/narration`)}>
              <i className="ph-duotone ph-note-pencil" />Open narration editor
            </button>
          </div>
          {story.map((a) => <StoryAttempt key={a.attempt} a={a} />)}
          {!story.length && <div style={{ fontSize: 14, ...muted }}>No story attempts match this filter.</div>}
          {finalScript && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 10 }}>
              <div style={kicker}>Final approved script</div>
              <p style={{ margin: 0, fontSize: 23, lineHeight: 1.5, maxWidth: 820, textWrap: 'pretty', whiteSpace: 'pre-wrap' }}>{finalScript}</p>
            </div>
          )}
        </section>
      )}

      {shots.length > 0 && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: 30 }}>
          <h3 style={{ margin: 0 }}>Shot reviews</h3>
          {visibleShots.map((s) => <ShotReview key={s.number} s={s} keep={keep} />)}
          {!visibleShots.length && <div style={{ fontSize: 14, ...muted }}>No shot attempts match this filter.</div>}
        </section>
      )}
    </div>
  );
}
