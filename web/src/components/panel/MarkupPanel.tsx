import { useMemo } from 'react';
import type { Markup, MarkupStatus } from '../../types';
import MarkupCard from './MarkupCard';

type Props = {
  items: Markup[];
  filter: Record<MarkupStatus, boolean>;
  onFilterChange: (f: Record<MarkupStatus, boolean>) => void;
  selectedId: number | null;
  onSelect: (id: number) => void;
  // 작업자(master) 처리 — 반영(QGIS 수정 완료 선언) / 반려(사유)
  onApply: (id: number) => void;
  onReject: (id: number) => void;
  // 라인등록/삭제표기/속성등록 공간정보를 GeoJSON 으로 다운로드 (QGIS 작업용)
  onDownload?: () => void;
  canProcess?: boolean;
  loading?: boolean;
};

const FILTER_ROWS: { key: MarkupStatus; label: string }[] = [
  { key: 'pending', label: '미처리' },
  { key: 'applied', label: '반영' },
  { key: 'rejected', label: '반려' },
];

export default function MarkupPanel({
  items,
  filter,
  onFilterChange,
  selectedId,
  onSelect,
  onApply,
  onReject,
  onDownload,
  canProcess,
  loading,
}: Props) {
  const filtered = useMemo(
    () => items.filter((i) => filter[i.status]),
    [items, filter]
  );

  return (
    <div style={styles.box}>
      <div style={styles.head}>
        <div style={styles.titleRow}>
          <div style={styles.title}>수정요청</div>
          {onDownload && (
            <button
              type="button"
              style={styles.dlBtn}
              onClick={onDownload}
              title="라인등록/삭제표기/속성등록을 GeoJSON 파일로 저장 (QGIS에서 바로 열림)"
            >
              ⬇ 공간정보
            </button>
          )}
        </div>
        <div style={styles.filters}>
          {FILTER_ROWS.map((r) => (
            <label key={r.key} style={styles.flbl}>
              <input
                type="checkbox"
                checked={filter[r.key]}
                onChange={(e) =>
                  onFilterChange({ ...filter, [r.key]: e.target.checked })
                }
              />
              <span>{r.label}</span>
            </label>
          ))}
        </div>
      </div>
      <div style={styles.list}>
        {loading && <div style={styles.empty}>불러오는 중…</div>}
        {!loading && filtered.length === 0 && (
          <div style={styles.empty}>해당 조건의 요청이 없습니다.</div>
        )}
        {filtered.map((m) => (
          <MarkupCard
            key={m.id}
            item={m}
            selected={m.id === selectedId}
            canProcess={canProcess}
            onClick={() => onSelect(m.id)}
            onApply={() => onApply(m.id)}
            onReject={() => onReject(m.id)}
          />
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  box: {
    width: 320,
    minWidth: 320,
    borderLeft: '1px solid #d0d3da',
    background: '#fafbfc',
    display: 'flex',
    flexDirection: 'column',
  },
  head: {
    padding: '10px 12px',
    borderBottom: '1px solid #d0d3da',
    background: '#fff',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  titleRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  title: { fontSize: 13, fontWeight: 600, color: '#1f2937' },
  dlBtn: {
    padding: '3px 10px',
    border: '1px solid #c9ced6',
    background: '#fff',
    borderRadius: 4,
    fontSize: 12,
    cursor: 'pointer',
    color: '#374151',
  },
  filters: { display: 'flex', gap: 12 },
  flbl: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    fontSize: 12,
    color: '#374151',
    cursor: 'pointer',
  },
  list: {
    flex: 1,
    overflowY: 'auto',
    padding: 10,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  empty: {
    textAlign: 'center',
    color: '#9ca3af',
    fontSize: 12,
    padding: '20px 0',
  },
};
