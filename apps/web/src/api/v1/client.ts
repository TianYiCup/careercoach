import { getAuthToken } from './auth-token';
import { emitAuthInvalid, emitAgeRequired } from './auth-events';
import { resolveApiBaseUrl } from './config';

const BASE_URL = resolveApiBaseUrl();

class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const res = await fetch(url, {
      ...options,
      headers: this._buildHeaders(options.headers),
    });

    if (!res.ok) {
      const error = await res.json().catch(() => ({ message: res.statusText }));
      // 401 means the backend rejected our bearer token. Emit a
      // global signal so AuthProvider can wipe the stale token and
      // route back to the login page. We still throw the ApiError so
      // local error UI (banners, toasts) can render on the way out.
      if (res.status === 401) {
        emitAuthInvalid();
      }
      // 403 AGE_REQUIRED — user hasn't declared birth year yet.
      // Emit so AuthProvider shows the age gate page.
      if (res.status === 403 && (error as { code?: string }).code === 'AGE_REQUIRED') {
        emitAgeRequired();
      }
      throw new ApiError(res.status, error, res.headers);
    }

    return res.json() as Promise<T>;
  }

  /**
   * Compose the request headers, layering in:
   *   - Content-Type (always JSON for this v0.1 surface)
   *   - Authorization: Bearer <token>, when a token is stored
   *   - any caller-supplied headers, which override the defaults
   *
   * Authorization is read fresh on every request so a logout (clear
   * token) is picked up immediately without the client needing to be
   * re-instantiated.
   */
  private _buildHeaders(callerHeaders: HeadersInit | undefined): HeadersInit {
    const token = getAuthToken();
    const base: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (token) {
      base.Authorization = `Bearer ${token}`;
    }
    return { ...base, ...(callerHeaders ?? {}) } as HeadersInit;
  }

  async get<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: 'GET' });
  }

  async post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    });
  }
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  headers: Headers;

  constructor(status: number, body: unknown, headers: Headers = new Headers()) {
    const message =
      typeof body === 'object' && body !== null && 'message' in body
        ? String((body as { message: unknown }).message)
        : `API Error ${status}`;
    super(message);
    this.status = status;
    this.body = body;
    this.headers = headers;
  }
}

export const apiClient = new ApiClient(BASE_URL);
