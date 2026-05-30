/**
 * Unit tests for useHintTts.
 *
 * Covers:
 *  - fetch is triggered when a finalized hint arrives
 *  - same text in a row doesn't refetch (deps gate)
 *  - rapid hint change aborts the previous in-flight fetch
 *  - muted / null hintText skip the fetch entirely
 *  - autoplay rejection surfaces as a non-fatal error state
 *  - HTTP 4xx/5xx classify into typed error kinds
 *  - unmount releases the object URL and pauses audio
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'

import { useHintTts } from '../useHintTts'

vi.mock('../../../api/v1/auth-token', () => ({
  getAuthToken: () => 'tok_test',
}))

// jsdom doesn't implement URL.createObjectURL / revokeObjectURL.
const createObjectURL = vi.fn(() => 'blob:mock-url')
const revokeObjectURL = vi.fn()
vi.stubGlobal('URL', {
  ...globalThis.URL,
  createObjectURL,
  revokeObjectURL,
})

// jsdom's HTMLMediaElement.play returns undefined and never resolves a
// promise the way browsers do. Stub play so we can drive autoplay
// rejection / success deterministically.
type PlayResult = 'resolve' | 'reject'
let nextPlayResult: PlayResult = 'resolve'
const playSpy = vi.fn<() => Promise<void>>()
const pauseSpy = vi.fn()
beforeEach(() => {
  nextPlayResult = 'resolve'
  playSpy.mockReset()
  playSpy.mockImplementation(() => {
    return nextPlayResult === 'resolve'
      ? Promise.resolve()
      : Promise.reject(new DOMException('autoplay blocked', 'NotAllowedError'))
  })
  pauseSpy.mockClear()
  createObjectURL.mockClear()
  revokeObjectURL.mockClear()
  HTMLMediaElement.prototype.play = playSpy as unknown as HTMLMediaElement['play']
  HTMLMediaElement.prototype.pause = pauseSpy as unknown as HTMLMediaElement['pause']
})

const fetchSpy = vi.fn<typeof fetch>()
vi.stubGlobal('fetch', fetchSpy)

afterEach(() => {
  fetchSpy.mockReset()
})

function mockOk(): Response {
  return new Response(new Blob(['x'], { type: 'audio/mpeg' }), {
    status: 200,
    headers: { 'Content-Type': 'audio/mpeg' },
  })
}

function mockStatus(status: number): Response {
  return new Response('{"code":"X","message":"x"}', {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('useHintTts', () => {
  it('fetches and plays when a hint text is provided', async () => {
    fetchSpy.mockResolvedValueOnce(mockOk())

    const { result } = renderHook(({ text }) => useHintTts({ hintText: text }), {
      initialProps: { text: '先反问预算' as string | null },
    })

    expect(result.current.isSpeaking).toBe(true)
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1)
    })
    expect(playSpy).toHaveBeenCalledTimes(1)
    const [url, init] = fetchSpy.mock.calls[0]!
    expect(String(url)).toContain('/tts/synthesize')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({
      text: '先反问预算',
      audio_format: 'mp3',
    })
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok_test')
  })

  it('does not fetch when hintText is null', async () => {
    renderHook(() => useHintTts({ hintText: null }))
    await new Promise((r) => setTimeout(r, 0))
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('does not fetch when muted', async () => {
    renderHook(() => useHintTts({ hintText: '说点啥', muted: true }))
    await new Promise((r) => setTimeout(r, 0))
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('does not refetch when the same hint text re-renders', async () => {
    fetchSpy.mockResolvedValue(mockOk())

    const { rerender } = renderHook(({ text }) => useHintTts({ hintText: text }), {
      initialProps: { text: '同一句' as string | null },
    })
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1))

    rerender({ text: '同一句' })
    await new Promise((r) => setTimeout(r, 0))
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('aborts the previous fetch when a new hint arrives mid-flight', async () => {
    // First call: hang so we can interrupt
    let abortedFirst = false
    fetchSpy.mockImplementationOnce((_input, init) => {
      return new Promise((_resolve, reject) => {
        ;(init?.signal as AbortSignal | undefined)?.addEventListener('abort', () => {
          abortedFirst = true
          reject(new DOMException('aborted', 'AbortError'))
        })
      })
    })
    fetchSpy.mockResolvedValueOnce(mockOk())

    const { rerender } = renderHook(({ text }) => useHintTts({ hintText: text }), {
      initialProps: { text: '第一句' as string | null },
    })
    await new Promise((r) => setTimeout(r, 0))
    rerender({ text: '第二句' })

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2))
    expect(abortedFirst).toBe(true)
  })

  it('marks autoplay rejection as a non-fatal error', async () => {
    fetchSpy.mockResolvedValueOnce(mockOk())
    nextPlayResult = 'reject'

    const { result } = renderHook(() => useHintTts({ hintText: '试试看' }))

    await waitFor(() => {
      expect(result.current.error).toBe('autoplay_blocked')
    })
    expect(result.current.isSpeaking).toBe(false)
  })

  it('classifies 401 as auth_required', async () => {
    fetchSpy.mockResolvedValueOnce(mockStatus(401))

    const { result } = renderHook(() => useHintTts({ hintText: '需要登录' }))

    await waitFor(() => {
      expect(result.current.error).toBe('auth_required')
    })
    expect(playSpy).not.toHaveBeenCalled()
  })

  it('classifies 422 as tts_blocked (moderation rejected)', async () => {
    fetchSpy.mockResolvedValueOnce(mockStatus(422))

    const { result } = renderHook(() => useHintTts({ hintText: '违禁词' }))

    await waitFor(() => {
      expect(result.current.error).toBe('tts_blocked')
    })
  })

  it('classifies 503 as tts_unavailable', async () => {
    fetchSpy.mockResolvedValueOnce(mockStatus(503))

    const { result } = renderHook(() => useHintTts({ hintText: '厂商挂了' }))

    await waitFor(() => {
      expect(result.current.error).toBe('tts_unavailable')
    })
  })

  it('classifies a thrown network error as network', async () => {
    fetchSpy.mockRejectedValueOnce(new TypeError('failed to fetch'))

    const { result } = renderHook(() => useHintTts({ hintText: '断网' }))

    await waitFor(() => {
      expect(result.current.error).toBe('network')
    })
  })

  it('revokes the object URL and pauses audio on unmount', async () => {
    fetchSpy.mockResolvedValueOnce(mockOk())

    const { unmount } = renderHook(() => useHintTts({ hintText: '收尾' }))
    await waitFor(() => expect(playSpy).toHaveBeenCalled())

    act(() => unmount())

    expect(pauseSpy).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
  })
})
