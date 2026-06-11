import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../store/AuthContext';
import {
  listMarkupStats,
  listUploadHistory,
  listMarkupItems,
  deleteMarkupItem,
  deleteAllMarkup,
  type MarkupStat,
  type UploadHistory,
  type MarkupItem,
} from '../api/admin';
import { ApiError } from '../api/client';
import { formatKST } from '../lib/datetime';

const KIND_LABEL: Record<MarkupItem['kind'], string> = {
  add: '라인등록',
  delete: '라인삭제',
  attr: '속성등록',
  delete_mark: '삭제표기',
};
const STATUS_LABEL: Record<MarkupItem['status'], string> = {
  pending: '처리대기',
  applied: '반영됨',
  rejected: '반려',
};
const STATUS_COLOR: Record<MarkupItem['status'], string> = {
  pending: '#b45309',
  applied: '#15803d',
  rejected: '#b91c1c',
};

type Tab = 'markup' | 'upload';

type Props = {
  onBack: () => void;
};

// 관리 현황 페이지 — 00000000(발주처 총괄) 전용.
// 1) 지역별 수정요청 현황  2) 데이터 업로드 이력
export default function AdminDashboardPage({ onBack }: Props) {
  const { user, signOut } = useAuth();
  const [tab, setTab] = useState<Tab>('markup');
  const [stats, setStats] = useState<MarkupStat[] | null>(null);
  const [uploads, setUploads] = useState<UploadHistory[] | null>(null);
  const [items, setItems] = useState<MarkupItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 삭제 진행 상태 — 개별(id 집합) / 전체(불리언)
  const [deletingIds, setDeletingIds] = useState<Set<number>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);

  const fetchAll = useCallback(async () => {
    const [s, u, i] = await Promise.all([
      listMarkupStats(),
      listUploadHistory(),
      listMarkupItems(),
    ]);
    setStats(s);
    setUploads(u);
    setItems(i);
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fetchAll()
      .catch((e) => {
        if (!alive) return;
        const msg =
          e instanceof ApiError && e.status === 403
            ? '이 페이지는 00000000 계정만 열람할 수 있습니다.'
            : '현황을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.';
        setError(msg);
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [fetchAll]);

  async function handleDeleteOne(it: MarkupItem) {
    const ok = window.confirm(
      `이 수정요청을 삭제할까요?\n\n` +
        `[${it.adm_nm ?? it.adm_cd}] ${KIND_LABEL[it.kind]} · ${STATUS_LABEL[it.status]}\n` +
        `삭제하면 복구할 수 없습니다.`
    );
    if (!ok) return;
    setDeletingIds((s) => new Set(s).add(it.id));
    try {
      await deleteMarkupItem(it.id);
      await fetchAll();
    } catch {
      alert('삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      setDeletingIds((s) => {
        const n = new Set(s);
        n.delete(it.id);
        return n;
      });
    }
  }

  async function handleDeleteAll() {
    const n = totals?.total ?? 0;
    if (n === 0 || bulkDeleting) return;
    const ok1 = window.confirm(
      `전국 모든 수정요청 ${n}건을 삭제합니다.\n` +
        `반영·반려된 요청까지 전부 삭제되며, 복구할 수 없습니다.\n\n계속할까요?`
    );
    if (!ok1) return;
    const ok2 = window.confirm(`정말 ${n}건을 모두 삭제할까요? 이 작업은 되돌릴 수 없습니다.`);
    if (!ok2) return;
    setBulkDeleting(true);
    try {
      const r = await deleteAllMarkup();
      await fetchAll();
      alert(`${r.deleted}건을 삭제했습니다.`);
    } catch {
      alert('일괄 삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.');
    } finally {
      setBulkDeleting(false);
    }
  }

  // 수정요청 합계
  const totals = useMemo(() => {
    if (!stats) return null;
    return stats.reduce(
      (a, s) => ({
        total: a.total + s.total,
        pending: a.pending + s.pending,
        applied: a.applied + s.applied,
        rejected: a.rejected + s.rejected,
        regions: a.regions + 1,
      }),
      { total: 0, pending: 0, applied: 0, rejected: 0, regions: 0 }
    );
  }, [stats]);

  return (
    <div style={styles.page}>
      <div style={styles.bar}>
        <div style={styles.barLeft}>
          <button type="button" style={styles.back} onClick={onBack}>
            ← 검수 화면으로
          </button>
          <span style={styles.title}>관리 현황</span>
        </div>
        <div style={styles.barRight}>
          <span style={styles.user}>{user?.id}</span>
          <button type="button" style={styles.logout} onClick={signOut}>
            로그아웃
          </button>
        </div>
      </div>

      <div style={styles.tabs}>
        <button
          type="button"
          style={tab === 'markup' ? styles.tabActive : styles.tab}
          onClick={() => setTab('markup')}
        >
          지역별 수정요청 현황
        </button>
        <button
          type="button"
          style={tab === 'upload' ? styles.tabActive : styles.tab}
          onClick={() => setTab('upload')}
        >
          데이터 업로드 이력
        </button>
      </div>

      <div style={styles.content}>
        {loading && <div style={styles.note}>불러오는 중…</div>}
        {error && <div style={styles.error}>{error}</div>}

        {!loading && !error && tab === 'markup' && (
          <MarkupTable
            rows={stats ?? []}
            totals={totals}
            items={items ?? []}
            deletingIds={deletingIds}
            bulkDeleting={bulkDeleting}
            onDeleteOne={handleDeleteOne}
            onDeleteAll={handleDeleteAll}
          />
        )}
        {!loading && !error && tab === 'upload' && (
          <UploadTable rows={uploads ?? []} />
        )}
      </div>
    </div>
  );
}

function MarkupTable({
  rows,
  totals,
  items,
  deletingIds,
  bulkDeleting,
  onDeleteOne,
  onDeleteAll,
}: {
  rows: MarkupStat[];
  totals: {
    total: number;
    pending: number;
    applied: number;
    rejected: number;
    regions: number;
  } | null;
  items: MarkupItem[];
  deletingIds: Set<number>;
  bulkDeleting: boolean;
  onDeleteOne: (it: MarkupItem) => void;
  onDeleteAll: () => void;
}) {
  // 펼친 읍면(adm_cd) — 한 번에 하나만 펼침
  const [expanded, setExpanded] = useState<string | null>(null);
  // 읍면별 개별 요청 묶기
  const byAdm = useMemo(() => {
    const m = new Map<string, MarkupItem[]>();
    for (const it of items) {
      const arr = m.get(it.adm_cd) ?? [];
      arr.push(it);
      m.set(it.adm_cd, arr);
    }
    return m;
  }, [items]);

  if (rows.length === 0)
    return <div style={styles.note}>등록된 수정요청이 없습니다.</div>;

  return (
    <>
      {totals && (
        <div style={styles.summary}>
          <Stat label="대상 읍면" value={totals.regions} />
          <Stat label="전체 요청" value={totals.total} />
          <Stat label="처리대기" value={totals.pending} color="#b45309" />
          <Stat label="반영됨" value={totals.applied} color="#15803d" />
          <Stat label="반려" value={totals.rejected} color="#b91c1c" />
        </div>
      )}
      <div style={styles.toolbarRow}>
        <span style={styles.hintText}>읍면 행을 클릭하면 개별 수정요청을 펼쳐 삭제할 수 있습니다.</span>
        <button
          type="button"
          style={{
            ...styles.dangerBtn,
            ...(bulkDeleting || (totals?.total ?? 0) === 0 ? styles.btnDisabled : {}),
          }}
          onClick={onDeleteAll}
          disabled={bulkDeleting || (totals?.total ?? 0) === 0}
        >
          {bulkDeleting
            ? '삭제 중…'
            : `전국 수정요청 전부 삭제 (${totals?.total ?? 0}건)`}
        </button>
      </div>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>시도</th>
            <th style={styles.th}>시군구</th>
            <th style={styles.th}>읍면</th>
            <th style={styles.th}>행정코드</th>
            <th style={styles.thNum}>전체</th>
            <th style={styles.thNum}>처리대기</th>
            <th style={styles.thNum}>반영됨</th>
            <th style={styles.thNum}>반려</th>
            <th style={styles.th}>최근 요청</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const open = expanded === r.adm_cd;
            const regionItems = byAdm.get(r.adm_cd) ?? [];
            return (
              <Fragment key={r.adm_cd}>
                <tr
                  style={{ ...styles.tr, ...styles.clickableRow }}
                  onClick={() => setExpanded(open ? null : r.adm_cd)}
                >
                  <td style={styles.td}>{r.sido_nm}</td>
                  <td style={styles.td}>{r.sgg_nm}</td>
                  <td style={styles.tdName}>
                    <span style={styles.caret}>{open ? '▾' : '▸'}</span> {r.adm_nm}
                  </td>
                  <td style={styles.tdCode}>{r.adm_cd}</td>
                  <td style={styles.tdNum}>{r.total}</td>
                  <td style={{ ...styles.tdNum, ...emph(r.pending, '#b45309') }}>
                    {r.pending}
                  </td>
                  <td style={styles.tdNum}>{r.applied}</td>
                  <td style={{ ...styles.tdNum, ...emph(r.rejected, '#b91c1c') }}>
                    {r.rejected}
                  </td>
                  <td style={styles.tdDate}>{fmt(r.last_request_at)}</td>
                </tr>
                {open && (
                  <tr>
                    <td colSpan={9} style={styles.detailCell}>
                      {regionItems.length === 0 ? (
                        <div style={styles.note}>개별 요청을 불러오지 못했습니다.</div>
                      ) : (
                        <table style={styles.subTable}>
                          <thead>
                            <tr>
                              <th style={styles.subTh}>종류</th>
                              <th style={styles.subTh}>상태</th>
                              <th style={styles.subTh}>요청내용</th>
                              <th style={styles.subTh}>작성자</th>
                              <th style={styles.subTh}>등록</th>
                              <th style={styles.subThRight}>관리</th>
                            </tr>
                          </thead>
                          <tbody>
                            {regionItems.map((it) => (
                              <tr key={it.id} style={styles.subTr}>
                                <td style={styles.subTd}>{KIND_LABEL[it.kind]}</td>
                                <td style={{ ...styles.subTd, color: STATUS_COLOR[it.status], fontWeight: 600 }}>
                                  {STATUS_LABEL[it.status]}
                                </td>
                                <td style={styles.subTdNote}>{it.note || '-'}</td>
                                <td style={styles.subTd}>{it.created_by || '-'}</td>
                                <td style={styles.subTdDate}>{fmt(it.created_at)}</td>
                                <td style={styles.subTdRight}>
                                  <button
                                    type="button"
                                    style={{
                                      ...styles.delBtn,
                                      ...(deletingIds.has(it.id) ? styles.btnDisabled : {}),
                                    }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      onDeleteOne(it);
                                    }}
                                    disabled={deletingIds.has(it.id)}
                                  >
                                    {deletingIds.has(it.id) ? '삭제 중…' : '삭제'}
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

function UploadTable({ rows }: { rows: UploadHistory[] }) {
  if (rows.length === 0)
    return <div style={styles.note}>업로드된 데이터가 없습니다.</div>;
  return (
    <table style={styles.table}>
      <thead>
        <tr>
          <th style={styles.th}>시도</th>
          <th style={styles.th}>시군구</th>
          <th style={styles.th}>읍면</th>
          <th style={styles.th}>행정코드</th>
          <th style={styles.th}>지도데이터 업로드</th>
          <th style={styles.thNum}>경계 건수</th>
          <th style={styles.th}>경계 최종 업로드</th>
          <th style={styles.th}>경계 작업자</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.adm_cd} style={styles.tr}>
            <td style={styles.td}>{r.sido_nm}</td>
            <td style={styles.td}>{r.sgg_nm}</td>
            <td style={styles.tdName}>{r.adm_nm}</td>
            <td style={styles.tdCode}>{r.adm_cd}</td>
            <td style={styles.tdDate}>
              {r.cog_published_at ? (
                fmt(r.cog_published_at)
              ) : (
                <span style={styles.muted}>미업로드</span>
              )}
            </td>
            <td style={styles.tdNum}>{r.boundary_count ?? 0}</td>
            <td style={styles.tdDate}>
              {r.boundary_updated_at ? (
                fmt(r.boundary_updated_at)
              ) : (
                <span style={styles.muted}>미업로드</span>
              )}
            </td>
            <td style={styles.td}>{r.boundary_updated_by || '-'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color?: string;
}) {
  return (
    <div style={styles.statBox}>
      <div style={{ ...styles.statValue, ...(color ? { color } : {}) }}>
        {value.toLocaleString()}
      </div>
      <div style={styles.statLabel}>{label}</div>
    </div>
  );
}

function emph(n: number, color: string): React.CSSProperties {
  return n > 0 ? { color, fontWeight: 700 } : {};
}

// 시각은 한국시간(KST)으로 표시 — 공용 formatKST 사용. 값 없으면 '-'.
const fmt = (s?: string | null) => formatKST(s, '-');

const styles: Record<string, React.CSSProperties> = {
  page: {
    height: '100vh',
    display: 'flex',
    flexDirection: 'column',
    background: '#f7f8fa',
  },
  bar: {
    height: 48,
    minHeight: 48,
    background: '#1f2937',
    color: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 16px',
  },
  barLeft: { display: 'flex', alignItems: 'center', gap: 14 },
  barRight: { display: 'flex', alignItems: 'center', gap: 12 },
  back: {
    padding: '6px 12px',
    border: '1px solid #4b5563',
    background: '#374151',
    color: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 13,
  },
  title: { fontSize: 15, fontWeight: 600 },
  user: { fontSize: 12, color: '#cbd5e1' },
  logout: {
    padding: '4px 10px',
    border: '1px solid #4b5563',
    background: '#374151',
    color: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
  },
  tabs: {
    display: 'flex',
    gap: 4,
    padding: '10px 16px 0',
    borderBottom: '1px solid #d0d3da',
    background: '#fff',
  },
  tab: {
    padding: '8px 16px',
    border: '1px solid transparent',
    borderBottom: 'none',
    background: 'transparent',
    color: '#6b7280',
    borderRadius: '6px 6px 0 0',
    cursor: 'pointer',
    fontSize: 14,
  },
  tabActive: {
    padding: '8px 16px',
    border: '1px solid #d0d3da',
    borderBottom: '1px solid #fff',
    background: '#fff',
    color: '#1f2937',
    fontWeight: 600,
    borderRadius: '6px 6px 0 0',
    cursor: 'pointer',
    fontSize: 14,
    marginBottom: -1,
  },
  content: { flex: 1, overflow: 'auto', padding: 16 },
  note: { color: '#6b7280', fontSize: 14, padding: 24, textAlign: 'center' },
  error: {
    color: '#b91c1c',
    fontSize: 14,
    padding: 16,
    background: '#fef2f2',
    border: '1px solid #fca5a5',
    borderRadius: 6,
  },
  summary: { display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' },
  statBox: {
    minWidth: 96,
    padding: '10px 16px',
    background: '#fff',
    border: '1px solid #e5e7eb',
    borderRadius: 8,
    textAlign: 'center',
  },
  statValue: { fontSize: 22, fontWeight: 700, color: '#1f2937' },
  statLabel: { fontSize: 12, color: '#6b7280', marginTop: 2 },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    background: '#fff',
    fontSize: 13,
    border: '1px solid #e5e7eb',
    borderRadius: 8,
    overflow: 'hidden',
  },
  th: {
    textAlign: 'left',
    padding: '10px 12px',
    background: '#f3f4f6',
    borderBottom: '1px solid #e5e7eb',
    color: '#374151',
    fontWeight: 600,
    whiteSpace: 'nowrap',
  },
  thNum: {
    textAlign: 'right',
    padding: '10px 12px',
    background: '#f3f4f6',
    borderBottom: '1px solid #e5e7eb',
    color: '#374151',
    fontWeight: 600,
    whiteSpace: 'nowrap',
  },
  tr: { borderBottom: '1px solid #f0f1f3' },
  td: { padding: '8px 12px', color: '#374151', whiteSpace: 'nowrap' },
  tdName: {
    padding: '8px 12px',
    color: '#1f2937',
    fontWeight: 600,
    whiteSpace: 'nowrap',
  },
  tdNum: {
    padding: '8px 12px',
    color: '#374151',
    textAlign: 'right',
    fontVariantNumeric: 'tabular-nums',
  },
  tdCode: {
    padding: '8px 12px',
    color: '#6b7280',
    whiteSpace: 'nowrap',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    fontVariantNumeric: 'tabular-nums',
  },
  tdDate: {
    padding: '8px 12px',
    color: '#6b7280',
    whiteSpace: 'nowrap',
    fontVariantNumeric: 'tabular-nums',
  },
  muted: { color: '#9ca3af' },

  // 전부 삭제 툴바
  toolbarRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    marginBottom: 10,
    flexWrap: 'wrap',
  },
  hintText: { fontSize: 12, color: '#6b7280' },
  dangerBtn: {
    padding: '8px 14px',
    border: '1px solid #b91c1c',
    background: '#b91c1c',
    color: '#fff',
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: 13,
    fontWeight: 600,
    whiteSpace: 'nowrap',
  },
  btnDisabled: { opacity: 0.5, cursor: 'not-allowed' },

  // 펼침 행 / 카라트
  clickableRow: { cursor: 'pointer' },
  caret: { color: '#9ca3af', fontSize: 11 },

  // 개별 요청 상세(펼침)
  detailCell: { padding: '0 12px 10px', background: '#fafbfc' },
  subTable: {
    width: '100%',
    borderCollapse: 'collapse',
    background: '#fff',
    fontSize: 12,
    border: '1px solid #e5e7eb',
    borderRadius: 6,
    overflow: 'hidden',
  },
  subTh: {
    textAlign: 'left',
    padding: '6px 10px',
    background: '#eef0f3',
    borderBottom: '1px solid #e5e7eb',
    color: '#4b5563',
    fontWeight: 600,
    whiteSpace: 'nowrap',
  },
  subThRight: {
    textAlign: 'right',
    padding: '6px 10px',
    background: '#eef0f3',
    borderBottom: '1px solid #e5e7eb',
    color: '#4b5563',
    fontWeight: 600,
    whiteSpace: 'nowrap',
  },
  subTr: { borderBottom: '1px solid #f0f1f3' },
  subTd: { padding: '6px 10px', color: '#374151', whiteSpace: 'nowrap' },
  subTdNote: {
    padding: '6px 10px',
    color: '#374151',
    maxWidth: 360,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  subTdDate: {
    padding: '6px 10px',
    color: '#6b7280',
    whiteSpace: 'nowrap',
    fontVariantNumeric: 'tabular-nums',
  },
  subTdRight: { padding: '6px 10px', textAlign: 'right', whiteSpace: 'nowrap' },
  delBtn: {
    padding: '3px 10px',
    border: '1px solid #fca5a5',
    background: '#fff',
    color: '#dc2626',
    borderRadius: 4,
    fontSize: 12,
    cursor: 'pointer',
    fontWeight: 600,
  },
};
