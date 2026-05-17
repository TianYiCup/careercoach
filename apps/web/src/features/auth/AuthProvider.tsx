/**
 * App-wide auth state — token + user profile + age gate flag.
 *
 * Source of truth is `localStorage` via the auth-token / auth-user
 * helpers. The provider hydrates from storage on first mount so a
 * returning user (token still valid) lands on the home page without a
 * login-flash. Mutations go through `login` / `logout` which keep
 * storage and React state in lock-step.
 *
 * `needsAge` is set to true when the api client receives a
 * 403 AGE_REQUIRED response (user hasn't declared birth year).
 * AppGate then renders <AgeGatePage> instead of Home/Sandbox.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import {
  AUTH_INVALID_EVENT,
  AGE_REQUIRED_EVENT,
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
  /** User hasn't declared birth year — show age gate instead of app. */
  needsAge: boolean;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(() => ({
    token: getAuthToken(),
    user: getAuthUser(),
    needsAge: false,
  }));

  const login = useCallback((token: string, user: UserPublic) => {
    setAuthToken(token);
    setAuthUser(user);
    setState({ token, user, needsAge: false });
  }, []);

  const logout = useCallback(() => {
    clearAuthToken();
    clearAuthUser();
    setState({ token: null, user: null, needsAge: false });
  }, []);

  // Listen for global auth events emitted by api client / postSSE.
  useEffect(() => {
    const invalidHandler = () => logout();
    const ageHandler = () => setState((s) => ({ ...s, needsAge: true }));

    window.addEventListener(AUTH_INVALID_EVENT, invalidHandler);
    window.addEventListener(AGE_REQUIRED_EVENT, ageHandler);
    return () => {
      window.removeEventListener(AUTH_INVALID_EVENT, invalidHandler);
      window.removeEventListener(AGE_REQUIRED_EVENT, ageHandler);
    };
  }, [logout]);

  const value = useMemo<AuthContextValue>(
    () => ({
      token: state.token,
      user: state.user,
      isAuthenticated: state.token !== null,
      needsAge: state.needsAge,
      login,
      logout,
    }),
    [state.token, state.user, state.needsAge, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
