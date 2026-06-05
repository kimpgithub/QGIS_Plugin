import type { MarkupKind } from '../../types';
import { formatPhone } from '../../lib/phone';

export type ToolId = MarkupKind | null;

type Props = {
  active: ToolId;
  onChange: (t: ToolId) => void;
  onOpenAdminPicker?: () => void;
  showAdminPicker?: boolean;
  adminLabel?: string;
  userId?: string;
  contact?: string | null;     // 담당자 업무연락처(내선번호)
  onEditContact?: () => void;  // 내선번호 클릭 시 수정 모달 열기
  onLogout?: () => void;
};

// '라인삭제'(delete) 툴은 혼동을 일으켜 제거 — 요청 삭제는 수정요청 카드의
// [요청삭제] 버튼으로 이동 (MarkupCard).
const BTNS: { id: Exclude<ToolId, null>; label: string }[] = [
  { id: 'add', label: '라인등록' },
  { id: 'delete_mark', label: '삭제표기' },
  { id: 'attr', label: '속성등록' },
];

export default function ToolBar({
  active,
  onChange,
  onOpenAdminPicker,
  showAdminPicker,
  adminLabel,
  userId,
  contact,
  onEditContact,
  onLogout,
}: Props) {
  return (
    <div style={styles.bar}>
      <div style={styles.left}>
        {BTNS.map((b) => (
          <button
            key={b.id}
            type="button"
            style={active === b.id ? styles.btnActive : styles.btn}
            onClick={() => onChange(active === b.id ? null : b.id)}
          >
            {b.label}
          </button>
        ))}
        {showAdminPicker && (
          <button type="button" style={styles.btn} onClick={onOpenAdminPicker}>
            행정읍면 선택
          </button>
        )}
      </div>
      <div style={styles.right}>
        {adminLabel && (
          <span style={styles.adm}>
            <span>{adminLabel}</span>
            {contact &&
              (onEditContact ? (
                <span
                  style={styles.contactBtn}
                  onClick={onEditContact}
                  title="내선번호 수정"
                >
                  ({formatPhone(contact)})
                </span>
              ) : (
                <span style={styles.contact}>({formatPhone(contact)})</span>
              ))}
          </span>
        )}
        {userId && <span style={styles.user}>{userId}</span>}
        {onLogout && (
          <button type="button" style={styles.logout} onClick={onLogout}>
            로그아웃
          </button>
        )}
      </div>
    </div>
  );
}

const baseBtn: React.CSSProperties = {
  padding: '6px 14px',
  border: '1px solid #c9ced6',
  background: '#fff',
  borderRadius: 4,
  cursor: 'pointer',
  fontSize: 13,
};

const styles: Record<string, React.CSSProperties> = {
  bar: {
    height: 44,
    minHeight: 44,
    background: '#f1f3f7',
    borderBottom: '1px solid #d0d3da',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 12px',
  },
  left: { display: 'flex', gap: 6 },
  right: { display: 'flex', gap: 12, alignItems: 'center' },
  btn: baseBtn,
  btnActive: {
    ...baseBtn,
    background: '#1f6feb',
    color: '#fff',
    borderColor: '#1f6feb',
  },
  adm: { fontSize: 13, color: '#374151', fontWeight: 500, whiteSpace: 'nowrap' },
  user: { fontSize: 12, color: '#6b7280' },
  contact: {
    marginLeft: 4,
    fontSize: 13,
    color: '#374151',
    fontWeight: 500,
  },
  contactBtn: {
    marginLeft: 4,
    fontSize: 12,
    color: '#1f6feb',
    fontWeight: 500,
    cursor: 'pointer',
    textDecoration: 'underline',
  },
  logout: {
    ...baseBtn,
    padding: '4px 10px',
    fontSize: 12,
  },
};
