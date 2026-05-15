/**
 * Verifies that both the JSON `apiClient` and the streaming `postSSE`
 * helper emit the global `auth-invalid` event on a 401 response (and
 * NOT on 200 / 400 / 500).
 *
 * This is the cross-cutting test that pins logout-on-401 behavior:
 * AuthProvider listens for the event and force-logs the user out, so
 * any regression in the emit side would silently strand a stale token
 * in storage.
 *
 * vitest's default env is node — `window` / `CustomEvent` are stubbed
 * per-test via `vi.stubGlobal` so the production guard
 * (`typeof window !== 'undefined'`) reaches the dispatch path.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AUTH_INVALID_EVENT } from '../auth-events';
import { ApiError, apiClient } from '../client';
import { clearAuthToken } from '../auth-token';
import { postSSE } from '../sse';

interface FakeEvent {
  type: string;
}

class _StubCustomEvent implements FakeEvent {
  type: string;
  constructor(type: string) {
    this.type = type;
  }
}

interface FetchAndDispatch {
  fetch: ReturnType<typeof vi.fn>;
  dispatchEvent: ReturnType<typeof vi.fn>;
}

function _stub(status: number, body: unknown): FetchAndDispatch {
  const dispatchEvent = vi.fn();
  vi.stubGlobal('window', { dispatchEvent });
  vi.stubGlobal('CustomEvent', _StubCustomEvent);
  const fetch = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', fetch);
  return { fetch, dispatchEvent };
}

function _firedEventTypes(spy: ReturnType<typeof vi.fn>): string[] {
  return spy.mock.calls.map((args) => (args[0] as FakeEvent).type);
}

beforeEach(() => {
  clearAuthToken();
});

afterEach(() => {
  clearAuthToken();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('apiClient 401 handling', () => {
  it('emits AUTH_INVALID_EVENT and throws ApiError(401) on a 401 response', async () => {
    const { dispatchEvent } = _stub(401, {
      code: 'UNAUTHORIZED',
      message: 'missing bearer token',
    });

    await expect(apiClient.get('/scenarios')).rejects.toBeInstanceOf(ApiError);

    expect(_firedEventTypes(dispatchEvent)).toContain(AUTH_INVALID_EVENT);
  });

  it('does NOT emit on a 200 response', async () => {
    const { dispatchEvent } = _stub(200, { ok: true });

    await apiClient.get('/scenarios');

    expect(_firedEventTypes(dispatchEvent)).not.toContain(AUTH_INVALID_EVENT);
  });

  it('does NOT emit on a 500 response — only 401 should trigger logout', async () => {
    const { dispatchEvent } = _stub(500, { code: 'ERROR', message: 'boom' });

    await expect(apiClient.get('/scenarios')).rejects.toBeInstanceOf(ApiError);

    expect(_firedEventTypes(dispatchEvent)).not.toContain(AUTH_INVALID_EVENT);
  });

  it('does NOT emit on a 400 response — 401 is the auth signal, 400 is request shape', async () => {
    const { dispatchEvent } = _stub(400, { code: 'BAD_REQUEST', message: 'x' });

    await expect(apiClient.post('/sessions', { x: 1 })).rejects.toBeInstanceOf(
      ApiError,
    );

    expect(_firedEventTypes(dispatchEvent)).not.toContain(AUTH_INVALID_EVENT);
  });
});

describe('postSSE 401 handling', () => {
  it('emits AUTH_INVALID_EVENT and throws on a 401 response', async () => {
    const { dispatchEvent } = _stub(401, {
      code: 'UNAUTHORIZED',
      message: 'missing bearer token',
    });

    await expect(
      postSSE('/sessions/ses_x/turns', { content: 'hi' }, () => {}),
    ).rejects.toThrow();

    expect(_firedEventTypes(dispatchEvent)).toContain(AUTH_INVALID_EVENT);
  });

  it('does NOT emit on a 500 response — only 401 should trigger logout', async () => {
    const { dispatchEvent } = _stub(500, { code: 'ERROR', message: 'boom' });

    await expect(
      postSSE('/sessions/ses_x/turns', { content: 'hi' }, () => {}),
    ).rejects.toThrow();

    expect(_firedEventTypes(dispatchEvent)).not.toContain(AUTH_INVALID_EVENT);
  });
});
