import { useEffect, useMemo, useState } from 'react';
import Modal from '../common/Modal';
import {
  downloadMarkupExport,
  getMarkupExportSummary,
  type MarkupExportSummary,
} from '../../api/admin';

type Props = {
  open: boolean;
  onClose: () => void;
};

// 전국/시도별 수정요청 공간정보를 GeoJSON(kind별) ZIP 으로 내려받는 모달.
// - '전국 전체' 선택 시 개별 시도 선택은 비활성(전국이 전부 포함).
// - 데이터(미처리 수정요청) 있는 시도만 목록에 노출, 건수 표시.
export default function MarkupExportModal({ open, onClose }: Props) {
  const [summary, setSummary] = useState<MarkupExportSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [nation, setNation] = useState(false); // 전국 전체 선택
  const [sel, setSel] = useState<Set<string>>(new Set()); // 선택된 시도코드
  const [busy, setBusy] = useState(false);

  // 열릴 때마다 요약 로드 + 선택 초기화
  useEffect(() => {
    if (!open) return;
    setNation(false);
    setSel(new Set());
    setErr(null);
    setLoading(true);
    getMarkupExportSummary('pending')
      .then(setSummary)
      .catch(() => setErr('현황을 불러오지 못했습니다.'))
      .finally(() => setLoading(false));
  }, [open]);

  const sidoList = summary?.sido ?? [];
  const nationTotal = summary?.nation.total ?? 0;

  const selectedCount = useMemo(() => {
    if (nation) return nationTotal;
    return sidoList
      .filter((s) => sel.has(s.sido_cd))
      .reduce((a, s) => a + s.total, 0);
  }, [nation, sel, sidoList, nationTotal]);

  const canDownload = !busy && (nation ? nationTotal > 0 : sel.size > 0);

  function toggleSido(cd: string) {
    setSel((prev) => {
      const next = new Set(prev);
      if (next.has(cd)) next.delete(cd);
      else next.add(cd);
      return next;
    });
  }

  async function onDownload() {
    const scopes = nation ? ['all'] : Array.from(sel);
    if (!scopes.length) return;
    setBusy(true);
    setErr(null);
    try {
      await downloadMarkupExport(scopes, 'pending');
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : '다운로드에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} title="공간정보 다운로드 (전국/시도)" onClose={onClose} width={480}>
      <div style={styles.note}>
        미처리 수정요청을 라인등록·삭제표기·속성등록 GeoJSON 으로 묶어 ZIP 으로 내려받습니다.
      </div>

      {loading ? (
        <div style={styles.empty}>불러오는 중…</div>
      ) : nationTotal === 0 ? (
        <div style={styles.empty}>내보낼 미처리 수정요청이 없습니다.</div>
      ) : (
        <>
          {/* 전국 전체 */}
          <label style={styles.nationRow}>
            <input
              type="checkbox"
              checked={nation}
              onChange={(e) => setNation(e.target.checked)}
            />
            <span style={styles.nationLabel}>전국 전체</span>
            <span style={styles.count}>{nationTotal}건</span>
          </label>

          <div style={styles.divider} />

          {/* 시도 목록 (전국 선택 시 비활성) */}
          <div style={{ ...styles.grid, opacity: nation ? 0.4 : 1 }}>
            {sidoList.map((s) => (
              <label key={s.sido_cd} style={styles.sidoRow}>
                <input
                  type="checkbox"
                  disabled={nation}
                  checked={sel.has(s.sido_cd)}
                  onChange={() => toggleSido(s.sido_cd)}
                />
                <span style={styles.sidoNm}>{s.sido_nm}</span>
                <span style={styles.count}>{s.total}</span>
              </label>
            ))}
          </div>
        </>
      )}

      {err && <div style={styles.err}>{err}</div>}

      <div style={styles.actions}>
        <span style={styles.selInfo}>
          {selectedCount > 0 ? `선택 ${selectedCount}건` : '선택 없음'}
        </span>
        <button
          type="button"
          style={{ ...styles.dlBtn, ...(canDownload ? {} : styles.dlBtnOff) }}
          onClick={onDownload}
          disabled={!canDownload}
        >
          {busy ? '준비 중…' : '선택 다운로드 (ZIP)'}
        </button>
      </div>
    </Modal>
  );
}

const styles: Record<string, React.CSSProperties> = {
  note: {
    fontSize: 12,
    color: '#6b7280',
    lineHeight: 1.6,
    marginBottom: 12,
  },
  empty: {
    textAlign: 'center',
    color: '#9ca3af',
    fontSize: 13,
    padding: '24px 0',
  },
  nationRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 4px',
    fontSize: 14,
    cursor: 'pointer',
  },
  nationLabel: { fontWeight: 600, color: '#1f2937' },
  divider: { height: 1, background: '#e5e7eb', margin: '4px 0 10px' },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
    gap: 6,
    maxHeight: 260,
    overflowY: 'auto',
  },
  sidoRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '5px 6px',
    fontSize: 13,
    color: '#374151',
    cursor: 'pointer',
    border: '1px solid #eef0f3',
    borderRadius: 4,
  },
  sidoNm: { flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  count: {
    fontSize: 12,
    color: '#6b7280',
    fontFamily: 'ui-monospace, Consolas, monospace',
  },
  err: {
    marginTop: 10,
    color: '#b91c1c',
    fontSize: 12,
    textAlign: 'center',
  },
  actions: {
    marginTop: 14,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
    flexWrap: 'wrap',
  },
  selInfo: { fontSize: 12, color: '#6b7280' },
  dlBtn: {
    padding: '9px 16px',
    background: '#0f766e',
    color: '#fff',
    border: 'none',
    borderRadius: 4,
    fontSize: 14,
    cursor: 'pointer',
  },
  dlBtnOff: { background: '#c9ced6', cursor: 'not-allowed' },
};
