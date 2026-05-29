/**
 * useStrategyProfile — 获取策略画像 (Character Engine L5)
 * GET /v1/users/me/profile → StrategyProfileResponse
 *
 * Call refetch() to load data. Does not auto-fetch on mount
 * (avoids react-hooks/set-state-in-effect lint) — mirrors useWeaknesses.
 */

import { useCallback, useRef, useState } from 'react'
import { apiClient, ApiError } from '../../api/v1/client'
import type { StrategyProfileResponse } from '../../api/v1/types'

export interface StrategyProfileState {
  data: StrategyProfileResponse | null
  isLoading: boolean
  error: string | null
}

export function useStrategyProfile() {
  const [state, setState] = useState<StrategyProfileState>({
    data: null,
    isLoading: false,
    error: null,
  })
  const mountedRef = useRef(true)

  const fetchProfile = useCallback(async () => {
    setState((prev) => ({ ...prev, isLoading: true, error: null }))
    try {
      const res = await apiClient.get<StrategyProfileResponse>('/users/me/profile')
      if (mountedRef.current) {
        setState({ data: res, isLoading: false, error: null })
      }
    } catch (err) {
      if (mountedRef.current) {
        const msg = err instanceof ApiError ? err.message : '获取策略画像失败'
        setState((prev) => ({ ...prev, isLoading: false, error: msg }))
      }
    }
  }, [])

  return { state, refetch: fetchProfile }
}
