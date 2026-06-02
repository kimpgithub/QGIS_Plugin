// 그리기 툴 활성 시 지도 위에 뜨는 안내·조작 바.
// 그리는 중에는 [마지막 점 취소](라인 한정) / [그리기 취소], 대기 중에는 [툴 종료].
// 단축키: Backspace=마지막 점 취소, Esc=그리기 취소 또는 툴 종료 (InspectPage 에서 처리).
import type { MarkupKind } from '../../types';

type Props = {
  kind: MarkupKind;
  drawing: boolean;       // drawstart~drawend 사이 (그리는 중)
  isLine: boolean;        // 라인 툴(add)만 점 취소 노출
  onUndoPoint: () => void;
  onAbort: () => void;    // 그리던 도형 전체 취소
  onExit: () => void;     // 툴 종료(비활성)
};

const KIND_LABEL: Record<MarkupKind, string> = {
  add: '라인등록',
  delete: '라인삭제',
  attr: '속성등록',
  delete_mark: '삭제표기',
};

export default function DrawHint({
  kind,
  drawing,
  isLine,
  onUndoPoint,
  onAbort,
  onExit,
}: Props) {
  const label = KIND_LABEL[kind];

  let guide: string;
  if (kind === 'delete_mark') {
    // 경계선에 스냅해 구간을 그림.
    guide = drawing
      ? `${label} 중 — 경계선을 따라 클릭(자동 스냅), 더블클릭으로 완료`
      : `${label} — 경계선 위를 클릭하면 자동 스냅, 구간을 그어 표시하세요`;
  } else if (drawing) {
    guide = isLine
      ? `${label} 중 — 클릭으로 점 추가, 더블클릭으로 완료`
      : `${label} 중`;
  } else {
    guide = isLine
      ? `${label} — 클릭으로 점을 찍고 더블클릭으로 완료하세요`
      : `${label} — 지도를 클릭해 위치를 지정하세요`;
  }

  return (
    <div style={styles.bar}>
      <span style={styles.icon}>✏</span>
      <span style={styles.guide}>{guide}</span>
      <div style={styles.btns}>
        {drawing ? (
          <>
            {isLine && (
              <button
                type="button"
                style={styles.btn}
                onClick={onUndoPoint}
                title="Backspace"
              >
                ↶ 마지막 점 취소
              </button>
            )}
            <button
              type="button"
              style={styles.btnDanger}
              onClick={onAbort}
              title="Esc"
            >
              ✕ 그리기 취소
            </button>
          </>
        ) : (
          <button
            type="button"
            style={styles.btn}
            onClick={onExit}
            title="Esc"
          >
            ✕ 툴 종료
          </button>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    position: 'absolute',
    top: 12,
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: 20,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '7px 12px',
    background: 'rgba(17,24,39,0.92)',
    color: '#fff',
    borderRadius: 8,
    boxShadow: '0 2px 10px rgba(0,0,0,0.25)',
    fontSize: 13,
    maxWidth: 'calc(100% - 24px)',
  },
  icon: { fontSize: 14, lineHeight: 1 },
  guide: { whiteSpace: 'nowrap' },
  btns: { display: 'flex', gap: 6, marginLeft: 4 },
  btn: {
    padding: '4px 10px',
    border: '1px solid rgba(255,255,255,0.35)',
    background: 'rgba(255,255,255,0.08)',
    color: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
    whiteSpace: 'nowrap',
  },
  btnDanger: {
    padding: '4px 10px',
    border: '1px solid #f87171',
    background: 'rgba(220,38,38,0.85)',
    color: '#fff',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 12,
    whiteSpace: 'nowrap',
  },
};
