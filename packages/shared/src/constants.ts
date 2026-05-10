export const RED_LINE_CATEGORIES = [
  'self-harm',
  'school-violence',
  'predatory-lending',
  'sexual-harassment',
  'political',
  'communication-harm',
] as const

export type RedLineCategory = (typeof RED_LINE_CATEGORIES)[number]

export const APP_MODES = ['sandbox', 'copilot', 'review'] as const
export type AppMode = (typeof APP_MODES)[number]

export type ModerationResult =
  | { passed: true }
  | { passed: false; reason: RedLineCategory; details: string }
