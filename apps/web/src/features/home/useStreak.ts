/**
 * useStreak — 获取连胜天数
 * GET /v1/streak → StreakResponse
 */

import { useState, useCallback, useRef } from 'react'
import { apiClient, ApiError } from '../../api/v1/client'
import type { StreakResponse } from '../../api/v1/types'

export interface StreakState {
  currentDays: number
  maxDays: number
  isLoading: boolean
  error: string | null
}

const INITIAL_STATE: StreakState = {
  currentDays: 0,
  maxDays: 0,
  isLoading: false,
  error: null,
}

export function useStreak() {
  const [state, setState] = useState<StreakState>(INITIAL_STATE)
  const mountedRef = useRef(true)

  const fetchStreak = useCallback(async () => {
    setState(prev => ({ ...prev, isLoading: true, error: null }))
    try {
      const res = await apiClient.get<StreakResponse>('/streak')
      if (mountedRef.current) {
        setState({ currentDays: res.current_days, maxDays: res.max_days, isLoading: false, error: null })
      }
    } catch (err) {
      if (mountedRef.current) {
        const msg = err instanceof ApiError ? err.message : '获取连胜失败'
        setState(prev => ({ ...prev, isLoading: false, error: msg }))
      }
    }
  }, [])

  return { state, refetch: fetchStreak }
}
