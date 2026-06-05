import { useState, useEffect } from 'react';
import Modal from '../common/Modal';

type Props = {
  open: boolean;
  onCancel: () => void;
  onSave: (reason: string) => void;
};

export default function RejectReasonModal({ open, onCancel, onSave }: Props) {
  const [reason, setReason] = useState('');
  // 빈 값으로 저장을 누른 적이 있으면 빨간 알림을 강조한다.
  const [tried, setTried] = useState(false);
  useEffect(() => {
    if (open) {
      setReason('');
      setTried(false);
    }
  }, [open]);

  const reasonOk = reason.trim() !== '';
  const showError = tried && !reasonOk;

  return (
    <Modal open={open} title="반려사유" onClose={onCancel} width={420}>
      <div style={styles.q}>
        반려 사유를 입력하세요. <span style={styles.req}>*</span>
      </div>
      <textarea
        style={{ ...styles.area, ...(showError ? styles.areaRequired : {}) }}
        rows={4}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="예: 기존 화면에 맞는 라인으로 반려"
      />
      <div style={showError ? styles.reqHintOn : styles.reqHint}>
        * 필수입력항목입니다
      </div>
      <div style={styles.actions}>
        <button type="button" style={styles.cancel} onClick={onCancel}>
          취소
        </button>
        <button
          type="button"
          style={styles.save}
          onClick={() => {
            // 아무것도 입력하지 않고 저장을 누른 경우 — 빨간 인라인 알림
            if (!reasonOk) {
              setTried(true);
              return;
            }
            onSave(reason.trim());
          }}
        >
          저장
        </button>
      </div>
    </Modal>
  );
}

const styles: Record<string, React.CSSProperties> = {
  q: { fontSize: 13, color: '#1f2937', marginBottom: 10 },
  req: { color: '#dc2626', fontWeight: 700 },
  reqHint: { marginTop: 4, fontSize: 11, color: '#9ca3af' },
  reqHintOn: { marginTop: 4, fontSize: 12, color: '#dc2626', fontWeight: 700 },
  areaRequired: { borderColor: '#f0a4a4', background: '#fff7f7' },
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
