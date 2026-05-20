import { useState, useCallback } from 'react'
import { apiClient, ApiError } from '../../api/v1/client'
import type {
  ShareCardResponse,
  SessionShareCardRequest,
  WeeklyShareCardRequest,
  WrappedShareCardRequest,
} from '../../api/v1/types'

export type ShareCardMode = 'session' | 'weekly' | 'wrapped'

export interface ShareCardState {
  /** Current mode */
  mode: ShareCardMode
  /** Loading state */
  isGenerating: boolean
  /** Generated card data */
  card: ShareCardResponse | null
  /** User-visible error */
  error: string | null
}

const INITIAL_STATE: ShareCardState = {
  mode: 'session',
  isGenerating: false,
  card: null,
  error: null,
}

/** Human-friendly error from sharecard API — distinguishes CAPTION_BLOCKED */
function _shareCardError(err: unknown): string {
  if (err instanceof ApiError) {
    const code = (err.body as { code?: string })?.code
    if (code === 'CAPTION_BLOCKED') return '文案被拦截，请修改后重试'
    if (err.status === 404) return '战报数据不存在'
  }
  return '生成失败，请稍后重试'
}

/**
 * useShareCard — wraps the three sharecard POST endpoints.
 *
 * Flow:
 *   1. Choose mode (session / weekly / wrapped)
 *   2. Call generate*()
 *   3. Get back ShareCardResponse (png_url + share_links)
 *   4. User can download PNG or share via link
 */
export function useShareCard() {
  const [state, setState] = useState<ShareCardState>(INITIAL_STATE)

  /** Generate a session sharecard — POST /sharecards/session/:sessionId */
  const generateSessionCard = useCallback(
    async (sessionId: string, req?: SessionShareCardRequest) => {
      setState((s) => ({ ...s, mode: 'session', isGenerating: true, error: null, card: null }))
      try {
        const res = await apiClient.post<ShareCardResponse>(
          `/sharecards/session/${sessionId}`,
          req ?? { include_qrcode: false },
        )
        setState((s) => ({ ...s, isGenerating: false, card: res }))
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return
        setState((s) => ({
          ...s,
          isGenerating: false,
          error: _shareCardError(err),
        }))
      }
    },
    [],
  )

  /** Generate a weekly sharecard — POST /sharecards/weekly */
  const generateWeeklyCard = useCallback(
    async (req?: WeeklyShareCardRequest) => {
      setState((s) => ({ ...s, mode: 'weekly', isGenerating: true, error: null, card: null }))
      try {
        const res = await apiClient.post<ShareCardResponse>(
          '/sharecards/weekly',
          req ?? { include_qrcode: false },
        )
        setState((s) => ({ ...s, isGenerating: false, card: res }))
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return
        setState((s) => ({
          ...s,
          isGenerating: false,
          error: _shareCardError(err),
        }))
      }
    },
    [],
  )

  /** Generate a wrapped sharecard — POST /sharecards/wrapped/year/:year */
  const generateWrappedCard = useCallback(
    async (year: number, req?: WrappedShareCardRequest) => {
      setState((s) => ({ ...s, mode: 'wrapped', isGenerating: true, error: null, card: null }))
      try {
        const res = await apiClient.post<ShareCardResponse>(
          `/sharecards/wrapped/year/${year}`,
          req ?? { include_qrcode: true },
        )
        setState((s) => ({ ...s, isGenerating: false, card: res }))
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return
        setState((s) => ({
          ...s,
          isGenerating: false,
          error: _shareCardError(err),
        }))
      }
    },
    [],
  )

  /** Dismiss error */
  const dismissError = useCallback(() => {
    setState((s) => (s.error === null ? s : { ...s, error: null }))
  }, [])

  /** Reset state */
  const reset = useCallback(() => {
    setState(INITIAL_STATE)
  }, [])

  return {
    state,
    generateSessionCard,
    generateWeeklyCard,
    generateWrappedCard,
    dismissError,
    reset,
  }
}
