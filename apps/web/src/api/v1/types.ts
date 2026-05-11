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
  user_id: string
  session_id?: string | null
}

export interface ModerationCheckResponse {
  verdict: ModerationVerdict
  categories: ModerationCategory[]
  score: number
  redirect_resource?: { title: string; url: string } | null
  trace_id: string
}

// --- Error ---
export interface ErrorResponse {
  code: string
  message: string
  trace_id: string
}
