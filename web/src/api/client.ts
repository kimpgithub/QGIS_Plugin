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
