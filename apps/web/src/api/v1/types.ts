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

export interface CharacterVector {
  aggression: number
  empathy: number
  control: number
  honesty: number
  stability: number
  power_gap: number
}

export interface SessionMemory {
  visit_count: number
  last_result: ScoreResult
}

export interface CreateSessionResponse {
  session_id: string
  opening_line: string
  character_vector: CharacterVector
  memory?: SessionMemory | null
}

export interface TurnRequest {
  content: string
}

// --- SSE Frames ---
export interface ModerationFrameData {
  verdict: ModerationVerdict
  categories: ModerationCategory[]
  score: number
  redirect_resource?: { title: string; url: string } | null
}

export type ArcStage = 'opening' | 'conflict' | 'turning' | 'closing'

export type CoachStrategyKey =
  | 'placate'
  | 'concede'
  | 'avoid'
  | 'deflect'
  | 'counter'
  | 'reason'
  | 'direct'
export type CoachEffectKey = 'good' | 'mixed' | 'poor'

export interface CoachStrategyRead {
  strategy: CoachStrategyKey
  effect: CoachEffectKey
  upgrade: CoachStrategyKey
}

export interface CoachHintData {
  safe: string
  aggressive: string
  humor: string
  strategy?: CoachStrategyRead | null
}

export type SseEventFrame =
  | { event: 'arc.update'; data: { stage: ArcStage } }
  | { event: 'safety.soften'; data: { crash_streak: number } }
  | { event: 'mood.update'; data: CharacterVector }
  | { event: 'opponent.delta'; data: { text: string } }
  | { event: 'opponent.done'; data: { turn_id: string; full_text: string } }
  | { event: 'coach.hint'; data: CoachHintData }
  | { event: 'meta'; data: { turns_used: number; turns_left: number } }
  | { event: 'moderation'; data: ModerationFrameData }

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

// --- Review (PRD §3.3 / design-spec §9.6) ---

export type ReviewVerdict = 'win' | 'neutral' | 'lose'
export type ReviewUploadStatus = 'processing' | 'done' | 'failed'

export interface CreateReviewUploadRequest {
  /** Conversation transcript pasted as text. PRD §3.3 caps at 5000 chars. */
  text: string
}

export interface CreateReviewUploadResponse {
  upload_id: string
  status: ReviewUploadStatus
}

export interface ReviewTurn {
  turn_idx: number
  speaker: 'user' | 'opponent'
  content: string
  verdict: ReviewVerdict
  /** Why this user turn lost — populated on `lose` user turns only. */
  reason?: string | null
  /** Suggested rewrite — populated on `lose` user turns only. */
  better?: string | null
}

export interface ReviewSummary {
  /** Overall score 0-10. */
  score: number
  /** At most 3 failure points. */
  top_failures: string[]
  /** At most 3 actionable improvement suggestions. */
  improvements: string[]
}

export interface ReviewUploadResponse {
  upload_id: string
  status: ReviewUploadStatus
  turns: ReviewTurn[]
  summary: ReviewSummary | null
  created_at: string
  completed_at?: string | null
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

// --- Copilot (PRD §3.2 / §7.5 / design-spec §9.5) ---

export type PrivacyLevel = 'standard' | 'high'

export interface CreateCopilotSessionRequest {
  scenario_hint: string
  privacy_level?: PrivacyLevel
}

export interface CreateCopilotSessionResponse {
  copilot_id: string
  ws_url: string
}

/** WS server → client events — PRD §7.5, A-18/A-19/A-20 */
export type CopilotWsEvent =
  | { type: 'asr_partial'; text: string }
  | { type: 'asr_final'; text: string }
  | { type: 'moderation'; verdict: ModerationVerdict; categories: ModerationCategory[]; score: number; redirect_resource?: { title: string; url: string } | null }
  | { type: 'hint_delta'; text: string }
  | { type: 'hint_done'; text: string }
  | { type: 'hint_error'; message: string }
  | { type: 'asr_error'; message: string }

/** WS client → server control frame */
export interface CopilotAudioEndFrame {
  type: 'audio_end'
}

// --- Weakness Profile (PRD §7.7 / design-spec §9.7) ---

export interface WeaknessItem {
  /** Weakness tag, e.g. "主动让步" */
  tag: string
  /** Frequency count across all sessions */
  count: number
  /** Percentage of total weakness occurrences (0-100) */
  percentage: number
  /** K's sharp comment on this weakness */
  remark: string
}

export interface RecommendedScenario {
  /** Scenario id for direct navigation */
  scenario_id: string
  /** Display title */
  title: string
  /** Why K recommends this */
  reason: string
}

export interface WeaknessProfile {
  /** Total sessions analyzed */
  total_sessions: number
  /** Top weakness (displayed as hero card) */
  top_weakness: WeaknessItem
  /** Remaining weaknesses ranked #2... */
  weaknesses: WeaknessItem[]
  /** K's recommended training scenarios (max 3) */
  recommendations: RecommendedScenario[]
}

// --- Vibe (PRD §7.11 / PR #129) ---

export type VibeType = 'fire' | 'tired' | 'anxious' | 'excited' | 'meh'

export interface SetVibeRequest {
  vibe: VibeType
}

export interface VibeResponse {
  vibe: VibeType
  /** Asia/Shanghai calendar date the check-in is filed under */
  logged_date: string
}

// --- Streak (PRD §7.11 / PR #130) ---

export interface StreakResponse {
  /** Consecutive practice days up to and including today */
  current_days: number
  /** All-time best streak */
  max_days: number
}

// --- Weakness Profile API response (PR #131) ---

export interface WeaknessProfileResponse {
  /** Tracked weaknesses, highest-frequency first. Empty until first scored session. */
  weaknesses: Array<{
    tag: string
    frequency: number
    last_seen: string
  }>
  /** Scenarios to train against (max 3) */
  recommended_scenarios: ScenarioSummary[]
}

// --- Strategy Profile (Character Engine L5) ---

export interface StrategyStatItem {
  strategy: CoachStrategyKey
  count: number
  good: number
  mixed: number
  poor: number
  /** good / count — share of times this strategy landed, 0..1. */
  win_rate: number
  last_seen: string
}

export interface StrategyProfileResponse {
  /** Per-strategy stats, highest-count first. Empty until the first coached turn. */
  stats: StrategyStatItem[]
  /** Sum of all strategy counts — the experience signal driving opponent intensity. */
  total_observations: number
  /** The over-relied-but-failing strategy the opponent is built to punish, or null. */
  overrelied_strategy: CoachStrategyKey | null
}

// --- Custom Scenario (PRD §7.3 / PR #132-133) ---

export interface CustomScenarioRequest {
  /** Free-text scenario description. ≥30 chars, ≤1000. */
  description: string
}

export interface CustomScenarioResponse {
  /** Immediately usable as scenario_id for POST /v1/sessions */
  scenario_id: string
  title: string
  background: string
  persona_title: string
  opening_line: string
}

// --- Personas (PR #144 / US-A2) ---

export interface PersonaCard {
  /** Persona id, e.g. "p_hard" */
  id: string
  /** Display name, e.g. "赵刚" */
  name: string
  /** Archetype label, e.g. "强硬型" */
  style: string
  /** Persona's age */
  age: number
  /** Avatar asset key the client maps to an image */
  avatar: string
  /** One-line persona blurb shown on the picker card */
  background: string
  /** 1 = easy / 5 = hard. Drives easy→hard ordering. */
  difficulty: number
}

export interface PersonaListResponse {
  items: PersonaCard[]
  total: number
}

// --- Email Auth (PR #160-161 / PR-A2) ---

export interface EmailSendRequest {
  /** Recipient email address. */
  email: string
}

export interface EmailSendResponse {
  /** Seconds until the user may request another code. Hardcoded 60s in v0.1. */
  ttl: number
}

export interface EmailVerifyRequest {
  email: string
  /** 6-digit verification code received by email. */
  code: string
}

export interface EmailVerifyResponse {
  /** JWT bearer token. Pass as `Authorization: Bearer <token>`. */
  token: string
  user: UserPublic
}

// --- TTS (PR #147-149 / US-B2) ---

export type TtsAudioFormat = 'mp3' | 'ogg' | 'wav'

export interface TTSSynthesizeRequest {
  /** Mandarin text to synthesize. ≤200 chars. Subject to red-line moderation. */
  text: string
  /** Voice id. v1 ships only "k-warm". */
  voice?: 'k-warm'
  /** Audio container for the response body. Default mp3. */
  audio_format?: TtsAudioFormat
}

// --- Mascot Timeline (PR #151 / PRD §7.10) ---

export type MascotTimelineExpression =
  | 'confident'
  | 'burning'
  | 'thinking'
  | 'shenfeng'
  | 'fanche'
  | 'integrate'
  | 'caring'
  | 'sleeping'

export interface MascotMoment {
  turn_idx: number
  expression: MascotTimelineExpression
  /** UTC timestamp the moment was recorded (CLAUDE.md §6). */
  at: string
}

export interface LogMascotMomentRequest {
  session_id: string
  turn_idx: number
  expression: MascotTimelineExpression
}

export interface MascotExpressionTimelineResponse {
  items: MascotMoment[]
  total: number
}

// --- Voice Turn SSE Frame (PR #145 / US-A3) ---

export interface VoiceTranscribedFrame {
  event: 'user.transcribed'
  data: { text: string }
}

// SSE frames now include VoiceTranscribedFrame
export type SseEventFrameWithVoice =
  | SseEventFrame
  | VoiceTranscribedFrame

// --- API error codes (emitted as body.code on 4xx) ---

export type ApiErrorCode =
  | 'AGE_REQUIRED'
  | 'MINOR_QUIET_HOURS'
  | 'MINOR_FORBIDDEN'
  | 'SMS_SEND_COOLDOWN'
  | 'SMS_VERIFY_LOCKED'
  | 'EMAIL_SEND_COOLDOWN'
  | 'EMAIL_VERIFY_LOCKED'
  | 'USER_INPUT_BLOCKED'
  | 'CAPTION_BLOCKED'
  | 'SCENARIO_BLOCKED'
  | 'TTS_INPUT_BLOCKED'
  | 'TTS_UNAVAILABLE'
  | 'RATE_LIMIT_EXCEEDED'
  | 'INVALID_CODE'
  | 'NOT_FOUND'
  | 'ALREADY_ENDED'
