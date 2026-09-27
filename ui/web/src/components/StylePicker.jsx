// Art style picker: a grid of cards with the style's sample image (or palette stripes) and palette swatches.
// Props: { value, onChange(id), styles } — styles are Style objects from listStyles() (or meta.styles {id,name}).

const COLORS = { blue: '#3a86ff', azure: '#3a86ff', teal: '#2a9d8f', green: '#2a9d8f', yellow: '#ffd166', orange: '#f4845f', coral: '#f4845f', cream: '#fff3d6', tan: '#d2b48c', red: '#d7263d', pink: '#f7a1c4', purple: '#7a3cff', violet: '#7a3cff', black: '#1e1e1e', white: '#f5f5f5', grey: '#a7a7a7', gray: '#a7a7a7', brown: '#8b5e3c', navy: '#23395b' };

/** Turn a palette colour word ("deep navy") into a CSS colour. */
export function colorFor(word) {
  const w = String(word).toLowerCase();
  const k = Object.keys(COLORS).find((c) => w.includes(c));
  return k ? COLORS[k] : '#9b9797';
}

export default function StylePicker({ value, onChange, styles }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(118px,1fr))', gap: 12 }}>
      {(styles || []).map((s) => {
        const palette = (s.palette || []).slice(0, 6).map(colorFor);
        const selected = value === s.id;
        return (
          <button key={s.id} type="button" title={s.description || s.name} onClick={() => onChange(s.id)}
            style={{ display: 'flex', flexDirection: 'column', padding: 0, border: 0, background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', overflow: 'hidden', font: 'inherit', color: 'inherit', cursor: 'pointer', outline: selected ? '2px solid var(--color-accent)' : 'none', outlineOffset: 2 }}>
            <div style={{ position: 'relative', width: '100%', aspectRatio: '4/3', display: 'flex' }}>
              {s.image_url
                ? <img src={s.image_url} alt={`${s.name} sample`} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                : palette.map((c, i) => <span key={i} style={{ flex: 1, background: c }} />)}
            </div>
            <span style={{ fontSize: 12, fontWeight: 600, padding: '6px 8px 2px', textAlign: 'left' }}>{s.display_name || s.name}</span>
            <span style={{ display: 'flex', gap: 3, padding: '0 8px 8px' }}>
              {palette.map((c, i) => <span key={i} style={{ width: 10, height: 10, borderRadius: '50%', background: c }} />)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
