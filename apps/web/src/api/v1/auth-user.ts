/**
 * Persisted user-profile snapshot.
 *
 * Sibling of `auth-token.ts`: the JWT lives there, the `UserPublic`
 * payload it was minted alongside lives here. Splitting them lets
 * non-UI code (the api client) keep depending only on the token and
 * stay unaware of any user-profile shape changes.
 *
 * Storage strategy mirrors auth-token — `localStorage` in the browser
 * and an in-memory `Map` everywhere else (vitest node env, SSR, EXE
 * pre-render). JSON encoded under a namespaced key so we don't collide
 * with anything else on the origin.
 *
 * On any parse error we return `null` (treat as logged out) rather than
 * throwing — a corrupt localStorage entry should NOT brick the app.
 */

import type { UserPublic } from './types';

const USER_KEY = 'careercoach.auth_user';

const memoryStore = new Map<string, string>();

interface UserStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function resolveStorage(): UserStorage {
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

/** Read the stored user profile, or null if logged out / never set. */
export function getAuthUser(): UserPublic | null {
  const raw = resolveStorage().getItem(USER_KEY);
  if (raw === null) return null;
  try {
    return JSON.parse(raw) as UserPublic;
  } catch {
    // Corrupt entry — clear it so we don't re-trip every read.
    resolveStorage().removeItem(USER_KEY);
    return null;
  }
}

/** Persist the user profile — call after `/auth/sms/verify` succeeds. */
export function setAuthUser(user: UserPublic): void {
  resolveStorage().setItem(USER_KEY, JSON.stringify(user));
}

/** Drop the stored profile — call on logout or after a 401 response. */
export function clearAuthUser(): void {
  resolveStorage().removeItem(USER_KEY);
}
