import { useEffect } from 'react';

// Dialog. Props: { open, title, onClose, children, footer, width?, plain? }
// plain: render children as-is (no dimmed .dialog-body wrapper), e.g. for a large image.
export default function Modal({ open, title, onClose, children, footer, width, plain }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape' && onClose) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="dialog-backdrop" onClick={onClose} style={{ zIndex: 60 }}>
      <div className="dialog" onClick={(e) => e.stopPropagation()} style={width ? { width } : undefined}>
        {title && <div className="dialog-title">{title}</div>}
        {children && (plain ? children : <div className="dialog-body">{children}</div>)}
        {footer && <div className="dialog-actions">{footer}</div>}
      </div>
    </div>
  );
}
