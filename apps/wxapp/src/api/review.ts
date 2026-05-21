/**
 * 复盘 API 类型 — 小程序端
 * Aligned with web api/v1/types.ts + OpenAPI v0.1
 */

export type ReviewVerdict = 'win' | 'neutral' | 'lose'
export type ReviewUploadStatus = 'processing' | 'done' | 'failed'

export interface CreateReviewUploadResponse {
  upload_id: string
  status: ReviewUploadStatus
}

export interface ReviewTurn {
  turn_idx: number
  speaker: 'user' | 'opponent'
  content: string
  verdict: ReviewVerdict
  reason?: string | null
  better?: string | null
}

export interface ReviewSummary {
  score: number
  top_failures: string[]
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
