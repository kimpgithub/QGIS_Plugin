import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { AuthUser } from '../types';
import { logout as apiLogout } from '../api/auth';
import { getToken, setToken } from '../api/client';

const USER_KEY = 'auth_user';

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
