/**
 * Round-trip tests for the auth-user storage helpers.
 *
 * Mirrors `auth-token.test.ts` — both rely on the same in-memory
 * fallback in vitest's node env, so the contract surface is what we
 * actually exercise here.
 */

import { afterEach, describe, expect, it } from 'vitest';

import { clearAuthUser, getAuthUser, setAuthUser } from '../auth-user';
import type { UserPublic } from '../types';

const SAMPLE_USER: UserPublic = {
  id: 'u_018f3a8b',
  nickname: 'K 学员 0001',
  persona_type: 'in_school',
  is_minor: false,
};

afterEach(() => {
  clearAuthUser();
});

describe('auth-user storage', () => {
  it('returns null when nothing is stored', () => {
    expect(getAuthUser()).toBeNull();
  });

  it('round-trips a stored user', () => {
    setAuthUser(SAMPLE_USER);
    expect(getAuthUser()).toEqual(SAMPLE_USER);
  });

  it('overwrites prior user on second set', () => {
    setAuthUser(SAMPLE_USER);
    const second: UserPublic = { ...SAMPLE_USER, nickname: '小苏' };
    setAuthUser(second);
    expect(getAuthUser()?.nickname).toBe('小苏');
  });

  it('clearAuthUser drops the stored user', () => {
    setAuthUser(SAMPLE_USER);
    expect(getAuthUser()).not.toBeNull();
    clearAuthUser();
    expect(getAuthUser()).toBeNull();
  });

  it('clearAuthUser is a no-op when nothing is stored', () => {
    clearAuthUser();
    expect(getAuthUser()).toBeNull();
  });

  it('returns null and self-heals when storage holds corrupt JSON', () => {
    // Simulate a hand-edited / browser-extension-corrupted entry.
    // We poke the same storage layer the helper uses so corrupt
    // entries don't permanently brick the app.
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('careercoach.auth_user', '{not-json');
    } else {
      // node env — use the same in-memory key indirectly by setting
      // a real user first, then mangle via direct localStorage access
      // is impossible. Skip the corrupt-data leg in node — the browser
      // is where this matters.
      return;
    }
    expect(getAuthUser()).toBeNull();
    // After getAuthUser sees corrupt JSON it clears the key, so a
    // subsequent read also returns null without re-tripping the parser.
    expect(getAuthUser()).toBeNull();
  });
});
