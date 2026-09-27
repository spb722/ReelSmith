// Character picker: round thumbnails from the Character pool, plus "No character" (value null).
// Props: { value, onChange(id|null), characters } — characters from listCharacters() (or meta.characters {id,name}).
import { characterImageUrl } from '../api.js';

export default function CharacterPicker({ value, onChange, characters }) {
  const cards = [{ id: null, name: 'No character' }, ...(characters || [])];
  return (
    <div style={{ display: 'flex', gap: 15, flexWrap: 'wrap' }}>
      {cards.map((c) => {
        const selected = (value ?? null) === c.id;
        return (
          <button key={c.id ?? '__none'} type="button" onClick={() => onChange(c.id)}
            style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: 8, border: 0, background: selected ? 'var(--color-accent-100)' : 'transparent', color: 'inherit', font: 'inherit', cursor: 'pointer', borderRadius: 'var(--radius-md)', outline: selected ? '2px solid var(--color-accent)' : 'none' }}>
            {c.id === null
              ? <span style={{ width: 64, height: 64, borderRadius: '50%', display: 'grid', placeItems: 'center', background: 'var(--color-surface)' }}><i className="ph-duotone ph-prohibit" style={{ fontSize: 26 }} /></span>
              : <img src={c.main_url || characterImageUrl(c.id, 'main')} alt={c.name} style={{ width: 64, height: 64, borderRadius: '50%', objectFit: 'cover', background: 'var(--color-surface)' }} />}
            <span style={{ fontSize: 12, display: 'flex', gap: 3, alignItems: 'center' }}>
              {c.name}
              {c.is_default && <i className="ph-duotone ph-star" style={{ color: 'var(--color-accent)' }} />}
            </span>
          </button>
        );
      })}
    </div>
  );
}
