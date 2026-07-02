import type { LayerKey, LayerVisibility } from './MapView';

type Props = {
  visible: LayerVisibility;
  onToggle: (k: LayerKey, v: boolean) => void;
  onFitBoundary?: () => void;
  // 행정리 목록(ri_nm/ri_cd/remark 테이블) 패널 토글
  onToggleRiList?: () => void;
  riListOpen?: boolean;
};

const ROWS: { key: LayerKey; label: string }[] = [
  { key: 'markup', label: '수정요청' },
  { key: 'ri', label: '행정리 경계' },
  { key: 'admin', label: '행정읍면 라인' },
  { key: 'cog', label: '스캔 이미지' },
  { key: 'base', label: '배경지도' },
];

export default function LayerControls({
  visible,
  onToggle,
  onFitBoundary,
  onToggleRiList,
  riListOpen,
}: Props) {
  return (
    <div style={styles.box}>
      <div style={styles.head}>레이어</div>
      <ul style={styles.list}>
        {ROWS.map((r) => (
          <li key={r.key} style={styles.row}>
            <label style={styles.lbl}>
              <input
                type="checkbox"
                checked={visible[r.key]}
                onChange={(e) => onToggle(r.key, e.target.checked)}
              />
              <span>{r.label}</span>
            </label>
          </li>
        ))}
      </ul>
      <div style={styles.btns}>
        {onToggleRiList && (
          <button
            type="button"
            style={riListOpen ? styles.btnActive : styles.btn}
            onClick={onToggleRiList}
          >
            {riListOpen ? '행정리 목록 닫기' : '행정리 목록'}
          </button>
        )}
        {onFitBoundary && (
          <button type="button" style={styles.btn} onClick={onFitBoundary}>
            범위 맞춤
          </button>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  box: {
    // 태블릿/가로 화면 대응 — 화면이 좁아지면 함께 줄어듦(미디어쿼리 없이 clamp).
    width: 'clamp(168px, 16vw, 200px)',
    minWidth: 0,
    flexShrink: 0,
    borderRight: '1px solid #d0d3da',
    background: '#fafbfc',
    padding: '12px 14px',
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  head: {
    fontSize: 12,
    color: '#6b7280',
    fontWeight: 600,
    textTransform: 'uppercase',
  },
  list: { margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 },
  row: { fontSize: 13, color: '#1f2937' },
  lbl: { display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' },
  btns: {
    marginTop: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  btn: {
    padding: '6px 10px',
    border: '1px solid #c9ced6',
    background: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
  },
  btnActive: {
    padding: '6px 10px',
    border: '1px solid #1f6feb',
    background: '#eff6ff',
    color: '#1f6feb',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
  },
};
