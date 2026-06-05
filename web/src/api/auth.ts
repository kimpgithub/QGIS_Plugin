import { api, setToken } from './client';
import type { AuthUser } from '../types';

// POST /api/login — 백엔드 미구현 시 mock 로그인으로 폴백
export async function login(id: string, password: string): Promise<AuthUser> {
  try {
    const r = await api<{ token: string; user: Omit<AuthUser, 'token'> }>(
      '/api/login',
      { method: 'POST', body: { id, password } }
    );
    const user: AuthUser = { ...r.user, token: r.token };
    setToken(r.token);
    return user;
  } catch (e) {
    // 백엔드 /api/login 이 아직 없으면 형식 검증만 통과 시 mock 발급
    if (import.meta.env.DEV && id && password) {
      const isMaster = id === 'master';
      const user: AuthUser = {
        id,
        role: isMaster ? 'master' : 'user',
        adm_cd: isMaster ? undefined : id,
        adm_nm: isMaster ? undefined : `${id} 읍/면`,
        token: `dev-${Date.now()}`,
      };
      setToken(user.token);
      console.warn('[auth] /api/login 미응답 — dev mock 로그인 발급', e);
      return user;
    }
    throw e;
  }
}

export function logout() {
  setToken(null);
}
