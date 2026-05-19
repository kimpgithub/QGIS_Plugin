import { useState, useEffect } from 'react';
import Modal from '../common/Modal';

type Props = {
  open: boolean;
  onCancel: () => void;
  onSave: (reason: string) => void;
};

export default function RejectReasonModal({ open, onCancel, onSave }: Props) {
  const [reason, setReason] = useState('');
  useEffect(() => {
    if (open) setReason('');
  }, [open]);

  return (
    <Modal open={open} title="반려사유" onClose={onCancel} width={420}>
      <div style={styles.q}>반려 사유를 입력하세요.</div>
      <textarea
        style={styles.area}
        rows={4}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="예: 기존 화면에 맞는 라인으로 반려"
      />
      <div style={styles.actions}>
        <button type="button" style={styles.cancel} onClick={onCancel}>
          취소
        </button>
        <button
          type="button"
          style={styles.save}
          disabled={!reason.trim()}
          onClick={() => onSave(reason.trim())}
        >
          저장
        </button>
      </div>
    </Modal>
  );
}

const styles: Record<string, React.CSSProperties> = {
  q: { fontSize: 13, color: '#1f2937', marginBottom: 10 },
  area: {
    width: '100%',
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    padding: 8,
    fontSize: 13,
    fontFamily: 'inherit',
    resize: 'vertical',
    boxSizing: 'border-box',
  },
  actions: {
    marginTop: 14,
    display: 'flex',
    gap: 8,
    justifyContent: 'flex-end',
  },
  cancel: {
    padding: '6px 16px',
    border: '1px solid #c9ced6',
    background: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 13,
  },
  save: {
    padding: '6px 16px',
    border: '1px solid #b91c1c',
    background: '#b91c1c',
    color: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 13,
  },
};
