import { getApiBaseUrl } from "./config";

export class ApiError extends Error {
  status: number;
  code: string;
  detail: unknown;

  constructor(status: number, code: string, message: string, detail: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

interface ErrorBody {
  code?: string;
  message?: string;
  detail?: unknown;
}

type TokenGetter = () => string | null;
type UnauthorizedHandler = () => void;

let getToken: TokenGetter = () => null;
let onUnauthorized: UnauthorizedHandler = () => undefined;

export function configureApiClient(tokenGetter: TokenGetter, unauthorizedHandler: UnauthorizedHandler): void {
  getToken = tokenGetter;
  onUnauthorized = unauthorizedHandler;
}

export function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

/** The only 401 codes that mean the access_token itself is the problem, i.e.
 * that the session is genuinely dead and the user must be logged out.
 * Everything else that comes back as a 401 is a step-up failure on an
 * otherwise-valid access_token (a wrong current password or a wrong/stale TOTP
 * code on a password / email-change / TOTP-reenroll mutation) — the user has to
 * see that error and retry, not be silently bounced to /login before the
 * banner can render. unknown_error covers a bodyless 401 (e.g. from a proxy),
 * where we cannot tell and must assume the token is bad. */
const DEAD_SESSION_CODES = new Set(["missing_token", "invalid_token", "unknown_error"]);

async function _authorizedFetch(path: string, options: RequestInit): Promise<Response> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${getApiBaseUrl()}${path}`, { ...options, headers });

  if (!response.ok) {
    // One body-parse, used both to decide whether the session is dead and to
    // build the ApiError — the body can only be consumed once.
    const body: ErrorBody = await response.json().catch(() => ({}));
    const code = body.code ?? "unknown_error";
    if (response.status === 401 && DEAD_SESSION_CODES.has(code)) {
      onUnauthorized();
    }
    throw new ApiError(response.status, code, body.message ?? response.statusText, body.detail ?? null);
  }

  return response;
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  // FormData (the avatar upload) sets its own multipart boundary; overriding
  // it with application/json breaks the request.
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await _authorizedFetch(path, { ...options, headers });

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** Like apiFetch, but for a binary response body (the avatar image) rather than
 * JSON. Shares the same auth-header and 401-handling behavior via
 * _authorizedFetch. */
export async function apiFetchBlob(path: string, options: RequestInit = {}): Promise<Blob> {
  const response = await _authorizedFetch(path, options);
  return await response.blob();
}

/** The current access token, for callers that cannot go through apiFetch (the live WebSocket). */
export function getAccessToken(): string | null {
  return getToken();
}
