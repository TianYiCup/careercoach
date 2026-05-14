/**
 * Round-trip tests for the auth-token storage helpers.
 *
 * In vitest's default node environment `localStorage` is undefined,
 * so `auth-token.ts` falls back to its in-memory map. Both code paths
 * have the same Protocol surface, and the same tests cover both —
 * vitest will exercise the memory path locally and a future jsdom
 * env wouldn't change the contract.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { clearAuthToken, getAuthToken, setAuthToken } from '../auth-token';

afterEach(() => {
  clearAuthToken();
});

describe('auth-token storage', () => {
  it('returns null when no token is stored', () => {
    expect(getAuthToken()).toBeNull();
  });

  it('round-trips a stored token', () => {
    setAuthToken('eyJhbGciOiJIUzI1NiJ9.payload.sig');
    expect(getAuthToken()).toBe('eyJhbGciOiJIUzI1NiJ9.payload.sig');
  });

  it('overwrites the prior token on second set', () => {
    setAuthToken('first');
    setAuthToken('second');
    expect(getAuthToken()).toBe('second');
  });

  it('clears the token via clearAuthToken', () => {
    setAuthToken('to-be-cleared');
    expect(getAuthToken()).toBe('to-be-cleared');

    clearAuthToken();

    expect(getAuthToken()).toBeNull();
  });

  it('clearAuthToken is a no-op when nothing is stored', () => {
    // No throw — the API is tolerant of redundant logout calls.
    clearAuthToken();
    expect(getAuthToken()).toBeNull();
  });
});
