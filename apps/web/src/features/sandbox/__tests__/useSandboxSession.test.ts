/**
 * Unit tests for useSandboxSession internal logic.
 *
 * We test the pure helper functions (deriveExpression, withExpression)
 * directly, and test the SSE frame handling / state transitions via
 * a lightweight hook render with @testing-library/react.
 *
 * These are NOT integration tests — apiClient and postSSE are mocked.
 *
 * @vitest-environment jsdom
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import type { SseEventFrame, ModerationFrameData } from '../../../api/v1/types'
import type { SandboxState } from '../useSandboxSession'

// --- Mock api layer ---

vi.mock('../../../api/v1/client', () => ({
  apiClient: {
    post: vi.fn(),
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

vi.mock('../../../api/v1/sse', () => ({
  postSSE: vi.fn(),
}))

// Import after mocks are set up
import { useSandboxSession } from '../useSandboxSession'
import { apiClient } from '../../../api/v1/client'
import { postSSE } from '../../../api/v1/sse'

const mockPost = vi.mocked(apiClient.post)
const mockPostSSE = vi.mocked(postSSE)

afterEach(() => {
  vi.restoreAllMocks()
})

// --- Helper: get current state from hook result ---

function getState(): SandboxState {
  return hookRef.result.current.state
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let hookRef: any

function renderSessionHook() {
  hookRef = renderHook(() => useSandboxSession())
  return hookRef
}

// =====================================================================
// deriveExpression (tested indirectly through state changes)
// =====================================================================

describe('initial state', () => {
  it('matches the expected shape', () => {
    renderSessionHook()
    const s = getState()
    expect(s.sessionId).toBeNull()
    expect(s.messages).toEqual([])
    expect(s.streamingText).toBe('')
    expect(s.isStreaming).toBe(false)
    expect(s.hints).toBeNull()
    expect(s.activeTone).toBe('aggro')
    expect(s.turnsUsed).toBe(0)
    expect(s.turnsLeft).toBe(30)
    expect(s.score).toBeNull()
    expect(s.started).toBe(false)
    expect(s.mascotExpression).toBe('confident')
    expect(s.isQuietHours).toBe(false)
    expect(s.error).toBeNull()
    expect(s.redirectResource).toBeNull()
  })
})

// =====================================================================
// startSession
// =====================================================================

describe('startSession', () => {
  it('sets started + sessionId + opening message on success', async () => {
    mockPost.mockResolvedValueOnce({
      session_id: 'ses_test1',
      opening_line: '你今天怎么又迟到了？',
    })

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox',
        scenario_id: 'sc_001',
        persona_id: 'p_hard',
        user_goal: 'test',
      })
    })

    const s = getState()
    expect(s.sessionId).toBe('ses_test1')
    expect(s.started).toBe(true)
    expect(s.messages).toHaveLength(1)
    expect(s.messages[0]).toEqual({ role: 'opponent', text: '你今天怎么又迟到了？' })
    // mascot auto-derives 'thinking' after start (turnsUsed=0 + started=true)
    expect(s.mascotExpression).toBe('thinking')
  })

  it('sets error banner on generic failure', async () => {
    mockPost.mockRejectedValueOnce(new Error('Network error'))

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox',
        scenario_id: 'sc_001',
        persona_id: 'p_hard',
        user_goal: 'test',
      })
    })

    expect(getState().error).toBe('加载失败，请稍后重试')
    expect(getState().started).toBe(false)
  })

  it('sets isQuietHours on 403 MINOR_QUIET_HOURS', async () => {
    const { ApiError } = await import('../../../api/v1/client')
    mockPost.mockRejectedValueOnce(new ApiError(403, { code: 'MINOR_QUIET_HOURS' }))

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox',
        scenario_id: 'sc_001',
        persona_id: 'p_hard',
        user_goal: 'test',
      })
    })

    expect(getState().isQuietHours).toBe(true)
    expect(getState().error).toBeNull()
  })

  it('ignores 401 — AuthProvider handles globally', async () => {
    const { ApiError } = await import('../../../api/v1/client')
    mockPost.mockRejectedValueOnce(new ApiError(401, { message: 'Unauthorized' }))

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox',
        scenario_id: 'sc_001',
        persona_id: 'p_hard',
        user_goal: 'test',
      })
    })

    expect(getState().error).toBeNull()
    expect(getState().started).toBe(false)
  })
})

// =====================================================================
// SSE frame handling via sendTurn
// =====================================================================

async function setupActiveSession() {
  mockPost.mockResolvedValueOnce({
    session_id: 'ses_sse',
    opening_line: '你好',
    character_vector: {
      aggression: 60,
      empathy: 30,
      control: 75,
      honesty: 50,
      stability: 80,
      power_gap: 70,
    },
  })

  renderSessionHook()
  await act(async () => {
    await hookRef.result.current.startSession({
      mode: 'sandbox',
      scenario_id: 'sc_001',
      persona_id: 'p_hard',
      user_goal: 'test',
    })
  })
  return hookRef
}

describe('sendTurn SSE frame handling', () => {
  it('opponent.delta appends to streamingText', async () => {
    mockPostSSE.mockImplementation(async (_path, _body, onFrame) => {
      onFrame({ event: 'opponent.delta', data: { text: '你' } } as SseEventFrame)
      onFrame({ event: 'opponent.delta', data: { text: '好' } } as SseEventFrame)
      onFrame({ event: 'opponent.done', data: { turn_id: 't_1', full_text: '你好' } } as SseEventFrame)
    })

    await setupActiveSession()
    await act(async () => {
      await hookRef.result.current.sendTurn('hello')
    })

    // After done, streamingText is cleared
    expect(getState().streamingText).toBe('')
    expect(getState().isStreaming).toBe(false)
    // User message + opponent done message
    expect(getState().messages).toHaveLength(3)
    expect(getState().messages[2]).toEqual({ role: 'opponent', text: '你好' })
  })

  it('mood.update replaces characterVector for the L9 radar', async () => {
    const newMood = {
      aggression: 76,
      empathy: 28,
      control: 75,
      honesty: 50,
      stability: 65,
      power_gap: 70,
    }
    mockPostSSE.mockImplementation(async (_path, _body, onFrame) => {
      onFrame({ event: 'mood.update', data: newMood } as SseEventFrame)
      onFrame({ event: 'opponent.done', data: { turn_id: 't_1', full_text: '哼' } } as SseEventFrame)
    })

    await setupActiveSession()
    // Seeded from session create
    expect(getState().characterVector?.aggression).toBe(60)

    await act(async () => {
      await hookRef.result.current.sendTurn('我就是不加班')
    })

    // Mood frame swapped the vector before the reply landed
    expect(getState().characterVector).toEqual(newMood)
  })

  it('coach.hint stores hints and derives mascot expression', async () => {
    mockPostSSE.mockImplementation(async (_path, _body, onFrame) => {
      onFrame({ event: 'opponent.done', data: { turn_id: 't_1', full_text: '好的' } } as SseEventFrame)
      onFrame({
        event: 'coach.hint',
        data: { safe: '稳', aggressive: '刚', humor: '活' },
      } as SseEventFrame)
      onFrame({ event: 'meta', data: { turns_used: 1, turns_left: 29 } } as SseEventFrame)
    })

    await setupActiveSession()
    await act(async () => {
      await hookRef.result.current.sendTurn('hi')
    })

    const s = getState()
    expect(s.hints).toEqual({ safe: '稳', aggressive: '刚', humor: '活' })
    // activeTone is 'aggro' → fired-up expression
    expect(s.mascotExpression).toBe('fired-up')
    expect(s.turnsUsed).toBe(1)
    expect(s.turnsLeft).toBe(29)
  })

  it('moderation redirect sets redirectResource and stops streaming', async () => {
    const modFrame: SseEventFrame = {
      event: 'moderation',
      data: {
        verdict: 'redirect',
        categories: ['self_harm'],
        score: 0.95,
        redirect_resource: { title: '心理援助热线', url: 'https://example.com/help' },
      } as ModerationFrameData,
    }

    mockPostSSE.mockImplementation(async (_path, _body, onFrame) => {
      onFrame(modFrame)
    })

    await setupActiveSession()
    await act(async () => {
      await hookRef.result.current.sendTurn('self harm content')
    })

    const s = getState()
    expect(s.redirectResource).toEqual({ title: '心理援助热线', url: 'https://example.com/help' })
    expect(s.isStreaming).toBe(false)
    expect(s.streamingText).toBe('')
  })

  it('moderation block inserts system message and stops streaming', async () => {
    const modFrame: SseEventFrame = {
      event: 'moderation',
      data: {
        verdict: 'block',
        categories: ['violence'],
        score: 0.9,
      } as ModerationFrameData,
    }

    mockPostSSE.mockImplementation(async (_path, _body, onFrame) => {
      onFrame(modFrame)
    })

    await setupActiveSession()
    await act(async () => {
      await hookRef.result.current.sendTurn('violent content')
    })

    const s = getState()
    expect(s.isStreaming).toBe(false)
    expect(s.messages.at(-1)?.text).toBe('（对话内容未通过审核，请换一个话题）')
  })

  it('moderation allow is a no-op', async () => {
    const modFrame: SseEventFrame = {
      event: 'moderation',
      data: {
        verdict: 'allow',
        categories: [],
        score: 0.1,
      } as ModerationFrameData,
    }

    mockPostSSE.mockImplementation(async (_path, _body, onFrame) => {
      onFrame({ event: 'opponent.delta', data: { text: 'OK' } } as SseEventFrame)
      onFrame(modFrame)
      onFrame({ event: 'opponent.done', data: { turn_id: 't_1', full_text: 'OK' } } as SseEventFrame)
    })

    await setupActiveSession()
    await act(async () => {
      await hookRef.result.current.sendTurn('normal content')
    })

    expect(getState().redirectResource).toBeNull()
    expect(getState().messages.at(-1)).toEqual({ role: 'opponent', text: 'OK' })
  })
})

// =====================================================================
// Dismiss callbacks
// =====================================================================

describe('dismiss callbacks', () => {
  it('dismissError clears error', async () => {
    mockPost.mockRejectedValueOnce(new Error('fail'))

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox',
        scenario_id: 'sc_001',
        persona_id: 'p_hard',
        user_goal: 'test',
      })
    })
    expect(getState().error).toBeTruthy()

    act(() => hookRef.result.current.dismissError())
    expect(getState().error).toBeNull()
  })

  it('dismissQuietHours clears isQuietHours', async () => {
    const { ApiError } = await import('../../../api/v1/client')
    mockPost.mockRejectedValueOnce(new ApiError(403, { code: 'MINOR_QUIET_HOURS' }))

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox',
        scenario_id: 'sc_001',
        persona_id: 'p_hard',
        user_goal: 'test',
      })
    })
    expect(getState().isQuietHours).toBe(true)

    act(() => hookRef.result.current.dismissQuietHours())
    expect(getState().isQuietHours).toBe(false)
  })

  it('dismissModeration clears redirectResource', async () => {
    const modFrame: SseEventFrame = {
      event: 'moderation',
      data: {
        verdict: 'redirect',
        categories: ['self_harm'],
        score: 0.95,
        redirect_resource: { title: '热线', url: 'https://help' },
      } as ModerationFrameData,
    }

    mockPost.mockResolvedValueOnce({ session_id: 'ses_1', opening_line: 'hi' })
    mockPostSSE.mockImplementation(async (_p, _b, onFrame) => { onFrame(modFrame) })

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox',
        scenario_id: 'sc_001',
        persona_id: 'p_hard',
        user_goal: 'test',
      })
    })
    await act(async () => {
      await hookRef.result.current.sendTurn('test')
    })
    expect(getState().redirectResource).toBeTruthy()

    act(() => hookRef.result.current.dismissModeration())
    expect(getState().redirectResource).toBeNull()
  })
})

// =====================================================================
// Mascot expression derivation (indirect via state transitions)
// =====================================================================

describe('mascot expression auto-derivation', () => {
  it('shenfeng score → godlike', async () => {
    mockPost.mockResolvedValueOnce({ session_id: 'ses_1', opening_line: 'hi' })
    mockPost.mockResolvedValueOnce({
      score: { aura: 9, logic: 8, emotion: 9, professionalism: 8, goal_achieve: 9, highlights: '', failures: '', result: 'shenfeng' },
      weakness_updates: [],
    })

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox', scenario_id: 'sc_001', persona_id: 'p_hard', user_goal: 'test',
      })
    })
    await act(async () => {
      await hookRef.result.current.endSession()
    })

    expect(getState().mascotExpression).toBe('godlike')
  })

  it('fanche score → crashed', async () => {
    mockPost.mockResolvedValueOnce({ session_id: 'ses_1', opening_line: 'hi' })
    mockPost.mockResolvedValueOnce({
      score: { aura: 2, logic: 3, emotion: 2, professionalism: 3, goal_achieve: 2, highlights: '', failures: '全翻车', result: 'fanche' },
      weakness_updates: [],
    })

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox', scenario_id: 'sc_001', persona_id: 'p_hard', user_goal: 'test',
      })
    })
    await act(async () => {
      await hookRef.result.current.endSession()
    })

    expect(getState().mascotExpression).toBe('crashed')
  })

  it('low turnsLeft (≤3) → fired-up', async () => {
    mockPost.mockResolvedValueOnce({ session_id: 'ses_1', opening_line: 'hi' })
    mockPostSSE.mockImplementation(async (_p, _b, onFrame) => {
      onFrame({ event: 'meta', data: { turns_used: 27, turns_left: 3 } } as SseEventFrame)
      onFrame({ event: 'opponent.done', data: { turn_id: 't_1', full_text: 'ok' } } as SseEventFrame)
    })

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox', scenario_id: 'sc_001', persona_id: 'p_hard', user_goal: 'test',
      })
    })
    await act(async () => {
      await hookRef.result.current.sendTurn('test')
    })

    expect(getState().mascotExpression).toBe('fired-up')
  })

  it('setTone changes active tone and re-derives expression with hints', async () => {
    mockPost.mockResolvedValueOnce({ session_id: 'ses_1', opening_line: 'hi' })
    mockPostSSE.mockImplementation(async (_p, _b, onFrame) => {
      onFrame({ event: 'meta', data: { turns_used: 1, turns_left: 29 } } as SseEventFrame)
      onFrame({ event: 'opponent.done', data: { turn_id: 't_1', full_text: 'ok' } } as SseEventFrame)
      onFrame({ event: 'coach.hint', data: { safe: 's', aggressive: 'a', humor: 'h' } } as SseEventFrame)
    })

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox', scenario_id: 'sc_001', persona_id: 'p_hard', user_goal: 'test',
      })
    })
    await act(async () => {
      await hookRef.result.current.sendTurn('test')
    })

    // Default aggro → fired-up
    expect(getState().mascotExpression).toBe('fired-up')

    // Switch to safe → caring
    act(() => hookRef.result.current.setTone('safe'))
    expect(getState().mascotExpression).toBe('caring')

    // Switch to fun → clowning
    act(() => hookRef.result.current.setTone('fun'))
    expect(getState().mascotExpression).toBe('clowning')
  })
})

// =====================================================================
// reset
// =====================================================================

describe('reset', () => {
  it('returns to initial state after session activity', async () => {
    mockPost.mockResolvedValueOnce({ session_id: 'ses_1', opening_line: 'hi' })

    renderSessionHook()
    await act(async () => {
      await hookRef.result.current.startSession({
        mode: 'sandbox', scenario_id: 'sc_001', persona_id: 'p_hard', user_goal: 'test',
      })
    })
    expect(getState().started).toBe(true)

    act(() => hookRef.result.current.reset())
    expect(getState()).toMatchObject({
      sessionId: null,
      started: false,
      messages: [],
      mascotExpression: 'confident',
    })
  })
})
