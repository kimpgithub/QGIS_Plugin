// 행정리 목록 패널 — 현재 행정읍면의 행정리(ri_nm/ri_cd/remark) 테이블.
// 행 더블클릭 시 해당 행정리 영역으로 지도 이동(InspectPage 의 onZoomTo 콜백).
// 데이터/값이 비어 있는 경우: 목록 없음 안내 / '-' 표기.
import { useMemo, useState } from 'react';
import type { BoundaryCollection, BoundaryProps, GjFeature } from '../../types';

type Props = {
  open: boolean;
  boundary: BoundaryCollection | null;
  onClose: () => void;
  onZoomTo: (feature: GjFeature<BoundaryProps>) => void;
};

export default function BoundaryListPanel({
  open,
  boundary,
  onClose,
  onZoomTo,
}: Props) {
  const [query, setQuery] = useState('');

  // ri_cd 오름차순(없는 건 뒤로) → ri_nm 순 정렬
  const rows = useMemo(() => {
    const feats = boundary?.features ?? [];
    const filtered = query.trim()
      ? feats.filter((f) => {
          const q = query.trim();
          return (
            (f.properties.ri_nm ?? '').includes(q) ||
            (f.properties.ri_cd ?? '').includes(q) ||
            (f.properties.remark ?? '').includes(q)
          );
        })
      : feats;
    return [...filtered].sort((a, b) => {
      const ac = a.properties.ri_cd ?? '￿';
      const bc = b.properties.ri_cd ?? '￿';
      if (ac !== bc) return ac < bc ? -1 : 1;
      return (a.properties.ri_nm ?? '').localeCompare(b.properties.ri_nm ?? '');
    });
  }, [boundary, query]);

  if (!open) return null;

  const total = boundary?.features.length ?? 0;

  return (
    <div style={styles.panel}>
      <div style={styles.head}>
        <b style={styles.title}>행정리 목록 ({total})</b>
        <button type="button" style={styles.close} onClick={onClose}>
          ✕
        </button>
      </div>

      {total === 0 ? (
        <div style={styles.empty}>
          행정리 경계 데이터가 없습니다.
          <br />
          QGIS 에서 경계를 제출하면 여기에 표시됩니다.
        </div>
      ) : (
        <>
          <input
            style={styles.search}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="행정리명 · 부호 · 비고 검색"
          />
          <div style={styles.hint}>행을 더블클릭하면 해당 위치로 이동합니다</div>
          <div style={styles.tableWrap}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>행정리명</th>
                  <th style={{ ...styles.th, width: 56 }}>부호</th>
                  <th style={styles.th}>비고</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={3} style={styles.noMatch}>
                      검색 결과가 없습니다
                    </td>
                  </tr>
                ) : (
                  rows.map((f) => (
                    <tr
                      key={f.properties.gid}
                      style={styles.tr}
                      onDoubleClick={() => onZoomTo(f)}
                      title="더블클릭: 위치로 이동"
                    >
                      <td style={styles.td}>
                        {f.properties.ri_nm?.trim() || (
                          <span style={styles.dim}>(이름 없음)</span>
                        )}
                      </td>
                      <td style={styles.td}>
                        {f.properties.ri_cd?.trim() || (
                          <span style={styles.dim}>-</span>
                        )}
                      </td>
                      <td style={{ ...styles.td, ...styles.tdRemark }}>
                        {f.properties.remark?.trim() || (
                          <span style={styles.dim}>-</span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  // 지도 옆에 도킹되는 사이드 패널 — 지도를 가리지 않고 레이아웃을 나눠 씀.
  panel: {
    width: 320,
    minWidth: 320,
    background: '#fff',
    borderRight: '1px solid #d0d3da',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  head: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '10px 12px',
    borderBottom: '1px solid #e5e7eb',
    background: '#f9fafb',
  },
  title: { fontSize: 13, color: '#1f2937' },
  close: {
    border: 'none',
    background: 'none',
    cursor: 'pointer',
    color: '#9ca3af',
    fontSize: 14,
    padding: 0,
    lineHeight: 1,
  },
  empty: {
    padding: 20,
    fontSize: 13,
    color: '#6b7280',
    textAlign: 'center',
    lineHeight: 1.7,
  },
  search: {
    margin: '10px 12px 0',
    padding: '6px 10px',
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    fontSize: 13,
  },
  hint: {
    margin: '6px 12px',
    fontSize: 11,
    color: '#9ca3af',
  },
  tableWrap: { flex: 1, overflowY: 'auto', minHeight: 0 },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 12,
  },
  th: {
    position: 'sticky',
    top: 0,
    background: '#f3f4f6',
    color: '#374151',
    fontWeight: 600,
    textAlign: 'left',
    padding: '6px 10px',
    borderBottom: '1px solid #e5e7eb',
    whiteSpace: 'nowrap',
  },
  tr: { cursor: 'pointer' },
  td: {
    padding: '6px 10px',
    borderBottom: '1px solid #f3f4f6',
    color: '#1f2937',
    verticalAlign: 'top',
    whiteSpace: 'nowrap',
  },
  tdRemark: {
    whiteSpace: 'normal',
    wordBreak: 'break-all',
    color: '#a16207',
  },
  dim: { color: '#c4c8cf' },
  noMatch: {
    padding: 20,
    textAlign: 'center',
    color: '#9ca3af',
    fontSize: 12,
  },
};
