import type { Markup, MarkupKind, MarkupStatus } from '../../types';

const KIND_LABEL: Record<MarkupKind, string> = {
  add: '등록',
  delete: '삭제',
  attr: '속성',
  delete_mark: '삭제표기',
};

const KIND_COLOR: Record<MarkupKind, string> = {
  add: '#1d4ed8',
  delete: '#dc2626',
  attr: '#0f766e',
  delete_mark: '#b45309',
};

// 처리는 전부 웹에서: 대기 → [반영](끝) / 대기 → [반려·사유](끝)
const STATUS_LABEL: Record<MarkupStatus, string> = {
  pending: '처리대기',
  applied: '반영됨',
  rejected: '반려',
};
const STATUS_COLOR: Record<MarkupStatus, string> = {
  pending: '#6b7280',
  applied: '#15803d',
  rejected: '#b91c1c',
};

type Props = {
  item: Markup;
  selected?: boolean;
  // 처리 권한(master=작업자) 여부 — false 면 처리 버튼 숨김(발주자는 열람·등록만)
  canProcess?: boolean;
  onClick?: () => void;
  // 작업자가 QGIS 로 경계를 고친 뒤 누르는 [반영]
  onApply?: () => void;
  // 수행 불가/오요청 [반려] (사유 입력 모달)
  onReject?: () => void;
  // [요청삭제] — 확인 모달을 거쳐 삭제 (반영된 요청은 버튼 숨김)
  onDelete?: () => void;
};

export default function MarkupCard({
  item,
  selected,
  canProcess,
  onClick,
  onApply,
  onReject,
  onDelete,
}: Props) {
  const note =
    (item.attrs?.note as string | undefined) ?? defaultNote(item.kind);

  return (
    <div
      style={{ ...styles.card, ...(selected ? styles.cardSelected : {}) }}
      onClick={onClick}
    >
      <div style={styles.head}>
        {/* 종류 배지 바로 옆에 상태 텍스트 — 오른쪽 자리는 [요청삭제] 버튼 */}
        <div style={styles.headLeft}>
          <span style={{ ...styles.badge, background: KIND_COLOR[item.kind] }}>
            [{KIND_LABEL[item.kind]}]
          </span>
          <span style={{ ...styles.statusBadge, color: STATUS_COLOR[item.status] }}>
            {STATUS_LABEL[item.status]}
          </span>
        </div>
        {/* 처리 완료(반영·반려)된 요청은 [요청삭제] 숨김 — 대기 상태에서만 삭제 가능 */}
        {onDelete && item.status === 'pending' && (
          <button
            type="button"
            style={styles.btnDelete}
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
          >
            요청삭제
          </button>
        )}
      </div>
      <div style={styles.body}>요청: {note}</div>
      <div style={styles.meta}>
        작업자 {item.created_by || '-'} · {fmt(item.created_at)}
      </div>
      {/* 대기: 작업자(master)가 QGIS 로 경계를 고친 뒤 [반영], 또는 [반려](사유). */}
      {canProcess && item.status === 'pending' && (
        <div style={styles.actions}>
          <button
            type="button"
            style={styles.btnApply}
            onClick={(e) => {
              e.stopPropagation();
              onApply?.();
            }}
          >
            반영
          </button>
          <button
            type="button"
            style={styles.btn}
            onClick={(e) => {
              e.stopPropagation();
              onReject?.();
            }}
          >
            반려
          </button>
        </div>
      )}
      {item.status === 'applied' && (
        <div style={styles.footnote}>
          반영 {item.applied_by || '-'} · {fmt(item.applied_at)}
        </div>
      )}
      {item.status === 'rejected' && (
        <div style={styles.footnote}>
          반려 {item.rejected_by || '-'} · {fmt(item.rejected_at)}
          {item.reject_reason && (
            <div style={styles.reason}>사유: {item.reject_reason}</div>
          )}
          <div style={styles.hint}>보완이 필요하면 새 수정요청을 등록하세요</div>
        </div>
      )}
    </div>
  );
}

function defaultNote(k: MarkupKind) {
  switch (k) {
    case 'add': return '행정리 적용 필요한 위치 신규 라인 등록';
    case 'delete': return '잘못된 경계 라인 삭제';
    case 'attr': return '행정리 명칭 및 부호 등록요청';
    case 'delete_mark': return '경계 부정확 — 삭제표기';
  }
}

function fmt(s?: string | null): string {
  if (!s) return '';
  // ISO → "YYYY-MM-DD HH:mm"
  return s.replace('T', ' ').slice(0, 16);
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    border: '1px solid #d0d3da',
    borderRadius: 6,
    padding: 10,
    background: '#fff',
    cursor: 'pointer',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  cardSelected: {
    border: '1px solid #1f6feb',
    boxShadow: '0 0 0 2px rgba(31,111,235,0.12)',
  },
  head: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 11,
  },
  headLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  btnDelete: {
    padding: '2px 8px',
    border: '1px solid #fca5a5',
    background: '#fff',
    color: '#dc2626',
    borderRadius: 3,
    fontSize: 11,
    cursor: 'pointer',
  },
  badge: {
    color: '#fff',
    fontSize: 11,
    padding: '2px 8px',
    borderRadius: 3,
    fontWeight: 600,
  },
  meta: { color: '#6b7280', fontSize: 11 },
  statusBadge: { fontSize: 11, fontWeight: 600 },
  body: { fontSize: 13, color: '#1f2937', lineHeight: 1.4 },
  actions: { display: 'flex', gap: 6 },
  btn: {
    flex: 1,
    padding: '4px 0',
    border: '1px solid #c9ced6',
    background: '#fff',
    borderRadius: 4,
    fontSize: 12,
    cursor: 'pointer',
  },
  btnApply: {
    flex: 1,
    padding: '4px 0',
    border: '1px solid #15803d',
    background: '#15803d',
    color: '#fff',
    borderRadius: 4,
    fontSize: 12,
    cursor: 'pointer',
  },
  btnDone: {
    flex: 1,
    padding: '4px 0',
    border: '1px solid #d0d3da',
    background: '#eef0f3',
    color: '#9ca3af',
    borderRadius: 4,
    fontSize: 12,
  },
  footnote: { fontSize: 11, color: '#6b7280' },
  reason: { marginTop: 2, color: '#374151' },
  hint: { marginTop: 4, color: '#9ca3af' },
};
