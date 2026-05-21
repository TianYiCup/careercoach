/**
 * useVibe — 获取/设置今日情绪
 * POST /v1/vibe/today → VibeResponse
 *
 * API VibeType ('fire'|'tired'|'anxious'|'excited'|'meh')
 * UI VibeType ('燃爆'|'想躺平'|'莫名烦'|'雄心勃勃'|'佛系')
 */

import { useState, useCallback } from 'react'
import { apiClient, ApiError } from '../../api/v1/client'
import type { VibeType as ApiVibeType, SetVibeRequest, VibeResponse } from '../../api/v1/types'

/** UI-facing vibe labels — matches VibePill component */
export type UiVibeType = '燃爆' | '想躺平' | '莫名烦' | '雄心勃勃' | '佛系'

/** Bidirectional mapping between API enum and Chinese UI label */
const API_TO_UI: Record<ApiVibeType, UiVibeType> = {
  fire: '燃爆',
  tired: '想躺平',
  anxious: '莫名烦',
  excited: '雄心勃勃',
  meh: '佛系',
}

const UI_TO_API: Record<UiVibeType, ApiVibeType> = {
  '燃爆': 'fire',
  '想躺平': 'tired',
  '莫名烦': 'anxious',
  '雄心勃勃': 'excited',
  '佛系': 'meh',
}

export function apiVibeToUi(v: ApiVibeType): UiVibeType {
  return API_TO_UI[v]
}

export function uiVibeToApi(v: UiVibeType): ApiVibeType {
  return UI_TO_API[v]
}

export interface VibeState {
  /** Currently selected vibe (null = not yet loaded or not set) */
  activeVibe: UiVibeType | null
  /** Date string the check-in is filed under */
  loggedDate: string | null
  /** Is a vibe POST in flight? */
  isSubmitting: boolean
  error: string | null
}

export function useVibe() {
  const [state, setState] = useState<VibeState>({
    activeVibe: null,
    loggedDate: null,
    isSubmitting: false,
    error: null,
  })

  /** Set today's vibe via POST /v1/vibe/today */
  const setVibe = useCallback(async (vibe: UiVibeType) => {
    setState(prev => ({ ...prev, isSubmitting: true, error: null }))
    try {
      const res = await apiClient.post<VibeResponse>('/vibe/today', { vibe: uiVibeToApi(vibe) } satisfies SetVibeRequest)
      setState({
        activeVibe: apiVibeToUi(res.vibe),
        loggedDate: res.logged_date,
        isSubmitting: false,
        error: null,
      })
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : '设置情绪失败'
      setState(prev => ({ ...prev, isSubmitting: false, error: msg }))
    }
  }, [])

  return { state, setVibe }
}
