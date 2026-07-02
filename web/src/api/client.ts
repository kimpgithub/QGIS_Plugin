// 공용 fetch 래퍼. 토큰은 localStorage('auth_token') 에서 자동 부착.
// 401 응답 시 토큰 제거하고 로그인 페이지로 돌려보냄(주체는 호출부).

const TOKEN_KEY = 'auth_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

// 전역 401 처리 — 로그인된 상태에서 토큰이 무효(만료·권한변경 등)해지면
// AuthContext 가 등록한 핸들러를 호출해 자동 로그아웃시킨다.
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

type ReqOpts = Omit<RequestInit, 'body'> & {
  body?: unknown;
  query?: Record<string, string | number | undefined | null>;
};

export async function api<T = unknown>(
  path: string,
  opts: ReqOpts = {}
): Promise<T> {
  const { body, query, headers, ...rest } = opts;
  const url = buildUrl(path, query);
  const h = new Headers(headers);
  const token = getToken();
  if (token) h.set('Authorization', `Bearer ${token}`);
  if (body !== undefined && !(body instanceof FormData)) {
    h.set('Content-Type', 'application/json');
  }
  const r = await fetch(url, {
    ...rest,
    headers: h,
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  if (!r.ok) {
    let detail: unknown = null;
    try {
      detail = await r.json();
    } catch {
      // ignore
    }
    // 로그인 요청이 아닌데 401 이고 토큰이 있으면 → 세션 무효(만료·권한변경) → 자동 로그아웃.
    if (r.status === 401 && path !== '/api/login' && getToken()) {
      onUnauthorized?.();
    }
    throw new ApiError(r.status, `HTTP ${r.status}`, detail);
  }
  if (r.status === 204) return undefined as T;
  const ct = r.headers.get('content-type') ?? '';
  return (ct.includes('application/json') ? r.json() : r.text()) as Promise<T>;
}

function buildUrl(
  path: string,
  query?: Record<string, string | number | undefined | null>
): string {
  if (!query) return path;
  const qs = Object.entries(query)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(
      ([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`
    )
    .join('&');
  return qs ? `${path}?${qs}` : path;
}
