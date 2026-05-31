/**
 * Unit tests for useHintTts's streaming branch.
 *
 * The buffered (blob) path is covered in useHintTts.test.tsx, which runs
 * under jsdom where MediaSource is absent. Here we mock `streamHintAudio`
 * so `canStreamMp3` reports true and `streamMp3ToAudio` is controllable,
 * exercising the hook's branch selection and error mapping without a real
 * MediaSource:
 *  - a streamable response uses streamMp3ToAudio, not res.blob()
 *  - an autoplay block surfaces as 'autoplay_blocked'
 *  - a decode / MediaSource fault surfaces as 'tts_unavailable'
 *  - an abort leaves the error state clean
 *  - a body-less response falls back to the buffered blob path
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

import { useHintTts } from '../useHintTts'

vi.mock('../../../api/v1/auth-token', () => ({
  getAuthToken: () => 'tok_test',
}))

const canStreamMp3 = vi.fn(() => true)
const streamMp3ToAudio = vi.fn<() => Promise<void>>()
vi.mock('../streamHintAudio', () => ({
  canStreamMp3: () => canStreamMp3(),
  streamMp3ToAudio: (...args: unknown[]) => streamMp3ToAudio(...(args as [])),
}))

const playSpy = vi.fn(() => Promise.resolve())
const pauseSpy = vi.fn()
const createObjectURL = vi.fn(() => 'blob:mock-url')
const revokeObjectURL = vi.fn()

beforeEach(() => {
  canStreamMp3.mockReturnValue(true)
  streamMp3ToAudio.mockReset()
  streamMp3ToAudio.mockResolvedValue(undefined)
  playSpy.mockClear()
  pauseSpy.mockClear()
  HTMLMediaElement.prototype.play = playSpy as unknown as HTMLMediaElement['play']
  HTMLMediaElement.prototype.pause = pauseSpy as unknown as HTMLMediaElement['pause']
  vi.stubGlobal('URL', { ...globalThis.URL, createObjectURL, revokeObjectURL })
})

const fetchSpy = vi.fn<typeof fetch>()
vi.stubGlobal('fetch', fetchSpy)

afterEach(() => {
  fetchSpy.mockReset()
})

/** A streamable Response: ok + a truthy body, blob() guarded so a wrong
 * branch selection is loud. */
function streamableResponse(): Response {
  return {
    ok: true,
    status: 200,
    body: {} as ReadableStream,
    blob: () => {
      throw new Error('blob() called on the streaming path')
    },
  } as unknown as Response
}

/** A Response with no readable body — forces the buffered fallback even
 * when MSE is available. (A real `new Response(blob)` exposes a truthy
 * `.body` stream in this runtime, so we hand-roll the null-body shape.) */
function bodylessResponse(): Response {
  return {
    ok: true,
    status: 200,
    body: null,
    blob: () => Promise.resolve(new Blob(['x'], { type: 'audio/mpeg' })),
  } as unknown as Response
}

describe('useHintTts streaming branch', () => {
  it('streams via streamMp3ToAudio instead of buffering the blob', async () => {
    fetchSpy.mockResolvedValueOnce(streamableResponse())

    renderHook(() => useHintTts({ hintText: '先反问预算' }))

    await waitFor(() => expect(streamMp3ToAudio).toHaveBeenCalledTimes(1))
    const args = streamMp3ToAudio.mock.calls[0] as unknown as [
      Response,
      HTMLAudioElement,
      { signal: AbortSignal; onAudible: () => void },
    ]
    expect(args[2].signal).toBeInstanceOf(AbortSignal)
    expect(typeof args[2].onAudible).toBe('function')
  })

  it('maps an autoplay block to autoplay_blocked', async () => {
    fetchSpy.mockResolvedValueOnce(streamableResponse())
    streamMp3ToAudio.mockRejectedValueOnce(new DOMException('blocked', 'NotAllowedError'))

    const { result } = renderHook(() => useHintTts({ hintText: '试试看' }))

    await waitFor(() => expect(result.current.error).toBe('autoplay_blocked'))
    expect(result.current.isSpeaking).toBe(false)
  })

  it('maps a decode / MediaSource fault to tts_unavailable', async () => {
    fetchSpy.mockResolvedValueOnce(streamableResponse())
    streamMp3ToAudio.mockRejectedValueOnce(new Error('SourceBuffer append failed'))

    const { result } = renderHook(() => useHintTts({ hintText: '解码炸了' }))

    await waitFor(() => expect(result.current.error).toBe('tts_unavailable'))
  })

  it('stays clean when the stream is aborted', async () => {
    fetchSpy.mockResolvedValueOnce(streamableResponse())
    streamMp3ToAudio.mockRejectedValueOnce(new DOMException('aborted', 'AbortError'))

    const { result } = renderHook(() => useHintTts({ hintText: '被打断' }))

    // Give the rejected promise a tick to settle.
    await new Promise((r) => setTimeout(r, 0))
    expect(result.current.error).toBeNull()
  })

  it('falls back to the buffered blob path when the response has no body', async () => {
    fetchSpy.mockResolvedValueOnce(bodylessResponse())

    renderHook(() => useHintTts({ hintText: '没有body' }))

    await waitFor(() => expect(playSpy).toHaveBeenCalledTimes(1))
    expect(streamMp3ToAudio).not.toHaveBeenCalled()
  })

  it('falls back to buffered when MSE is unavailable', async () => {
    canStreamMp3.mockReturnValue(false)
    fetchSpy.mockResolvedValueOnce(bodylessResponse())

    renderHook(() => useHintTts({ hintText: '不支持MSE' }))

    await waitFor(() => expect(playSpy).toHaveBeenCalledTimes(1))
    expect(streamMp3ToAudio).not.toHaveBeenCalled()
  })
})
