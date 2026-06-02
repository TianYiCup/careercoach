/**
 * Unit tests for useReviewUpload — fetch + poll lifecycle.
 *
 * Pins the fix for the "复盘功能异常" bug: the result page used to fetch
 * once and ignore `status`, so a `processing` upload rendered an empty
 * screen. This hook polls to a terminal status and maps failure modes.
 *
 * apiClient is mocked; timers are faked for the polling assertions.
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

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

import { useReviewUpload } from '../useReviewUpload'
import { apiClient, ApiError } from '../../../api/v1/client'

const mockGet = vi.mocked(apiClient.get)

beforeEach(() => {
  // `vi.fn()` from the module-mock factory keeps its call history + the
  // `...Once` queue between tests; reset both so counts and queued
  // responses don't leak across cases.
  mockGet.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

const DONE = {
  upload_id: 'up_1',
  status: 'done',
  turns: [{ turn_idx: 0, speaker: 'user', content: 'hi', verdict: 'neutral' }],
  summary: { score: 6.4, top_failures: ['too curt'], improvements: ['warm up'] },
  created_at: '2026-06-01T00:00:00Z',
  completed_at: '2026-06-01T00:00:02Z',
}

const PROCESSING = {
  upload_id: 'up_1',
  status: 'processing',
  turns: [],
  summary: null,
  created_at: '2026-06-01T00:00:00Z',
  completed_at: null,
}

const FAILED = { ...PROCESSING, status: 'failed', completed_at: '2026-06-01T00:00:02Z' }

describe('useReviewUpload', () => {
  it('hits the right endpoint and surfaces a terminal `done` record', async () => {
    mockGet.mockResolvedValueOnce(DONE)
    const { result } = renderHook(() => useReviewUpload('up_1'))

    await waitFor(() => expect(result.current.data?.status).toBe('done'))
    expect(mockGet).toHaveBeenCalledWith('/review/uploads/up_1')
    expect(mockGet).toHaveBeenCalledTimes(1)
    expect(result.current.error).toBeNull()
    expect(result.current.data?.summary?.score).toBe(6.4)
  })

  it('keeps `failed` as a terminal state without polling further', async () => {
    mockGet.mockResolvedValueOnce(FAILED)
    const { result } = renderHook(() => useReviewUpload('up_1'))

    await waitFor(() => expect(result.current.data?.status).toBe('failed'))
    expect(mockGet).toHaveBeenCalledTimes(1)
    expect(result.current.error).toBeNull()
  })

  it('polls while `processing` and resolves once the worker flips to `done`', async () => {
    vi.useFakeTimers()
    mockGet.mockResolvedValueOnce(PROCESSING).mockResolvedValueOnce(DONE)
    const { result } = renderHook(() => useReviewUpload('up_1'))

    // First poll resolves to processing.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.data?.status).toBe('processing')
    expect(mockGet).toHaveBeenCalledTimes(1)

    // After the poll interval the second GET sees `done`.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200)
    })
    expect(result.current.data?.status).toBe('done')
    expect(mockGet).toHaveBeenCalledTimes(2)
  })

  it('maps an ApiError to a load-failure message', async () => {
    mockGet.mockRejectedValueOnce(new ApiError(500, { message: 'boom' }))
    const { result } = renderHook(() => useReviewUpload('up_1'))

    await waitFor(() => expect(result.current.error).toBe('加载失败，请稍后重试'))
    expect(result.current.data).toBeNull()
  })

  it('maps a non-ApiError to a network message', async () => {
    mockGet.mockRejectedValueOnce(new Error('offline'))
    const { result } = renderHook(() => useReviewUpload('up_1'))

    await waitFor(() => expect(result.current.error).toBe('网络异常'))
  })
})
