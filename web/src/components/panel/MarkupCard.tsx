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

// 처리대기 0 / 승인 1 / 반려 2 — 요구사항 표기
const STATUS_LABEL: Record<MarkupStatus, string> = {
  pending: '처리대기(0)',
  applied: '승인(1)',
  rejected: '반려(2)',
};
const STATUS_COLOR: Record<MarkupStatus, string> = {
  pending: '#6b7280',
  applied: '#15803d',
  rejected: '#b91c1c',
};

type Props = {
  item: Markup;
  selected?: boolean;
  onClick?: () => void;
  onApply?: () => void;
  onReject?: () => void;
};

export default function MarkupCard({
  item,
  selected,
  onClick,
  onApply,
  onReject,
}: Props) {
  const note =
    (item.attrs?.note as string | undefined) ?? defaultNote(item.kind);

  return (
    <div
      style={{ ...styles.card, ...(selected ? styles.cardSelected : {}) }}
      onClick={onClick}
    >
      <div style={styles.head}>
        <span style={{ ...styles.badge, background: KIND_COLOR[item.kind] }}>
          [{KIND_LABEL[item.kind]}]
        </span>
        <span style={{ ...styles.statusBadge, color: STATUS_COLOR[item.status] }}>
          {STATUS_LABEL[item.status]}
        </span>
      </div>
      <div style={styles.body}>요청: {note}</div>
      <div style={styles.meta}>
        작업자 {item.created_by || '-'} · {fmt(item.created_at)}
      </div>
      <div style={styles.actions}>
        <button
          type="button"
          style={item.status === 'applied' ? styles.btnDone : styles.btn}
          disabled={item.status !== 'pending'}
          onClick={(e) => {
            e.stopPropagation();
            onApply?.();
          }}
        >
          반영
        </button>
        <button
          type="button"
          style={item.status === 'rejected' ? styles.btnDone : styles.btn}
          disabled={item.status !== 'pending'}
          onClick={(e) => {
            e.stopPropagation();
            onReject?.();
          }}
        >
          반려
        </button>
      </div>
      {item.status === 'applied' && (
        <div style={styles.footnote}>
          승인 {item.applied_by || '-'} · {fmt(item.applied_at)}
        </div>
      )}
      {item.status === 'rejected' && (
        <div style={styles.footnote}>
          반려 {item.rejected_by || '-'} · {fmt(item.rejected_at)}
          {item.reject_reason && (
            <div style={styles.reason}>사유: {item.reject_reason}</div>
          )}
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
};
