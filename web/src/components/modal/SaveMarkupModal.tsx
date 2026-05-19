import { useState, useEffect } from 'react';
import Modal from '../common/Modal';
import type { MarkupKind } from '../../types';

type Props = {
  open: boolean;
  kind: MarkupKind;
  onCancel: () => void;
  onSave: (note: string) => void;
};

const TITLES: Record<MarkupKind, string> = {
  add: '라인등록',
  delete: '라인삭제',
  attr: '속성등록',
  delete_mark: '삭제표기',
};

export default function SaveMarkupModal({ open, kind, onCancel, onSave }: Props) {
  const [note, setNote] = useState('');
  useEffect(() => {
    if (open) setNote('');
  }, [open]);

  return (
    <Modal open={open} title={TITLES[kind]} onClose={onCancel} width={420}>
      <div style={styles.q}>등록한 내용을 저장하시겠습니까?</div>
      <label style={styles.row}>
        <span style={styles.lbl}>수정사유</span>
        <textarea
          style={styles.area}
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="요청 사유를 간단히 입력하세요"
        />
      </label>
      <div style={styles.actions}>
        <button type="button" style={styles.cancel} onClick={onCancel}>
          취소
        </button>
        <button
          type="button"
          style={styles.save}
          onClick={() => onSave(note.trim())}
        >
          저장
        </button>
      </div>
    </Modal>
  );
}

const styles: Record<string, React.CSSProperties> = {
  q: { fontSize: 13, color: '#1f2937', marginBottom: 10 },
  row: { display: 'flex', flexDirection: 'column', gap: 4 },
  lbl: { fontSize: 12, color: '#374151' },
  area: {
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    padding: 8,
    fontSize: 13,
    fontFamily: 'inherit',
    resize: 'vertical',
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
