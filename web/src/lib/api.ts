/** The browser talks to the API through the same origin at /api (nginx or the Vite proxy). */
export const API_BASE = "/api";
const TOKEN_KEY = "ekalavya-token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session lasts until reload */
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

type Params = Record<string, string | number | boolean | undefined | null>;
type Options = {
  method?: string;
  body?: unknown;
  form?: FormData;
  params?: Params;
  signal?: AbortSignal;
};

function query(params?: Params): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

export async function api<T = unknown>(path: string, options: Options = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (options.form) body = options.form;
  else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  const response = await fetch(`${API_BASE}${path}${query(options.params)}`, {
    method: options.method ?? (body ? "POST" : "GET"),
    headers,
    body,
    signal: options.signal,
  });
  if (response.status === 401 && token && !path.startsWith("/v1/auth/login")) onUnauthorized?.();
  if (!response.ok) {
    let message = response.statusText || "Request failed";
    try {
      const data = await response.json();
      const detail = data?.detail;
      message =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((item) => item?.msg ?? String(item)).join("; ")
            : message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** A URL a <video>, <img> or link can load; it carries the session as a query parameter. */
export function mediaUrl(path: string, params: Params = {}) {
  const token = getToken();
  return `${API_BASE}${path}${query({ ...params, access_token: token ?? undefined })}`;
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong";
}
