import { useState } from 'react';
import Modal from './Modal.jsx';

// A button that asks first. Props: { label, confirmText, onConfirm, className, title?, confirmLabel?, icon?, danger?, disabled? }
export default function ConfirmButton({ label, confirmText, onConfirm, className, title, confirmLabel, icon, danger = true, disabled }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const confirm = async () => {
    setBusy(true);
    try { await onConfirm(); } finally { setBusy(false); setOpen(false); }
  };
  return (
    <>
      <button className={className || 'btn btn-secondary'} onClick={() => setOpen(true)} disabled={disabled}>
        {icon && <i className={'ph-duotone ' + icon} />}{label}
      </button>
      <Modal
        open={open}
        title={title || label + '?'}
        onClose={() => setOpen(false)}
        footer={<>
          <button className="btn btn-secondary" onClick={() => setOpen(false)}>Cancel</button>
          <button className="btn btn-primary" onClick={confirm} disabled={busy} style={danger ? { background: 'var(--color-accent-2-600)' } : undefined}>{confirmLabel || label}</button>
        </>}
      >
        {confirmText || 'Are you sure?'}
      </Modal>
    </>
  );
}
