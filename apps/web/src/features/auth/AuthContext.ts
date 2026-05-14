/**
 * Auth Context plumbing — lives in a TS-only file so the Provider
 * component and the consumer hook can each be exported from their own
 * single-purpose file (keeps Vite's fast-refresh boundary happy).
 *
 * State shape: token + UserPublic profile. `isAuthenticated` is derived
 * (a token exists) so callers don't have to recompute the predicate.
 */

import { createContext } from 'react';

import type { UserPublic } from '../../api/v1';

export interface AuthContextValue {
  token: string | null;
  user: UserPublic | null;
  isAuthenticated: boolean;
  /** Persist token + user and re-render gated routes. */
  login: (token: string, user: UserPublic) => void;
  /** Wipe token + user — call from logout button or after 401. */
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
