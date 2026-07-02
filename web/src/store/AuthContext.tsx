import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { AuthUser } from '../types';
import { logout as apiLogout, refreshSession } from '../api/auth';
import { getToken, setToken, setUnauthorizedHandler } from '../api/client';

const USER_KEY = 'auth_user';

// ── 미사용(idle) 세션 정책 ───────────────────────────────────────────────
// 마우스/키보드/스크롤/휠/터치 활동이 일정 시간 없으면 자동 로그아웃.
// 만료 5분 전(=55분 미사용)에 경고 다이얼로그를 띄워 [연장]을 받는다.
// 서버 토큰 TTL(JWT_EXPIRES_MIN)은 SESSION_MS 보다 충분히 길게(120분) 잡아,
// 60분 idle 카운트다운 내내 토큰이 유효 → [연장] 갱신이 항상 성공한다.
const SESSION_MS = 60 * 60 * 1000; // 미사용 허용 1시간
const WARN_BEFORE_MS = 5 * 60 * 1000; // 만료 5분 전(=55분 미사용) 경고
const WARN_AT_MS = SESSION_MS - WARN_BEFORE_MS;
const REFRESH_AHEAD_MS = 60 * 60 * 1000; // 활동 중 토큰 잔여수명이 이보다 적으면 갱신
const TICK_MS = 15 * 1000; // 점검 주기
const ACTIVITY_EVENTS = [
  'mousemove',
  'mousedown',
  'keydown',
  'scroll',
  'wheel',
  'touchstart',
] as const;

// JWT payload 의 exp(만료, 초 단위)를 ms 로 변환. 디코드 실패 시 null.
function jwtExpMs(token: string | null): number | null {
  if (!token) return null;
  try {
    const payload = token.split('.')[1];
    const json = JSON.parse(
      atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
    ) as { exp?: number };
    return typeof json.exp === 'number' ? json.exp * 1000 : null;
  } catch {
    return null;
  }
}

type AuthCtx = {
  user: AuthUser | null;
  setUser: (u: AuthUser | null) => void;
  signOut: () => void;
  // 미사용 자동 로그아웃 시 로그인 화면에 띄울 안내 문구(수동 로그아웃 시 null).
  logoutNotice: string | null;
  clearLogoutNotice: () => void;
};

const IDLE_LOGOUT_NOTICE = '1시간 미사용으로 자동 로그아웃되었습니다.';
// 세션 무효(토큰 만료 또는 관리자에 의한 권한 변경)로 자동 로그아웃된 경우.
const SESSION_LOGOUT_NOTICE =
  '세션이 만료되었거나 권한이 변경되어 로그아웃되었습니다. 다시 로그인해 주세요.';

const Ctx = createContext<AuthCtx | null>(null);

function loadUser(): AuthUser | null {
  const t = getToken();
  if (!t) return null;
  try {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    const u = JSON.parse(raw) as AuthUser;
    return u.token === t ? u : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<AuthUser | null>(() => loadUser());
  const [warnOpen, setWarnOpen] = useState(false);
  const [logoutNotice, setLogoutNotice] = useState<string | null>(null);

  const lastActivityRef = useRef<number>(Date.now());
  const warnOpenRef = useRef(false); // true 면 활동으로 세션을 연장하지 않음(경고 표시 중)
  const refreshingRef = useRef(false);

  const setUser = useCallback((u: AuthUser | null) => {
    setUserState(u);
    if (u) {
      localStorage.setItem(USER_KEY, JSON.stringify(u));
      setLogoutNotice(null); // 로그인하면 이전 미사용 안내 제거
    } else localStorage.removeItem(USER_KEY);
  }, []);

  const clearLogoutNotice = useCallback(() => setLogoutNotice(null), []);

  // reason 별 로그인 화면 안내: 'idle'=미사용, 'session'=토큰만료/권한변경.
  // 수동 [로그아웃] 버튼은 reason 없이 호출되어 안내를 띄우지 않는다.
  const doSignOut = useCallback((reason?: 'idle' | 'session') => {
    apiLogout();
    setToken(null);
    setUserState(null);
    localStorage.removeItem(USER_KEY);
    warnOpenRef.current = false;
    setWarnOpen(false);
    setLogoutNotice(
      reason === 'idle'
        ? IDLE_LOGOUT_NOTICE
        : reason === 'session'
          ? SESSION_LOGOUT_NOTICE
          : null
    );
  }, []);

  // 공개 signOut — 수동 로그아웃용(인자 없음). onClick 핸들러로도 안전.
  const signOut = useCallback(() => doSignOut(), [doSignOut]);

  // 전역 401 → 자동 로그아웃(세션 만료·권한 변경). api 클라이언트가 호출.
  useEffect(() => {
    setUnauthorizedHandler(() => doSignOut('session'));
    return () => setUnauthorizedHandler(null);
  }, [doSignOut]);

  // 새 토큰으로 교체 — localStorage(auth_token) + auth_user + state 동기화.
  const applyNewToken = useCallback((t: string) => {
    setToken(t);
    setUserState((u) => {
      if (!u) return u;
      const nu = { ...u, token: t };
      localStorage.setItem(USER_KEY, JSON.stringify(nu));
      return nu;
    });
  }, []);

  // [연장] — 서버 토큰 갱신 + 미사용 타이머 리셋 + 경고 닫기 (+1시간).
  const extendSession = useCallback(async () => {
    try {
      const t = await refreshSession();
      applyNewToken(t);
    } catch {
      // 갱신 실패(만료 등) 시: 타이머 리셋만 하고, 끊기면 다음 API 401 로 드러남.
    } finally {
      lastActivityRef.current = Date.now();
      warnOpenRef.current = false;
      setWarnOpen(false);
    }
  }, [applyNewToken]);

  // [닫기] — 기능 없음. 다이얼로그만 닫고 기존 미사용 로직 유지.
  // 닫는 클릭 자체가 활동으로 잡혀 타이머가 리셋되는 것을 막기 위해
  // 잠깐(0.8초) 활동 무시를 유지한 뒤 재개한다.
  const dismissWarn = useCallback(() => {
    setWarnOpen(false);
    window.setTimeout(() => {
      warnOpenRef.current = false;
    }, 800);
  }, []);

  // 활동 감지 + 미사용 점검 + 작업 중 토큰 자동 갱신.
  // user.id(로그인 주체) 기준으로만 재바인딩 — 토큰 갱신으로 user 객체가 바뀌어도
  // 효과가 재실행되지 않아 미사용 타이머가 리셋되지 않는다(현재 토큰은 getToken()).
  const loggedInId = user?.id ?? null;
  useEffect(() => {
    if (!loggedInId) return;
    lastActivityRef.current = Date.now();
    warnOpenRef.current = false;
    setWarnOpen(false);

    const onActivity = () => {
      if (warnOpenRef.current) return; // 경고 표시 중에는 활동으로 연장하지 않음
      lastActivityRef.current = Date.now();
    };
    ACTIVITY_EVENTS.forEach((e) =>
      window.addEventListener(e, onActivity, { passive: true })
    );

    const tick = window.setInterval(() => {
      const now = Date.now();
      const idle = now - lastActivityRef.current;

      if (idle >= SESSION_MS) {
        doSignOut('idle');
        return;
      }
      if (idle >= WARN_AT_MS) {
        if (!warnOpenRef.current) {
          warnOpenRef.current = true; // 활동 연장 일시중지
          setWarnOpen(true);
        }
        return;
      }
      // 활동 중 — 토큰이 곧(잔여<60분) 만료되면 조용히 갱신해 API 끊김 방지.
      if (!refreshingRef.current) {
        const exp = jwtExpMs(getToken());
        if (exp != null && exp - now < REFRESH_AHEAD_MS) {
          refreshingRef.current = true;
          refreshSession()
            .then(applyNewToken)
            .catch(() => {})
            .finally(() => {
              refreshingRef.current = false;
            });
        }
      }
    }, TICK_MS);

    return () => {
      ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, onActivity));
      window.clearInterval(tick);
    };
  }, [loggedInId, doSignOut, applyNewToken]);

  const value = useMemo(
    () => ({ user, setUser, signOut, logoutNotice, clearLogoutNotice }),
    [user, setUser, signOut, logoutNotice, clearLogoutNotice]
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      {warnOpen && (
        <SessionWarnDialog onExtend={extendSession} onClose={dismissWarn} />
      )}
    </Ctx.Provider>
  );
}

// 미사용 만료 경고 — 55분 미사용 시 표시. [연장]=+1시간, [닫기]=기능 없음.
function SessionWarnDialog({
  onExtend,
  onClose,
}: {
  onExtend: () => void;
  onClose: () => void;
}) {
  return (
    <div style={dlgStyles.overlay}>
      <div style={dlgStyles.box}>
        <div style={dlgStyles.msg}>
          로그인 유지 시간이 5분 후 만료됩니다.
          <br />
          로그인을 연장하시겠습니까?
        </div>
        <div style={dlgStyles.btns}>
          <button type="button" style={dlgStyles.extend} onClick={onExtend}>
            연장
          </button>
          <button type="button" style={dlgStyles.close} onClick={onClose}>
            닫기
          </button>
        </div>
      </div>
    </div>
  );
}

const dlgStyles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.45)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 10000,
  },
  box: {
    width: 360,
    background: '#fff',
    borderRadius: 8,
    padding: '24px 22px 18px',
    boxShadow: '0 8px 30px rgba(0,0,0,0.25)',
  },
  msg: {
    fontSize: 14,
    color: '#1f2937',
    lineHeight: 1.7,
    textAlign: 'center',
    marginBottom: 18,
  },
  btns: { display: 'flex', gap: 8, justifyContent: 'center' },
  extend: {
    minWidth: 96,
    padding: '9px 0',
    background: '#1f6feb',
    color: '#fff',
    border: 'none',
    borderRadius: 4,
    fontSize: 14,
    cursor: 'pointer',
  },
  close: {
    minWidth: 96,
    padding: '9px 0',
    background: '#fff',
    color: '#374151',
    border: '1px solid #c9ced6',
    borderRadius: 4,
    fontSize: 14,
    cursor: 'pointer',
  },
};

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error('useAuth() must be inside <AuthProvider>');
  return c;
}
