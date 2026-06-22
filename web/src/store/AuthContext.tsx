import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { AuthUser } from '../types';
import { logout as apiLogout } from '../api/auth';
import { getToken, setToken } from '../api/client';

const USER_KEY = 'auth_user';

// JWT payload 의 exp(만료, 초 단위)를 ms 로 변환 — 자동 로그아웃 타이머용.
// 디코드 실패 시 null → 타이머를 걸지 않음(기존 동작 유지).
function jwtExpMs(token: string): number | null {
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
};

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

  const setUser = useCallback((u: AuthUser | null) => {
    setUserState(u);
    if (u) localStorage.setItem(USER_KEY, JSON.stringify(u));
    else localStorage.removeItem(USER_KEY);
  }, []);

  const signOut = useCallback(() => {
    apiLogout();
    setToken(null);
    setUserState(null);
    localStorage.removeItem(USER_KEY);
  }, []);

  // 자동 로그아웃 — 토큰 만료(JWT exp, 서버 JWT_EXPIRES_MIN=60분) 시각에
  // signOut 하여 로그인 화면으로 복귀. 새로고침 후에도 남은 시간만큼 재설정됨.
  useEffect(() => {
    if (!user?.token) return;
    const expMs = jwtExpMs(user.token);
    if (expMs == null) return;
    const remain = expMs - Date.now();
    if (remain <= 0) {
      signOut();
      return;
    }
    const id = window.setTimeout(() => signOut(), remain);
    return () => window.clearTimeout(id);
  }, [user, signOut]);

  const value = useMemo(
    () => ({ user, setUser, signOut }),
    [user, setUser, signOut]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const c = useContext(Ctx);
  if (!c) throw new Error('useAuth() must be inside <AuthProvider>');
  return c;
}
