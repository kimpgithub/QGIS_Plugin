import type { LayerKey, LayerVisibility } from './MapView';

type Props = {
  visible: LayerVisibility;
  onToggle: (k: LayerKey, v: boolean) => void;
  onFitBoundary?: () => void;
};

const ROWS: { key: LayerKey; label: string }[] = [
  { key: 'markup', label: '수정요청' },
  { key: 'ri', label: '행정리 경계' },
  { key: 'admin', label: '행정읍면 라인' },
  { key: 'cog', label: '스캔 이미지' },
];

export default function LayerControls({ visible, onToggle, onFitBoundary }: Props) {
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
      {onFitBoundary && (
        <button type="button" style={styles.btn} onClick={onFitBoundary}>
          범위 맞춤
        </button>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  box: {
    width: 200,
    minWidth: 200,
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
  btn: {
    marginTop: 'auto',
    padding: '6px 10px',
    border: '1px solid #c9ced6',
    background: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
  },
};
