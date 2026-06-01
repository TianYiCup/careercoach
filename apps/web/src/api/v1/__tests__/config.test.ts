/**
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { API_BASE_OVERRIDE_KEY, resolveApiBaseUrl } from '../config';

describe('resolveApiBaseUrl', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    localStorage.clear();
  });

  it('falls back to relative /v1 when nothing is configured', () => {
    expect(resolveApiBaseUrl()).toBe('/v1');
  });

  it('uses the build-time VITE_API_BASE_URL when set', () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://env-api.example.com/v1');
    expect(resolveApiBaseUrl()).toBe('https://env-api.example.com/v1');
  });

  it('prefers the localStorage runtime override over the env default', () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://env-api.example.com/v1');
    localStorage.setItem(API_BASE_OVERRIDE_KEY, 'https://runtime.example.com/v1');
    expect(resolveApiBaseUrl()).toBe('https://runtime.example.com/v1');
  });

  it('strips trailing slashes so joined paths never double up', () => {
    localStorage.setItem(API_BASE_OVERRIDE_KEY, 'https://demo.example.com/v1//');
    expect(resolveApiBaseUrl()).toBe('https://demo.example.com/v1');
  });

  it('ignores a blank override and falls through', () => {
    localStorage.setItem(API_BASE_OVERRIDE_KEY, '   ');
    expect(resolveApiBaseUrl()).toBe('/v1');
  });
});
