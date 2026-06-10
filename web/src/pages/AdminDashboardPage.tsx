import { useEffect, useMemo, useState } from 'react';
import { useAuth } from '../store/AuthContext';
import {
  listMarkupStats,
  listUploadHistory,
  type MarkupStat,
  type UploadHistory,
} from '../api/admin';
import { ApiError } from '../api/client';
import { formatKST } from '../lib/datetime';

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
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    Promise.all([listMarkupStats(), listUploadHistory()])
      .then(([s, u]) => {
        if (!alive) return;
        setStats(s);
        setUploads(u);
      })
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
  }, []);

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
          <MarkupTable rows={stats ?? []} totals={totals} />
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
}: {
  rows: MarkupStat[];
  totals: {
    total: number;
    pending: number;
    applied: number;
    rejected: number;
    regions: number;
  } | null;
}) {
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
          {rows.map((r) => (
            <tr key={r.adm_cd} style={styles.tr}>
              <td style={styles.td}>{r.sido_nm}</td>
              <td style={styles.td}>{r.sgg_nm}</td>
              <td style={styles.tdName}>{r.adm_nm}</td>
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
          ))}
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
          <th style={styles.th}>항공사진 업로드</th>
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
};
