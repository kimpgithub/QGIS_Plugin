import { useMemo, useState } from 'react';
import Modal from '../common/Modal';
import { setAccountPerm } from '../../api/admin';
import type { AdminUnit, PermLevel } from '../../types';

// 권한 3단계 — 세그먼트 버튼. 색상으로 상태 구분.
const PERM_OPTS: { level: PermLevel; label: string; title: string }[] = [
  { level: 1, label: '정상', title: '정상 이용(기본)' },
  { level: 2, label: '편집회수', title: '열람 전용 — 등록/체크/요청취소 차단' },
  { level: 3, label: '접근회수', title: '로그인 불가' },
];
// 최근 카드 일시 — UTC ISO → 한국시간(KST) 'YY/MM/DD hh:mm:ss'.
function fmtCardTime(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: '2-digit', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).formatToParts(d);
  const g = (t: string) => parts.find((x) => x.type === t)?.value ?? '';
  return `${g('year')}/${g('month')}/${g('day')} ${g('hour')}:${g('minute')}:${g('second')}`;
}

const PERM_ACTIVE: Record<PermLevel, React.CSSProperties> = {
  1: { background: '#dcfce7', borderColor: '#16a34a', color: '#15803d', fontWeight: 600 },
  2: { background: '#fef3c7', borderColor: '#d97706', color: '#b45309', fontWeight: 600 },
  3: { background: '#fee2e2', borderColor: '#dc2626', color: '#b91c1c', fontWeight: 600 },
};

type Props = {
  open: boolean;
  admins: AdminUnit[];
  onCancel: () => void;
  onSelect: (a: AdminUnit) => void;
};

export default function AdminPickerModal({
  open,
  admins,
  onCancel,
  onSelect,
}: Props) {
  const [q, setQ] = useState('');
  const [sido, setSido] = useState('');
  const [sgg, setSgg] = useState('');
  // 권한 변경 낙관적 갱신 — adm_cd → level. 서버 값(admins) 위에 덮어씀.
  const [permOverride, setPermOverride] = useState<Record<string, PermLevel>>({});
  const [permBusy, setPermBusy] = useState<Record<string, boolean>>({});

  const levelOf = (a: AdminUnit): PermLevel =>
    permOverride[a.adm_cd] ?? a.perm_level ?? 1;

  async function changePerm(a: AdminUnit, level: PermLevel) {
    const cd = a.adm_cd;
    if (permBusy[cd] || levelOf(a) === level) return;
    const label = PERM_OPTS.find((p) => p.level === level)?.label ?? '';
    const ok = window.confirm(
      `'${a.adm_nm}'(${cd}) 계정의 권한을 '${label}'(으)로 변경하시겠습니까?\n` +
        `\n변경하면 해당 계정이 로그인 중인 경우 자동으로 로그아웃됩니다.`
    );
    if (!ok) return;
    const prev = levelOf(a);
    setPermBusy((s) => ({ ...s, [cd]: true }));
    setPermOverride((s) => ({ ...s, [cd]: level }));
    try {
      await setAccountPerm(cd, level);
    } catch {
      setPermOverride((s) => ({ ...s, [cd]: prev }));
      alert('권한 변경에 실패했습니다.');
    } finally {
      setPermBusy((s) => {
        const { [cd]: _drop, ...rest } = s;
        return rest;
      });
    }
  }

  const sidoOpts = useMemo(
    () =>
      uniqueBy(admins, 'sido_cd').map((a) => ({
        cd: a.sido_cd,
        nm: a.sido_nm,
      })),
    [admins]
  );

  const sggOpts = useMemo(() => {
    const list = sido ? admins.filter((a) => a.sido_cd === sido) : admins;
    return uniqueBy(list, 'sigungu_cd').map((a) => ({
      cd: a.sigungu_cd,
      nm: a.sigungu_nm,
    }));
  }, [admins, sido]);

  const rows = useMemo(() => {
    let list = admins;
    if (sido) list = list.filter((a) => a.sido_cd === sido);
    if (sgg) list = list.filter((a) => a.sigungu_cd === sgg);
    if (q) {
      const needle = q.toLowerCase();
      list = list.filter(
        (a) =>
          a.adm_cd.includes(needle) ||
          a.adm_nm.toLowerCase().includes(needle)
      );
    }
    return list.slice(0, 300);
  }, [admins, sido, sgg, q]);

  return (
    <Modal open={open} title="행정읍면 선택" onClose={onCancel} width={760}>
      <div style={styles.filters}>
        <select
          style={styles.sel}
          value={sido}
          onChange={(e) => {
            setSido(e.target.value);
            setSgg('');
          }}
        >
          <option value="">전체 시·도</option>
          {sidoOpts.map((o) => (
            <option key={o.cd} value={o.cd}>
              {o.nm}
            </option>
          ))}
        </select>
        <select
          style={styles.sel}
          value={sgg}
          onChange={(e) => setSgg(e.target.value)}
        >
          <option value="">전체 시·군·구</option>
          {sggOpts.map((o) => (
            <option key={o.cd} value={o.cd}>
              {o.nm}
            </option>
          ))}
        </select>
        <input
          style={styles.q}
          placeholder="코드 또는 명칭 검색"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>
      <div style={styles.list}>
        <div style={styles.headRow}>
          <div style={styles.headMain}>
            <span style={styles.hInfo}>행정읍면</span>
            <span style={styles.grpProgress}>
              <span style={styles.hBadge} title="완료체크 수 / 보완사항 수">
                진척
              </span>
              <span style={styles.hTime}>최근 수정요청</span>
            </span>
          </div>
          <span style={styles.hPerm}>권한</span>
        </div>
        {rows.length === 0 && <div style={styles.empty}>일치하는 항목 없음</div>}
        {rows.map((a) => (
          <div key={a.adm_cd} style={styles.row}>
            <button
              type="button"
              style={styles.rowMain}
              onClick={() => onSelect(a)}
              title="클릭: 이 읍면 열기"
            >
              <span style={styles.grpInfo}>
                <span style={styles.code}>{a.adm_cd}</span>
                <span style={styles.nameCell}>
                  <span style={styles.nm}>
                    {a.sido_nm} {a.sigungu_nm} {a.adm_nm}
                  </span>
                  {a.has_cog === false && (
                    <span style={styles.noImg} title="스캔 이미지가 없어 경계만 검수합니다">
                      이미지 없음
                    </span>
                  )}
                </span>
              </span>
              <span style={styles.grpProgress}>
                <span
                  style={{
                    ...styles.progress,
                    ...((a.remark_count ?? 0) === 0 ? styles.metaEmpty : {}),
                  }}
                  title="완료체크 수 / 보완사항 수"
                >
                  {(a.remark_count ?? 0) > 0
                    ? `${a.confirmed_count ?? 0}/${a.remark_count}`
                    : '-'}
                </span>
                <span
                  style={{
                    ...styles.cardTime,
                    textAlign: a.latest_card_at ? 'right' : 'center',
                  }}
                  title="최근 카드 등록 일시(KST)"
                >
                  {a.latest_card_at ? fmtCardTime(a.latest_card_at) : '-'}
                </span>
              </span>
            </button>
            <div style={styles.perm}>
              {PERM_OPTS.map((p) => {
                const active = levelOf(a) === p.level;
                return (
                  <button
                    key={p.level}
                    type="button"
                    disabled={permBusy[a.adm_cd]}
                    onClick={() => changePerm(a, p.level)}
                    title={p.title}
                    style={{
                      ...styles.permBtn,
                      ...(active ? PERM_ACTIVE[p.level] : {}),
                    }}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}

function uniqueBy<T, K extends keyof T>(arr: T[], key: K): T[] {
  const seen = new Set<unknown>();
  const out: T[] = [];
  for (const x of arr) {
    const k = x[key];
    if (!seen.has(k)) {
      seen.add(k);
      out.push(x);
    }
  }
  return out;
}

const styles: Record<string, React.CSSProperties> = {
  filters: { display: 'flex', gap: 6, marginBottom: 8 },
  sel: {
    flex: 1,
    padding: '6px 8px',
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    fontSize: 13,
  },
  q: {
    flex: 2,
    padding: '6px 10px',
    border: '1px solid #cbd5e0',
    borderRadius: 4,
    fontSize: 13,
  },
  list: {
    maxHeight: 360,
    overflowY: 'auto',
    border: '1px solid #e5e7eb',
    borderRadius: 4,
  },
  // 상단 고정 헤더 — 데이터 행과 컬럼 폭·간격을 동일하게 맞춰 정렬.
  headRow: {
    position: 'sticky',
    top: 0,
    zIndex: 1,
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    padding: '7px 12px',
    background: '#f8fafc',
    borderBottom: '1px solid #d0d3da',
    fontSize: 12,
    fontWeight: 700,
    color: '#6b7280',
  },
  headMain: { display: 'flex', gap: 32, alignItems: 'center' },
  hInfo: { flex: '0 0 300px' },              // grpInfo 폭과 동일
  hBadge: { width: 60, textAlign: 'center' }, // progress 폭과 동일
  hTime: { width: 128, textAlign: 'right' },  // cardTime 폭과 동일
  hPerm: { marginLeft: 'auto', width: 174, textAlign: 'center' }, // 권한 버튼 묶음 위
  // 3덩어리(지역정보 // 진척 // 권한) — 덩어리 사이 32px.
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 32,
    padding: '6px 12px',
    borderBottom: '1px solid #f1f3f7',
  },
  // 지역정보 // 진척 두 덩어리 — 덩어리 사이 32px.
  rowMain: {
    minWidth: 0,
    display: 'flex',
    gap: 32,
    alignItems: 'center',
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    textAlign: 'left',
    fontSize: 13,
    padding: '3px 0',
  },
  // 덩어리1: 코드 + 지명 (내부 8px). 헤더와 정렬 위해 폭 고정.
  grpInfo: {
    flex: '0 0 300px',
    minWidth: 0,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  // 덩어리2: 진척도 + 시간 (내부 8px)
  grpProgress: {
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  code: {
    fontFamily: 'ui-monospace, Consolas, monospace',
    color: '#1f6feb',
    flexShrink: 0,
  },
  // 지명 셀 — 이름(줄임표) + '이미지 없음' 배지. 길면 줄임, 짧으면 내용폭.
  nameCell: {
    flex: '1 1 auto',
    minWidth: 0,
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  nm: {
    color: '#1f2937',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    minWidth: 0,
  },
  // 진척도 배지 — 내용폭 배지, 요소 사이 간격은 row/rowMain 의 gap 이 담당.
  progress: {
    flexShrink: 0,
    fontSize: 13,
    fontWeight: 700,
    width: 60,
    textAlign: 'center',
    padding: '2px 8px',
    borderRadius: 4,
    background: '#eef2ff',
    color: '#4338ca',
    border: '1px solid #e0e7ff',
    whiteSpace: 'nowrap',
    fontFamily: 'ui-monospace, Consolas, monospace',
  },
  // 보완사항이 없어 '-' 만 표시되는 배지 — 연하게.
  metaEmpty: {
    background: '#f8fafc',
    color: '#9ca3af',
    border: '1px solid #eef1f5',
    fontWeight: 600,
  },
  // 최근 카드 일시 — 고정폭(시간 문자열 폭). 카드 없을 때 '-' 도 같은 폭 차지.
  cardTime: {
    flexShrink: 0,
    width: 128,
    fontSize: 12.5,
    color: '#4b5563',
    whiteSpace: 'nowrap',
    fontFamily: 'ui-monospace, Consolas, monospace',
  },
  // 스캔 이미지 없는 읍면 표시 — 경계만 검수 가능함을 알림.
  noImg: {
    flexShrink: 0,
    fontSize: 10,
    padding: '1px 5px',
    borderRadius: 3,
    background: '#f3f4f6',
    color: '#6b7280',
    border: '1px solid #e5e7eb',
    whiteSpace: 'nowrap',
  },
  // 권한 버튼 묶음 — 오른쪽 끝에 붙여 우측 빈 여백 제거(정보 왼쪽 / 액션 오른쪽).
  perm: { display: 'flex', gap: 8, flexShrink: 0, marginLeft: 'auto' },
  permBtn: {
    fontSize: 11,
    padding: '3px 7px',
    border: '1px solid #d0d3da',
    background: '#fff',
    color: '#6b7280',
    borderRadius: 3,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
  },
  empty: { textAlign: 'center', padding: 16, color: '#9ca3af', fontSize: 12 },
};
