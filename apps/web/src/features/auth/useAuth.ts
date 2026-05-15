/**
 * Consumer hook for the auth context. Throws if used outside the
 * provider — guards against silent misuse (a component reading auth
 * state above the gate boundary would otherwise see stale-looking
 * `null` defaults and route the user back to login).
 */

import { useContext } from 'react';

import { AuthContext, type AuthContextValue } from './AuthContext';

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error('useAuth must be used inside <AuthProvider>');
  }
  return ctx;
}
