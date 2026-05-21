/**
 * useCustomScenario — 用户自定义场景生成
 * POST /v1/scenarios/custom → CustomScenarioResponse
 * 返回可立即用于 startSession 的 scenario_id
 */

import { useState, useCallback } from 'react'
import { apiClient, ApiError } from '../../api/v1/client'
import type { CustomScenarioRequest, CustomScenarioResponse } from '../../api/v1/types'

export interface CustomScenarioState {
  /** Generated scenario (null = not yet generated) */
  scenario: CustomScenarioResponse | null
  /** Is the LLM generating the scenario? */
  isGenerating: boolean
  error: string | null
}

export function useCustomScenario() {
  const [state, setState] = useState<CustomScenarioState>({
    scenario: null,
    isGenerating: false,
    error: null,
  })

  /** Generate a custom scenario from user description */
  const generate = useCallback(async (description: string) => {
    if (description.trim().length < 30) {
      setState(prev => ({ ...prev, error: '描述至少 30 个字哦' }))
      return null
    }

    setState(prev => ({ ...prev, isGenerating: true, error: null }))
    try {
      const res = await apiClient.post<CustomScenarioResponse>(
        '/scenarios/custom',
        { description: description.trim() } satisfies CustomScenarioRequest,
      )
      setState({ scenario: res, isGenerating: false, error: null })
      return res
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : '场景生成失败'
      setState(prev => ({ ...prev, isGenerating: false, error: msg }))
      return null
    }
  }, [])

  /** Reset state (e.g. when closing the dialog) */
  const reset = useCallback(() => {
    setState({ scenario: null, isGenerating: false, error: null })
  }, [])

  return { state, generate, reset }
}
