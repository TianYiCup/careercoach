/**
 * API types derived from OpenAPI v0.1 spec (apps/api/openapi.yaml)
 */

// --- Scenarios ---
export type ScenarioCategory = 'campus' | 'jobhunt' | 'intern' | 'life'

export interface ScenarioSummary {
  id: string
  title: string
  category: ScenarioCategory
  difficulty: number
  tags: string[]
  background: string
  real_user_certified: boolean
}

export interface ScenarioListResponse {
  items: ScenarioSummary[]
  total: number
}

// --- Sessions ---
export type SessionMode = 'sandbox' | 'copilot' | 'review'
export type ScoreResult = 'shenfeng' | 'guolu' | 'fanche'

export interface CreateSessionRequest {
  mode: SessionMode
  scenario_id: string
  persona_id: string
  user_goal: string
}

export interface CreateSessionResponse {
  session_id: string
  opening_line: string
}

export interface TurnRequest {
  content: string
}

// --- SSE Frames ---
export type SseEventFrame =
  | { event: 'opponent.delta'; data: { text: string } }
  | { event: 'opponent.done'; data: { turn_id: string; full_text: string } }
  | { event: 'coach.hint'; data: { safe: string; aggressive: string; humor: string } }
  | { event: 'meta'; data: { turns_used: number; turns_left: number } }

// --- End Session ---
export interface Score {
  aura: number
  logic: number
  emotion: number
  professionalism: number
  goal_achieve: number
  highlights: string
  failures: string
  result: ScoreResult
}

export interface WeaknessUpdate {
  tag: string
  delta: number
}

export interface EndSessionResponse {
  score: Score
  weakness_updates: WeaknessUpdate[]
}

// --- Moderation ---
export type ModerationVerdict = 'allow' | 'warn' | 'redirect' | 'block'
export type ModerationCategory = 'self_harm' | 'violence' | 'loan' | 'harassment' | 'political' | 'other'
export type ModerationContext = 'user_input' | 'ai_output' | 'scenario_custom'

export interface ModerationCheckRequest {
  content: string
  context: ModerationContext
  session_id?: string | null
}

export interface ModerationCheckResponse {
  verdict: ModerationVerdict
  categories: ModerationCategory[]
  score: number
  redirect_resource?: { title: string; url: string } | null
  trace_id: string
}

// --- Share Cards (PRD §7.9 / design-spec §10) ---
export type ShareCardType = 'session' | 'weekly' | 'wrapped'

export interface ShareLinks {
  /** WeChat share deep link (weixin://...). */
  wechat: string
  /** Xiaohongshu share intent URL. */
  xiaohongshu: string
  /** Direct PNG URL for save-to-album. Same origin as `png_url`. */
  save_local: string
}

export interface SessionShareCardRequest {
  /** Render the bottom-right QR. Defaults false (screenshot-clean). */
  include_qrcode?: boolean
  /** Optional user-edited one-liner; max 80 chars; re-moderated server-side. */
  user_caption_override?: string | null
}

export interface WeeklyShareCardRequest {
  /** Weekly defaults QR off; flip true to share. */
  include_qrcode?: boolean
  /** 0 = previous ISO week (Asia/Shanghai). -12..0. */
  week_offset?: number
}

export interface WrappedShareCardRequest {
  /** Wrapped defaults QR on — virality matters. */
  include_qrcode?: boolean
}

export interface ShareCardResponse {
  /** Opaque id, also storage key. Prefix `card_`. */
  card_id: string
  type: ShareCardType
  /** 1080×1920 PNG URL. For `wrapped` this equals `pages[0]`. */
  png_url: string
  /** Empty for session/weekly; 6 URLs for wrapped. `pages[0]` IS the cover. */
  pages: string[]
  share_links: ShareLinks
  /** UTC timestamp at which the PNG was rendered. */
  generated_at: string
}

// --- Error ---
export interface ErrorResponse {
  code: string
  message: string
  trace_id: string
}

// --- Auth (PRD §7.2) ---
export type PersonaType = 'in_school' | 'intern' | 'graduate'

export interface SmsSendRequest {
  /** Mainland China mobile number, 11 digits, no country prefix. */
  phone: string
}

export interface SmsSendResponse {
  /** Seconds until the user may request another code. v0.1 hard-codes 60s. */
  ttl: number
}

export interface SmsVerifyRequest {
  phone: string
  /** 6-digit verification code received by SMS. */
  code: string
}

export interface UserPublic {
  id: string
  nickname: string
  persona_type: PersonaType
  /** True if user.birthdate < 18 — triggers minor mode (PRD §3.0.5 C). */
  is_minor: boolean
}

export interface SmsVerifyResponse {
  /** JWT bearer token. Pass as `Authorization: Bearer <token>`. */
  token: string
  user: UserPublic
}

// --- Age Gate (PRD §1.5 / §3.0.5 C) ---

export interface UpdateBirthYearRequest {
  /** 4-digit birth year. 1900 ≤ year ≤ 2100. Server derives is_minor. */
  birth_year: number
}

// --- API error codes (emitted as body.code on 4xx) ---

export type ApiErrorCode =
  | 'AGE_REQUIRED'
  | 'MINOR_QUIET_HOURS'
  | 'MINOR_FORBIDDEN'
  | 'SMS_SEND_COOLDOWN'
  | 'SMS_VERIFY_LOCKED'
  | 'USER_INPUT_BLOCKED'
  | 'CAPTION_BLOCKED'
  | 'INVALID_CODE'
  | 'NOT_FOUND'
  | 'ALREADY_ENDED'
