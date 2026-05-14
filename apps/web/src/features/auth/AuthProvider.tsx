/**
 * App-wide auth state — token + user profile.
 *
 * Source of truth is `localStorage` via the auth-token / auth-user
 * helpers. The provider hydrates from storage on first mount so a
 * returning user (token still valid) lands on the home page without a
 * login-flash. Mutations go through `login` / `logout` which keep
 * storage and React state in lock-step.
 *
 * Why a Context (not Zustand) here: the surface is two fields and two
 * actions, and every reader of auth state is a UI consumer that wants
 * to re-render on change. A Context fits exactly; Zustand would just
 * be ceremony.
 *
 * The api client reads its bearer token straight from `getAuthToken()`
 * on every request (see `client.ts`), so the provider does NOT need to
 * push the token into the client — they share the same storage layer.
 */

import { useCallback, useMemo, useState, type ReactNode } from 'react';

import {
  clearAuthToken,
  clearAuthUser,
  getAuthToken,
  getAuthUser,
  setAuthToken,
  setAuthUser,
} from '../../api/v1';
import type { UserPublic } from '../../api/v1';
import { AuthContext, type AuthContextValue } from './AuthContext';

interface AuthState {
  token: string | null;
  user: UserPublic | null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  // Lazy initializer so we read storage exactly once on mount and not
  // on every re-render of the parent.
  const [state, setState] = useState<AuthState>(() => ({
    token: getAuthToken(),
    user: getAuthUser(),
  }));

  const login = useCallback((token: string, user: UserPublic) => {
    setAuthToken(token);
    setAuthUser(user);
    setState({ token, user });
  }, []);

  const logout = useCallback(() => {
    clearAuthToken();
    clearAuthUser();
    setState({ token: null, user: null });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      token: state.token,
      user: state.user,
      isAuthenticated: state.token !== null,
      login,
      logout,
    }),
    [state.token, state.user, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
