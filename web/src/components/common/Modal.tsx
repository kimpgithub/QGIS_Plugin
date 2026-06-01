import { useRef, useState, type ReactNode } from 'react';

type Props = {
  open: boolean;
  title?: string;
  onClose: () => void;
  children: ReactNode;
  width?: number;
  // false 면 배경을 어둡게 하지 않고 지도 조작도 막지 않는다 — 지도(스캔 이미지)를
  // 보면서 입력해야 하는 모달용(속성등록 등). 바깥 클릭으로 닫히지 않음.
  dim?: boolean;
};

export default function Modal({
  open,
  title,
  onClose,
  children,
  width = 360,
  dim = true,
}: Props) {
  // 제목줄 드래그로 모달 이동. null = 기본 위치(화면 중앙).
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);

  // 닫혔다 다시 열리면 위치를 중앙으로 초기화 (렌더 중 상태 보정 — effect 불필요)
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) setPos(null);
  }

  if (!open) return null;

  function onDragStart(e: React.MouseEvent) {
    const card = cardRef.current;
    if (!card) return;
    e.preventDefault();
    const rect = card.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const origX = rect.left;
    const origY = rect.top;
    const onMove = (ev: MouseEvent) => {
      setPos({
        x: origX + (ev.clientX - startX),
        y: origY + (ev.clientY - startY),
      });
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  return (
    <div
      style={{
        ...styles.backdrop,
        ...(dim ? {} : styles.backdropClear),
      }}
      onClick={dim ? onClose : undefined}
    >
      <div
        ref={cardRef}
        style={{
          ...styles.card,
          width,
          ...(pos ? { position: 'fixed' as const, left: pos.x, top: pos.y } : {}),
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={styles.head}
          onMouseDown={onDragStart}
          title="드래그해서 이동"
        >
          <span>{title}</span>
          <button
            type="button"
            style={styles.close}
            onClick={onClose}
            onMouseDown={(e) => e.stopPropagation()}
          >
            ×
          </button>
        </div>
        <div style={styles.body}>{children}</div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.35)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
  },
  // dim=false — 배경 안 어둡게 + 지도 클릭/이동/줌 그대로 가능 (모달 카드만 조작을 받음)
  backdropClear: {
    background: 'transparent',
    pointerEvents: 'none',
  },
  card: {
    background: '#fff',
    borderRadius: 6,
    boxShadow: '0 6px 30px rgba(0,0,0,0.2)',
    display: 'flex',
    flexDirection: 'column',
    pointerEvents: 'auto',
  },
  head: {
    padding: '10px 14px',
    borderBottom: '1px solid #d0d3da',
    fontSize: 13,
    fontWeight: 600,
    color: '#1f2937',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    cursor: 'move',          // 제목줄 드래그로 모달 이동
    userSelect: 'none',
  },
  body: { padding: 14 },
  close: {
    background: 'transparent',
    border: 'none',
    fontSize: 18,
    cursor: 'pointer',
    color: '#6b7280',
  },
};
