// 행정리 목록 패널 — 현재 행정읍면의 행정리(ri_nm/ri_cd/remark) 테이블.
// 행 더블클릭 시 해당 행정리 영역으로 지도 이동(InspectPage 의 onZoomTo 콜백).
// 기본은 "비고 있음"만 표시 — 작업자 확인이 필요한 행정리에 집중. [전체] 토글로 전환.
// 데이터/값이 비어 있는 경우: 목록 없음 안내 / '-' 표기.
import { useMemo, useState } from 'react';
import type { BoundaryCollection, BoundaryProps, GjFeature } from '../../types';
import { setBoundaryConfirm } from '../../api/boundary';

type Props = {
  open: boolean;
  boundary: BoundaryCollection | null;
  onClose: () => void;
  onZoomTo: (feature: GjFeature<BoundaryProps>) => void;
};

const hasRemark = (f: GjFeature<BoundaryProps>) =>
  Boolean(f.properties.remark?.trim());

// 완료여부 체크 대상 — 비고가 "…경계 확인"(예: 덕소1리,6리 경계 확인 / 추후 행정리경계 확인)
// 이고 부호(ri_cd)가 있는 행정리. 작업자가 임의 작업한 경계의 검토 완료 체크용.
const CONFIRM_REMARK = /경계\s*확인/;
const isConfirmTarget = (f: GjFeature<BoundaryProps>) =>
  CONFIRM_REMARK.test(f.properties.remark ?? '') &&
  Boolean(f.properties.ri_cd?.trim());

export default function BoundaryListPanel({
  open,
  boundary,
  onClose,
  onZoomTo,
}: Props) {
  const [query, setQuery] = useState('');
  // false(기본) = 비고(remark) 있는 행정리만 / true = 전체
  const [showAll, setShowAll] = useState(false);
  // 완료여부 낙관적 갱신 — `${adm_cd}/${ri_cd}` → confirmed. 서버 값(props) 위에 덮어씀.
  const [confirmOverride, setConfirmOverride] = useState<Record<string, boolean>>({});
  const [confirmBusy, setConfirmBusy] = useState<Record<string, boolean>>({});

  const confirmKey = (f: GjFeature<BoundaryProps>) =>
    `${f.properties.adm_cd}/${f.properties.ri_cd?.trim() ?? ''}`;
  const isConfirmed = (f: GjFeature<BoundaryProps>) => {
    const k = confirmKey(f);
    return k in confirmOverride ? confirmOverride[k] : Boolean(f.properties.confirmed);
  };

  async function toggleConfirm(f: GjFeature<BoundaryProps>) {
    const adm_cd = f.properties.adm_cd;
    const ri_cd = f.properties.ri_cd?.trim() ?? '';
    const k = confirmKey(f);
    if (confirmBusy[k]) return;
    const next = !isConfirmed(f);
    setConfirmOverride((s) => ({ ...s, [k]: next }));
    setConfirmBusy((s) => ({ ...s, [k]: true }));
    try {
      await setBoundaryConfirm(adm_cd, ri_cd, next);
    } catch {
      // 실패 시 롤백
      setConfirmOverride((s) => ({ ...s, [k]: !next }));
      alert('완료여부 저장에 실패했습니다. 다시 시도하세요.');
    } finally {
      setConfirmBusy((s) => {
        const { [k]: _drop, ...rest } = s;
        return rest;
      });
    }
  }

  const feats = boundary?.features ?? [];
  const remarkCount = useMemo(() => feats.filter(hasRemark).length, [feats]);

  // 비고 필터 → 검색 → ri_cd 오름차순(없는 건 뒤로) → ri_nm 순 정렬
  const rows = useMemo(() => {
    const base = showAll ? feats : feats.filter(hasRemark);
    const filtered = query.trim()
      ? base.filter((f) => {
          const q = query.trim();
          return (
            (f.properties.ri_nm ?? '').includes(q) ||
            (f.properties.ri_cd ?? '').includes(q) ||
            (f.properties.remark ?? '').includes(q)
          );
        })
      : base;
    return [...filtered].sort((a, b) => {
      const ac = a.properties.ri_cd ?? '￿';
      const bc = b.properties.ri_cd ?? '￿';
      if (ac !== bc) return ac < bc ? -1 : 1;
      return (a.properties.ri_nm ?? '').localeCompare(b.properties.ri_nm ?? '');
    });
  }, [feats, query, showAll]);

  if (!open) return null;

  const total = feats.length;

  return (
    <div style={styles.panel}>
      <div style={styles.head}>
        <b style={styles.title}>
          행정리 목록 ({showAll ? total : `비고 ${remarkCount}`})
        </b>
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
          {/* 비고 있음(기본) / 전체 토글 */}
          <div style={styles.filterRow}>
            <button
              type="button"
              style={showAll ? styles.filterBtn : styles.filterBtnActive}
              onClick={() => setShowAll(false)}
            >
              비고 있음 ({remarkCount})
            </button>
            <button
              type="button"
              style={showAll ? styles.filterBtnActive : styles.filterBtn}
              onClick={() => setShowAll(true)}
            >
              전체 ({total})
            </button>
          </div>
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
                  <th style={{ ...styles.th, width: 44, textAlign: 'center' }}>완료</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={4} style={styles.noMatch}>
                      {query.trim() ? (
                        '검색 결과가 없습니다'
                      ) : (
                        <>
                          비고가 있는 행정리가 없습니다
                          <br />
                          <button
                            type="button"
                            style={styles.showAllLink}
                            onClick={() => setShowAll(true)}
                          >
                            전체 행정리 보기 ({total})
                          </button>
                        </>
                      )}
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
                      <td
                        style={{ ...styles.td, textAlign: 'center' }}
                        onDoubleClick={(e) => e.stopPropagation()}
                      >
                        {isConfirmTarget(f) ? (
                          <input
                            type="checkbox"
                            checked={isConfirmed(f)}
                            disabled={confirmBusy[confirmKey(f)]}
                            onChange={() => toggleConfirm(f)}
                            onClick={(e) => e.stopPropagation()}
                            title="행정리경계 확인 완료여부"
                            style={{ cursor: 'pointer' }}
                          />
                        ) : (
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
  filterRow: {
    display: 'flex',
    gap: 6,
    margin: '10px 12px 0',
  },
  filterBtn: {
    flex: 1,
    padding: '5px 0',
    border: '1px solid #d0d3da',
    background: '#fff',
    color: '#6b7280',
    borderRadius: 4,
    fontSize: 12,
    cursor: 'pointer',
  },
  filterBtnActive: {
    flex: 1,
    padding: '5px 0',
    border: '1px solid #a16207',
    background: '#fef9c3',
    color: '#a16207',
    fontWeight: 600,
    borderRadius: 4,
    fontSize: 12,
    cursor: 'pointer',
  },
  showAllLink: {
    marginTop: 8,
    padding: '5px 14px',
    border: '1px solid #c9ced6',
    background: '#fff',
    color: '#374151',
    borderRadius: 4,
    fontSize: 12,
    cursor: 'pointer',
  },
  search: {
    margin: '8px 12px 0',
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
