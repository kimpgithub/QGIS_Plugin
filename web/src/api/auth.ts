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

// POST /api/auth/refresh — 유효한 토큰으로 만료시각이 갱신된 새 토큰을 받는다.
// (미사용 경고의 [연장] 및 작업 중 자동 갱신용. 토큰 적용은 호출부가 담당.)
export async function refreshSession(): Promise<string> {
  const r = await api<{ token: string }>('/api/auth/refresh', {
    method: 'POST',
  });
  return r.token;
}

export function logout() {
  setToken(null);
}
