import { useState, useEffect } from 'react';
import Modal from '../common/Modal';

type Props = {
  open: boolean;
  onCancel: () => void;
  onSave: (data: { ri_nm: string; ri_cd: string }) => void;
};

export default function AttrFormModal({ open, onCancel, onSave }: Props) {
  const [riNm, setRiNm] = useState('');
  const [riCd, setRiCd] = useState('');
  useEffect(() => {
    if (open) {
      setRiNm('');
      setRiCd('');
    }
  }, [open]);

  return (
    <Modal open={open} title="속성등록" onClose={onCancel} width={400}>
      <div style={styles.q}>행정리 명칭 및 부호를 입력하세요.</div>
      <div style={styles.row}>
        <label style={styles.lbl}>행정리명</label>
        <input
          style={styles.input}
          value={riNm}
          onChange={(e) => setRiNm(e.target.value)}
          placeholder="예: 서양리"
        />
      </div>
      <div style={styles.row}>
        <label style={styles.lbl}>부호</label>
        <input
          style={styles.input}
          value={riCd}
          onChange={(e) => setRiCd(e.target.value)}
          placeholder="예: 032"
        />
      </div>
      <div style={styles.actions}>
        <button type="button" style={styles.cancel} onClick={onCancel}>
          취소
        </button>
        <button
          type="button"
          style={styles.save}
          disabled={!riNm.trim() || !riCd.trim()}
          onClick={() => onSave({ ri_nm: riNm.trim(), ri_cd: riCd.trim() })}
        >
          저장
        </button>
      </div>
    </Modal>
  );
}

const styles: Record<string, React.CSSProperties> = {
  q: { fontSize: 13, color: '#1f2937', marginBottom: 10 },
  row: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 },
  lbl: { width: 70, fontSize: 12, color: '#374151' },
  input: {
    flex: 1,
    padding: '6px 10px',
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    fontSize: 13,
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
    border: '1px solid #1f6feb',
    background: '#1f6feb',
    color: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 13,
  },
};
