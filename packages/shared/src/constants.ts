/** Application modes — aligns with `app.schemas.sessions.SessionMode`
 *  (sandbox / copilot / review). Single source of truth so that future
 *  cross-cutting code (mode-switcher UI, mode-gated routes) doesn't
 *  drift from the API's literal union.
 */
export const APP_MODES = ['sandbox', 'copilot', 'review'] as const
export type AppMode = (typeof APP_MODES)[number]
