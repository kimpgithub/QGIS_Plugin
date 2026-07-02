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
  // [요청삭제] — 확인 모달을 거쳐 삭제
  onDelete: (id: number) => void;
  // 공간정보 다운로드(QGIS 작업용) — 둘 다 관리자 전용, 넘어온 것만 버튼 표시.
  onDownloadThis?: () => void; // 현재 선택한 읍면 1개 (GeoJSON)
  onDownloadBulk?: () => void; // 전국/시도 (ZIP) — 범위 선택 모달
  canProcess?: boolean;
  // true 면 요청취소 버튼 숨김 — 열람전용(perm_level=2) 계정.
  readOnly?: boolean;
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
  onDelete,
  onDownloadThis,
  onDownloadBulk,
  canProcess,
  readOnly,
  loading,
}: Props) {
  const filtered = useMemo(
    () => items.filter((i) => filter[i.status]),
    [items, filter]
  );

  return (
    <div style={styles.box}>
      <div style={styles.head}>
        <div style={styles.title}>수정요청</div>
        {(onDownloadThis || onDownloadBulk) && (
          <div style={styles.dlRow}>
            {onDownloadThis && (
              <button
                type="button"
                style={styles.dlBtn}
                onClick={onDownloadThis}
                title="현재 선택한 읍면의 수정요청을 GeoJSON 파일로 저장 (QGIS에서 바로 열림)"
              >
                ⬇ 현재 읍면 공간정보
              </button>
            )}
            {onDownloadBulk && (
              <button
                type="button"
                style={styles.dlBtn}
                onClick={onDownloadBulk}
                title="전국/시도 범위를 골라 수정요청 공간정보를 ZIP 으로 저장"
              >
                ⬇ 전국·시도 공간정보
              </button>
            )}
          </div>
        )}
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
            onDelete={readOnly ? undefined : () => onDelete(m.id)}
          />
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  box: {
    // 태블릿/가로 화면 대응 — 화면 폭에 따라 유동(미디어쿼리 없이 clamp).
    width: 'clamp(240px, 24vw, 320px)',
    minWidth: 0,
    flexShrink: 0,
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
  title: { fontSize: 13, fontWeight: 600, color: '#1f2937' },
  // 다운로드 버튼 전용 줄 — 제목 아래, 패널 폭 전체를 반반 나눔.
  dlRow: { display: 'flex', gap: 6 },
  dlBtn: {
    flex: 1,
    padding: '5px 6px',
    border: '1px solid #c9ced6',
    background: '#fff',
    borderRadius: 4,
    fontSize: 12,
    cursor: 'pointer',
    color: '#374151',
    whiteSpace: 'nowrap',
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
