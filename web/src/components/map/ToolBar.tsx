import type { MarkupKind } from '../../types';

export type ToolId = MarkupKind | null;

type Props = {
  active: ToolId;
  onChange: (t: ToolId) => void;
  onOpenAdminPicker?: () => void;
  showAdminPicker?: boolean;
  adminLabel?: string;
  userId?: string;
  onLogout?: () => void;
  // 발주처 총괄(00000000) 일 때만 전달 — '관리 현황' 진입 버튼.
  onOpenAdmin?: () => void;
  // false 면 편집 도구(라인등록/삭제표기/속성등록) 숨김 — 열람전용(perm_level=2) 계정.
  canEdit?: boolean;
};

// '라인삭제'(delete) 툴은 혼동을 일으켜 제거 — 요청 삭제는 수정요청 카드의
// [요청삭제] 버튼으로 이동 (MarkupCard).
const BTNS: { id: Exclude<ToolId, null>; label: string }[] = [
  { id: 'add', label: '라인등록' },
  { id: 'delete_mark', label: '삭제표기' },
  { id: 'attr', label: '속성등록' },
];

// 속성등록 옆 자료 다운로드 — web/public/ 루트의 PDF (Vite 가 dist 로 복사해 서빙).
// href 는 한글·공백·괄호가 있어 encodeURI 로 인코딩. download 속성(값 없음)은
// 리소스 원래 파일명(한글)으로 저장되게 한다.
const DOCS: { label: string; file: string }[] = [
  { label: '지역조사표', file: '2025 농림어업총조사(지역조사표).pdf' },
  { label: '사용자 매뉴얼', file: '행정리 경계 검수 웹시스템 사용자 매뉴얼.pdf' },
];

export default function ToolBar({
  active,
  onChange,
  onOpenAdminPicker,
  showAdminPicker,
  adminLabel,
  userId,
  onLogout,
  onOpenAdmin,
  canEdit = true,
}: Props) {
  return (
    <div style={styles.bar}>
      <div style={styles.left}>
        {canEdit &&
          BTNS.map((b) => (
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
        <span style={styles.divider} />
        {DOCS.map((d) => (
          <a
            key={d.file}
            href={encodeURI(`/${d.file}`)}
            download
            style={styles.download}
            title={`${d.file} 다운로드`}
          >
            <span style={styles.dlIcon}>⤓</span> {d.label}
          </a>
        ))}
      </div>
      <div style={styles.right}>
        {onOpenAdmin && (
          <button type="button" style={styles.admin} onClick={onOpenAdmin}>
            관리 현황
          </button>
        )}
        {adminLabel && <span style={styles.adm}>{adminLabel}</span>}
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
    minHeight: 44,
    background: '#f1f3f7',
    borderBottom: '1px solid #d0d3da',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    // 좁은 화면(태블릿/가로)에서 버튼이 넘치면 줄바꿈 — 잘림 방지.
    flexWrap: 'wrap',
    gap: 6,
    padding: '4px 12px',
  },
  left: { display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' },
  divider: {
    width: 1,
    height: 22,
    background: '#d0d3da',
    margin: '0 4px',
  },
  download: {
    ...baseBtn,
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    textDecoration: 'none',
    color: '#0f766e',
    borderColor: '#99c7c1',
    background: '#f0fbf9',
    whiteSpace: 'nowrap',
  },
  dlIcon: { fontSize: 14, fontWeight: 700, lineHeight: 1 },
  right: { display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' },
  btn: baseBtn,
  btnActive: {
    ...baseBtn,
    background: '#1f6feb',
    color: '#fff',
    borderColor: '#1f6feb',
  },
  admin: {
    ...baseBtn,
    padding: '4px 12px',
    fontSize: 12,
    background: '#1f2937',
    color: '#fff',
    borderColor: '#1f2937',
    fontWeight: 600,
  },
  adm: { fontSize: 13, color: '#374151', fontWeight: 500, whiteSpace: 'nowrap' },
  user: { fontSize: 12, color: '#6b7280' },
  logout: {
    ...baseBtn,
    padding: '4px 10px',
    fontSize: 12,
  },
};
