/**
 * Shared API types for wxapp — aligned with web api/v1/types.ts
 *
 * Only includes types needed by the wxapp auth + session flow.
 * Full session/SSE types remain in ./sandbox.ts.
 */

export type PersonaType = 'in_school' | 'intern' | 'graduate'

export interface UserPublic {
  id: string
  nickname: string
  persona_type: PersonaType
  is_minor: boolean
}

export interface SmsSendRequest {
  phone: string
}

export interface SmsSendResponse {
  ttl: number
}

export interface SmsVerifyRequest {
  phone: string
  code: string
}

export interface SmsVerifyResponse {
  token: string
  user: UserPublic
}

export interface UpdateBirthYearRequest {
  birth_year: number
}

export type ApiErrorCode =
  | 'AGE_REQUIRED'
  | 'MINOR_QUIET_HOURS'
  | 'MINOR_FORBIDDEN'
  | 'SMS_SEND_COOLDOWN'
  | 'SMS_VERIFY_LOCKED'
  | 'CAPTION_BLOCKED'
  | 'SCENARIO_BLOCKED'
  | 'RATE_LIMIT_EXCEEDED'
  | 'INVALID_CODE'
  | 'NOT_FOUND'
  | 'ALREADY_ENDED'

export interface ErrorResponse {
  code: ApiErrorCode | string
  message: string
  trace_id: string
}

// --- Vibe (PRD §7.11) ---

export type VibeType = 'fire' | 'tired' | 'anxious' | 'excited' | 'meh'

export interface SetVibeRequest {
  vibe: VibeType
}

export interface VibeResponse {
  vibe: VibeType
  logged_date: string
}

// --- Streak (PRD §7.11) ---

export interface StreakResponse {
  current_days: number
  max_days: number
}

// --- Weakness Profile API (PR #131) ---

export interface WeaknessProfileResponse {
  weaknesses: Array<{
    tag: string
    frequency: number
    last_seen: string
  }>
  recommended_scenarios: Array<{
    id: string
    title: string
    category: string
    difficulty: number
    tags: string[]
    background: string
    real_user_certified: boolean
  }>
}

// --- Custom Scenario (PRD §7.3) ---

export interface CustomScenarioRequest {
  description: string
}

export interface CustomScenarioResponse {
  scenario_id: string
  title: string
  background: string
  persona_title: string
  opening_line: string
}
