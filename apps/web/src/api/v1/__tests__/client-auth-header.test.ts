/**
 * Verifies that `apiClient` and `postSSE` inject the `Authorization:
 * Bearer <token>` header on every request when a token is stored.
 *
 * We stub `globalThis.fetch` directly (not via MSW) so we can assert
 * on the exact `headers` argument the client computed — the contract
 * tests in `mocks/__tests__/contract.test.ts` already cover the
 * MSW-shaped behaviour.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '../client';
import { clearAuthToken, setAuthToken } from '../auth-token';
import { postSSE } from '../sse';

function _emptyJsonResponse(): Response {
  return new Response('{}', {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function _emptySseResponse(): Response {
  // No body chunks → postSSE loop exits immediately.
  return new Response(new ReadableStream({ start: (c) => c.close() }), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

function _capturedHeaders(spy: ReturnType<typeof vi.fn>): Headers {
  const init = spy.mock.calls[0]?.[1] as RequestInit | undefined;
  return new Headers(init?.headers);
}

beforeEach(() => {
  clearAuthToken();
});

afterEach(() => {
  clearAuthToken();
  vi.restoreAllMocks();
});

describe('apiClient Authorization header', () => {
  it('omits Authorization when no token is stored', async () => {
    const fetchSpy = vi.fn(async () => _emptyJsonResponse());
    vi.stubGlobal('fetch', fetchSpy);

    await apiClient.get('/scenarios');

    const headers = _capturedHeaders(fetchSpy);
    expect(headers.has('authorization')).toBe(false);
  });

  it('sends Bearer token from storage on GET', async () => {
    setAuthToken('eyJ-test-token-1');
    const fetchSpy = vi.fn(async () => _emptyJsonResponse());
    vi.stubGlobal('fetch', fetchSpy);

    await apiClient.get('/scenarios');

    expect(_capturedHeaders(fetchSpy).get('authorization')).toBe('Bearer eyJ-test-token-1');
  });

  it('sends Bearer token from storage on POST', async () => {
    setAuthToken('eyJ-test-token-2');
    const fetchSpy = vi.fn(async () => _emptyJsonResponse());
    vi.stubGlobal('fetch', fetchSpy);

    await apiClient.post('/sessions', { mode: 'sandbox' });

    expect(_capturedHeaders(fetchSpy).get('authorization')).toBe('Bearer eyJ-test-token-2');
  });

  it('still sets Content-Type on JSON requests', async () => {
    setAuthToken('any-token');
    const fetchSpy = vi.fn(async () => _emptyJsonResponse());
    vi.stubGlobal('fetch', fetchSpy);

    await apiClient.post('/sessions', { mode: 'sandbox' });

    expect(_capturedHeaders(fetchSpy).get('content-type')).toBe('application/json');
  });

  it('reads the token fresh on each request — logout takes effect immediately', async () => {
    setAuthToken('first-token');
    const fetchSpy = vi.fn(async () => _emptyJsonResponse());
    vi.stubGlobal('fetch', fetchSpy);

    await apiClient.get('/scenarios');
    clearAuthToken();
    await apiClient.get('/scenarios');

    const firstHeaders = new Headers(
      (fetchSpy.mock.calls[0]?.[1] as RequestInit | undefined)?.headers,
    );
    const secondHeaders = new Headers(
      (fetchSpy.mock.calls[1]?.[1] as RequestInit | undefined)?.headers,
    );

    expect(firstHeaders.get('authorization')).toBe('Bearer first-token');
    // After logout the second call must NOT carry the prior token —
    // a state leak here would let a logged-out user keep acting as
    // their old identity until the page refreshed.
    expect(secondHeaders.has('authorization')).toBe(false);
  });
});

describe('postSSE Authorization header', () => {
  it('omits Authorization when no token is stored', async () => {
    const fetchSpy = vi.fn(async () => _emptySseResponse());
    vi.stubGlobal('fetch', fetchSpy);

    await postSSE('/sessions/ses_x/turns', { content: 'hi' }, () => {});

    expect(_capturedHeaders(fetchSpy).has('authorization')).toBe(false);
  });

  it('sends Bearer token when one is stored', async () => {
    setAuthToken('sse-test-token');
    const fetchSpy = vi.fn(async () => _emptySseResponse());
    vi.stubGlobal('fetch', fetchSpy);

    await postSSE('/sessions/ses_x/turns', { content: 'hi' }, () => {});

    expect(_capturedHeaders(fetchSpy).get('authorization')).toBe('Bearer sse-test-token');
  });
});
