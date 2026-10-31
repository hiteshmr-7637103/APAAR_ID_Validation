// Thin fetch wrapper: adds the bearer token, refreshes once on 401, normalises API errors.

export const API_BASE = "/api/v1";

const ACCESS_KEY = "apaar.access";
const REFRESH_KEY = "apaar.refresh";

export const tokenStore = {
  get access() {
    return localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

export interface ApiErrorBody {
  code: string;
  message_en: string;
  message_key: string;
  details: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: ApiErrorBody,
  ) {
    super(body.message_en);
  }
}

async function toApiError(res: Response): Promise<ApiError> {
  try {
    const json = await res.json();
    if (json?.error?.code) return new ApiError(res.status, json.error);
  } catch {
    /* non-JSON error */
  }
  return new ApiError(res.status, {
    code: "HTTP_" + res.status,
    message_en: res.statusText || "Request failed.",
    message_key: "errors.http",
    details: {},
  });
}

async function tryRefresh(): Promise<boolean> {
  const refresh = tokenStore.refresh;
  if (!refresh) return false;
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!res.ok) {
    tokenStore.clear();
    return false;
  }
  const body = await res.json();
  tokenStore.set(body.access_token, body.refresh_token);
  return true;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  const access = tokenStore.access;
  if (access) headers.set("Authorization", `Bearer ${access}`);
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 401 && retry && (await tryRefresh())) {
    return apiFetch<T>(path, init, false);
  }
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

/** Like apiFetch but returns the raw Response (file downloads). */
export async function apiFetchRaw(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<Response> {
  const headers = new Headers(init.headers);
  const access = tokenStore.access;
  if (access) headers.set("Authorization", `Bearer ${access}`);
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 401 && retry && (await tryRefresh())) {
    return apiFetchRaw(path, init, false);
  }
  if (!res.ok) throw await toApiError(res);
  return res;
}
