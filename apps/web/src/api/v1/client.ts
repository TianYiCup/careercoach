import { getAuthToken } from './auth-token';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/v1';

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
      throw new ApiError(res.status, error);
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

  constructor(status: number, body: unknown) {
    const message =
      typeof body === 'object' && body !== null && 'message' in body
        ? String((body as { message: unknown }).message)
        : `API Error ${status}`;
    super(message);
    this.status = status;
    this.body = body;
  }
}

export const apiClient = new ApiClient(BASE_URL);
