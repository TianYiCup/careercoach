/**
 * Unit tests for useStrategyProfile — fetch lifecycle + error mapping.
 * apiClient is mocked.
 *
 * @vitest-environment jsdom
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'

vi.mock('../../../api/v1/client', () => ({
  apiClient: {
    get: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown) {
      super(`API Error ${status}`)
      this.status = status
      this.body = body
    }
  },
}))

import { useStrategyProfile } from '../useStrategyProfile'
import { apiClient, ApiError } from '../../../api/v1/client'

const mockGet = vi.mocked(apiClient.get)

afterEach(() => {
  vi.restoreAllMocks()
})

const SAMPLE = {
  stats: [
    { strategy: 'placate', count: 7, good: 2, mixed: 2, poor: 3, win_rate: 0.29, last_seen: '2026-05-29' },
  ],
  total_observations: 7,
  overrelied_strategy: 'placate',
}

describe('useStrategyProfile', () => {
  it('starts empty and does not auto-fetch', () => {
    const { result } = renderHook(() => useStrategyProfile())
    expect(result.current.state).toEqual({ data: null, isLoading: false, error: null })
    expect(mockGet).not.toHaveBeenCalled()
  })

  it('loads the profile on refetch and hits the right endpoint', async () => {
    mockGet.mockResolvedValueOnce(SAMPLE)
    const { result } = renderHook(() => useStrategyProfile())

    await act(async () => {
      await result.current.refetch()
    })

    expect(mockGet).toHaveBeenCalledWith('/users/me/profile')
    expect(result.current.state.data).toEqual(SAMPLE)
    expect(result.current.state.isLoading).toBe(false)
    expect(result.current.state.error).toBeNull()
  })

  it('surfaces an ApiError message', async () => {
    mockGet.mockRejectedValueOnce(new ApiError(500, { message: 'boom' }))
    const { result } = renderHook(() => useStrategyProfile())

    await act(async () => {
      await result.current.refetch()
    })

    expect(result.current.state.error).toBe('API Error 500')
    expect(result.current.state.data).toBeNull()
  })

  it('falls back to a generic message for a non-ApiError', async () => {
    mockGet.mockRejectedValueOnce(new Error('network down'))
    const { result } = renderHook(() => useStrategyProfile())

    await act(async () => {
      await result.current.refetch()
    })

    expect(result.current.state.error).toBe('获取策略画像失败')
  })
})
