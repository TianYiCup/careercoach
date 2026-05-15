/**
 * Contract tests for the global auth-invalid event helper.
 *
 * The helper is the single chokepoint between the api layer (client +
 * sse) and the auth layer (AuthProvider). Both sides depend on this
 * file; a regression here breaks logout-on-401 silently, so the
 * contract is worth pinning.
 *
 * vitest's default env is node, so `window` and `CustomEvent` are
 * not defined. We stub them via `vi.stubGlobal` for the tests that
 * need to observe a dispatch.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { AUTH_INVALID_EVENT, emitAuthInvalid } from '../auth-events';

interface FakeEvent {
  type: string;
}

class _StubCustomEvent implements FakeEvent {
  type: string;
  constructor(type: string) {
    this.type = type;
  }
}

interface DomStubs {
  dispatchEvent: ReturnType<typeof vi.fn>;
}

function _installDomStubs(): DomStubs {
  const dispatchEvent = vi.fn();
  vi.stubGlobal('window', { dispatchEvent });
  vi.stubGlobal('CustomEvent', _StubCustomEvent);
  return { dispatchEvent };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('AUTH_INVALID_EVENT constant', () => {
  it('uses the namespaced event name', () => {
    // Pinned because AuthProvider listens on this exact string —
    // changing the constant without updating the listener silently
    // breaks the global logout flow.
    expect(AUTH_INVALID_EVENT).toBe('careercoach:auth-invalid');
  });
});

describe('emitAuthInvalid', () => {
  it('dispatches a CustomEvent on window with AUTH_INVALID_EVENT type', () => {
    const { dispatchEvent } = _installDomStubs();
    emitAuthInvalid();
    expect(dispatchEvent).toHaveBeenCalledTimes(1);
    const event = dispatchEvent.mock.calls[0]?.[0] as FakeEvent;
    expect(event.type).toBe(AUTH_INVALID_EVENT);
  });

  it('is idempotent — repeated calls each fire one event', () => {
    const { dispatchEvent } = _installDomStubs();
    emitAuthInvalid();
    emitAuthInvalid();
    emitAuthInvalid();
    expect(dispatchEvent).toHaveBeenCalledTimes(3);
  });

  it('is a no-op when window is undefined (SSR / node-only env)', () => {
    // Don't install stubs — leave window undefined as in raw node env.
    expect(() => emitAuthInvalid()).not.toThrow();
  });
});
