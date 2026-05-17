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
  | 'INVALID_CODE'
  | 'NOT_FOUND'
  | 'ALREADY_ENDED'

export interface ErrorResponse {
  code: ApiErrorCode | string
  message: string
  trace_id: string
}
