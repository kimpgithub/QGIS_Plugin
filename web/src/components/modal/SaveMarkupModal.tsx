import { useState, useEffect } from 'react';
import Modal from '../common/Modal';
import type { MarkupKind } from '../../types';

type Props = {
  open: boolean;
  kind: MarkupKind;
  onCancel: () => void;
  // attr(속성등록)일 때만 attrs 가 채워짐 — 행정리명/부호는 모달 안에서 함께 입력.
  onSave: (note: string, attrs?: { ri_nm: string; ri_cd: string }) => void;
};

const TITLES: Record<MarkupKind, string> = {
  add: '라인등록',
  delete: '라인삭제',
  attr: '속성등록',
  delete_mark: '삭제표기',
};

export default function SaveMarkupModal({ open, kind, onCancel, onSave }: Props) {
  const [note, setNote] = useState('');
  // 속성등록 전용 — 행정리명/부호 (별도 모달로 나누지 않고 이 모달 위쪽에서 입력)
  const [riNm, setRiNm] = useState('');
  const [riCd, setRiCd] = useState('');
  useEffect(() => {
    if (open) {
      setNote('');
      setRiNm('');
      setRiCd('');
    }
  }, [open]);

  const isAttr = kind === 'attr';
  // 수정사유는 모든 종류에서 필수. attr 는 행정리명/부호도 필수 — 비어 있으면 저장 비활성
  const noteOk = note.trim() !== '';
  const canSave = noteOk && (!isAttr || (riNm.trim() !== '' && riCd.trim() !== ''));

  return (
    // dim=false — 지도(스캔 이미지)의 지명·부호를 보면서 입력해야 하므로
    // 배경을 어둡게 하지 않고, 모달을 드래그로 치우거나 지도를 움직일 수 있게 한다.
    <Modal open={open} title={TITLES[kind]} onClose={onCancel} width={420} dim={false}>
      {isAttr && (
        <>
          <div style={styles.q}>행정리 명칭 및 부호를 입력하세요.</div>
          <div style={styles.attrRow}>
            <label style={styles.attrLbl}>행정리명</label>
            <input
              style={styles.input}
              value={riNm}
              onChange={(e) => setRiNm(e.target.value)}
              placeholder="예: 서양리"
            />
          </div>
          <div style={styles.attrRow}>
            <label style={styles.attrLbl}>부호</label>
            <input
              style={styles.input}
              value={riCd}
              onChange={(e) => setRiCd(e.target.value)}
              placeholder="예: 032"
            />
          </div>
          <div style={styles.divider} />
        </>
      )}
      <div style={styles.q}>등록한 내용을 저장하시겠습니까?</div>
      <label style={styles.row}>
        <span style={styles.lbl}>
          수정사유 <span style={styles.req}>*</span>
        </span>
        <textarea
          style={{ ...styles.area, ...(noteOk ? {} : styles.areaRequired) }}
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="요청 사유를 간단히 입력하세요"
        />
        <span style={styles.reqHint}>* 필수입력항목입니다</span>
      </label>
      <div style={styles.actions}>
        <button type="button" style={styles.cancel} onClick={onCancel}>
          취소
        </button>
        <button
          type="button"
          style={canSave ? styles.save : { ...styles.save, opacity: 0.5, cursor: 'default' }}
          disabled={!canSave}
          onClick={() =>
            onSave(
              note.trim(),
              isAttr ? { ri_nm: riNm.trim(), ri_cd: riCd.trim() } : undefined
            )
          }
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
  req: { color: '#dc2626', fontWeight: 700 },
  reqHint: { fontSize: 11, color: '#dc2626' },
  areaRequired: { borderColor: '#f0a4a4', background: '#fff7f7' },
  attrRow: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 },
  attrLbl: { width: 70, fontSize: 12, color: '#374151' },
  input: {
    flex: 1,
    padding: '6px 10px',
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    fontSize: 13,
  },
  divider: {
    borderTop: '1px solid #e5e7eb',
    margin: '12px 0',
  },
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
