/**
 * Bearer-token storage for the API client.
 *
 * Wraps `localStorage` with an in-memory fallback so the same code
 * works in:
 *   - the browser (token survives reload)
 *   - vitest's default node env (no `localStorage` → memory map; each
 *     suite calls `clearAuthToken()` to isolate state)
 *   - SSR / pre-rendering (typeof check avoids "localStorage is not
 *     defined" crashes during a Tauri Static build)
 *
 * The key is namespaced under `careercoach.` so we don't collide with
 * any unrelated `auth_token` entries from third-party libs.
 *
 * Token format: a `u_<uuid>`-bound JWT minted by `/v1/auth/sms/verify`.
 * The client doesn't validate the payload here; the backend rejects
 * stale/tampered tokens via `decode_token` and the soft-auth
 * dependency falls back to anonymous on rejection.
 */

const TOKEN_KEY = 'careercoach.auth_token';

const memoryStore = new Map<string, string>();

interface TokenStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function resolveStorage(): TokenStorage {
  if (typeof localStorage !== 'undefined') {
    return localStorage;
  }
  return {
    getItem: (key) => memoryStore.get(key) ?? null,
    setItem: (key, value) => {
      memoryStore.set(key, value);
    },
    removeItem: (key) => {
      memoryStore.delete(key);
    },
  };
}

/** Read the current token, or null if the user is anonymous. */
export function getAuthToken(): string | null {
  return resolveStorage().getItem(TOKEN_KEY);
}

/** Persist a freshly minted JWT — call after `/auth/sms/verify` succeeds. */
export function setAuthToken(token: string): void {
  resolveStorage().setItem(TOKEN_KEY, token);
}

/** Drop the stored token — call on logout or after a 401 response. */
export function clearAuthToken(): void {
  resolveStorage().removeItem(TOKEN_KEY);
}
