/**
 * useWeaknesses — 获取弱点画像
 * GET /v1/users/me/weaknesses → WeaknessProfileResponse
 *
 * Call refetch() to load data. Does not auto-fetch on mount
 * (avoids react-hooks/set-state-in-effect lint).
 */

import { useState, useCallback, useRef } from 'react'
import { apiClient, ApiError } from '../../api/v1/client'
import type { WeaknessProfileResponse } from '../../api/v1/types'

export interface WeaknessesState {
  data: WeaknessProfileResponse | null
  isLoading: boolean
  error: string | null
}

export function useWeaknesses() {
  const [state, setState] = useState<WeaknessesState>({
    data: null,
    isLoading: false,
    error: null,
  })
  const mountedRef = useRef(true)

  const fetchWeaknesses = useCallback(async () => {
    setState(prev => ({ ...prev, isLoading: true, error: null }))
    try {
      const res = await apiClient.get<WeaknessProfileResponse>('/users/me/weaknesses')
      if (mountedRef.current) {
        setState({ data: res, isLoading: false, error: null })
      }
    } catch (err) {
      if (mountedRef.current) {
        const msg = err instanceof ApiError ? err.message : '获取弱点画像失败'
        setState(prev => ({ ...prev, isLoading: false, error: msg }))
      }
    }
  }, [])

  return { state, refetch: fetchWeaknesses }
}
