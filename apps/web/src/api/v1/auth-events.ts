/**
 * Auth-invalid event — global signal that the bearer token was
 * rejected by the backend (401).
 *
 * Wiring:
 *   apiClient / postSSE  ─emit──►  window.CustomEvent
 *                                       │
 *   AuthProvider        ──listen──┘     │
 *                                       ▼
 *                                  logout() → clears token+user → re-renders to <LoginPage>
 *
 * Why a window event instead of threading callbacks through every
 * feature hook: the api client lives below React, and every endpoint
 * benefits from the same handling. A custom-event bus keeps the
 * client decoupled from the auth context while still being trivially
 * testable (just `addEventListener` + `dispatchEvent`).
 *
 * The event is fire-and-forget; emit is a no-op when `window` is
 * undefined (SSR / node tests without jsdom) so the same code path
 * works in every env.
 */

export const AUTH_INVALID_EVENT = 'careercoach:auth-invalid';

export function emitAuthInvalid(): void {
  if (typeof window !== 'undefined' && typeof CustomEvent !== 'undefined') {
    window.dispatchEvent(new CustomEvent(AUTH_INVALID_EVENT));
  }
}

/**
 * Age-required event — the backend rejected a request because the
 * user's JWT has no `age_set=true` claim (PRD §1.5 / §3.0.5 C).
 *
 * Wiring:
 *   apiClient / postSSE  ─emit──►  window.CustomEvent
 *   AuthProvider         ──listen──┘  → sets needsAge → renders <AgeGatePage>
 */
export const AGE_REQUIRED_EVENT = 'careercoach:age-required';

export function emitAgeRequired(): void {
  if (typeof window !== 'undefined' && typeof CustomEvent !== 'undefined') {
    window.dispatchEvent(new CustomEvent(AGE_REQUIRED_EVENT));
  }
}
