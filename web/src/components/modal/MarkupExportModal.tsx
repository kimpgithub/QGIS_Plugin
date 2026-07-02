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
// - 상태(처리대기/반영됨) 다중선택 — 선택에 따라 건수/다운로드 대상이 바뀜.
// - '전국 전체' 선택 시 개별 시도 선택은 비활성(전국이 전부 포함).
// - 데이터 있는 시도만 목록에 노출, 건수 표시.
export default function MarkupExportModal({ open, onClose }: Props) {
  const [summary, setSummary] = useState<MarkupExportSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [stPending, setStPending] = useState(true); // 처리대기(pending)
  const [stApplied, setStApplied] = useState(false); // 반영됨(applied)
  const [nation, setNation] = useState(false); // 전국 전체 선택
  const [sel, setSel] = useState<Set<string>>(new Set()); // 선택된 시도코드
  const [busy, setBusy] = useState(false);

  const statusParam = [stPending && 'pending', stApplied && 'applied']
    .filter(Boolean)
    .join(',');

  // 열릴 때 지역 선택만 초기화(상태 선택은 직전 값 유지)
  useEffect(() => {
    if (!open) return;
    setNation(false);
    setSel(new Set());
    setErr(null);
  }, [open]);

  // 열림 + 상태선택 변경 시 건수 요약 재로드. 목록에서 사라진 시도는 선택 해제.
  useEffect(() => {
    if (!open) return;
    if (!statusParam) {
      setSummary(null);
      return;
    }
    setLoading(true);
    getMarkupExportSummary(statusParam)
      .then((s) => {
        setSummary(s);
        const codes = new Set(s.sido.map((x) => x.sido_cd));
        setSel((prev) => new Set([...prev].filter((c) => codes.has(c))));
      })
      .catch(() => setErr('현황을 불러오지 못했습니다.'))
      .finally(() => setLoading(false));
  }, [open, statusParam]);

  const sidoList = summary?.sido ?? [];
  const nationTotal = summary?.nation.total ?? 0;

  const selectedCount = useMemo(() => {
    if (nation) return nationTotal;
    return sidoList
      .filter((s) => sel.has(s.sido_cd))
      .reduce((a, s) => a + s.total, 0);
  }, [nation, sel, sidoList, nationTotal]);

  const canDownload =
    !busy && !!statusParam && (nation ? nationTotal > 0 : sel.size > 0);

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
    if (!scopes.length || !statusParam) return;
    setBusy(true);
    setErr(null);
    try {
      await downloadMarkupExport(scopes, statusParam);
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
        선택한 상태의 수정요청을 라인등록·삭제표기·속성등록 GeoJSON 으로 묶어 ZIP 으로 내려받습니다.
      </div>

      {/* 상태 선택 */}
      <div style={styles.statusRow}>
        <span style={styles.statusLabel}>상태</span>
        <label style={styles.stChk}>
          <input
            type="checkbox"
            checked={stPending}
            onChange={(e) => setStPending(e.target.checked)}
          />
          <span>처리대기</span>
        </label>
        <label style={styles.stChk}>
          <input
            type="checkbox"
            checked={stApplied}
            onChange={(e) => setStApplied(e.target.checked)}
          />
          <span>반영됨</span>
        </label>
      </div>

      {!statusParam ? (
        <div style={styles.empty}>상태를 하나 이상 선택하세요.</div>
      ) : loading ? (
        <div style={styles.empty}>불러오는 중…</div>
      ) : nationTotal === 0 ? (
        <div style={styles.empty}>선택한 상태의 수정요청이 없습니다.</div>
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
  statusRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
    padding: '8px 10px',
    background: '#f9fafb',
    border: '1px solid #eef0f3',
    borderRadius: 4,
    marginBottom: 12,
  },
  statusLabel: { fontSize: 13, fontWeight: 600, color: '#374151' },
  stChk: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    fontSize: 13,
    color: '#374151',
    cursor: 'pointer',
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
