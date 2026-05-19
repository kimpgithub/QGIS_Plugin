import type { ReactNode } from 'react';

type Props = {
  open: boolean;
  title?: string;
  onClose: () => void;
  children: ReactNode;
  width?: number;
};

export default function Modal({ open, title, onClose, children, width = 360 }: Props) {
  if (!open) return null;
  return (
    <div style={styles.backdrop} onClick={onClose}>
      <div
        style={{ ...styles.card, width }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={styles.head}>
          <span>{title}</span>
          <button type="button" style={styles.close} onClick={onClose}>
            ×
          </button>
        </div>
        <div style={styles.body}>{children}</div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.35)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
  },
  card: {
    background: '#fff',
    borderRadius: 6,
    boxShadow: '0 6px 30px rgba(0,0,0,0.2)',
    display: 'flex',
    flexDirection: 'column',
  },
  head: {
    padding: '10px 14px',
    borderBottom: '1px solid #d0d3da',
    fontSize: 13,
    fontWeight: 600,
    color: '#1f2937',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  body: { padding: 14 },
  close: {
    background: 'transparent',
    border: 'none',
    fontSize: 18,
    cursor: 'pointer',
    color: '#6b7280',
  },
};
