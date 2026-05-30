/**
 * Unit tests for useMicCapture.
 *
 * Web Audio has no jsdom shim, so getUserMedia, AudioContext, and
 * AudioWorkletNode are all stubbed. The pure conversion the worklet
 * delegates to is covered separately in pcm.test.ts; here we assert the
 * hook's orchestration: acquisition, error classification, frame
 * forwarding, trailing-silence VAD, and teardown.
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

import { useMicCapture } from '../useMicCapture'
import { SILENCE_RMS_THRESHOLD, SPEECH_RMS_THRESHOLD } from '../pcm'

// --- Web Audio fakes -------------------------------------------------

const trackStop = vi.fn()
function makeStream(): MediaStream {
  return {
    getTracks: () => [{ stop: trackStop }],
  } as unknown as MediaStream
}

const getUserMedia = vi.fn<() => Promise<MediaStream>>()
const addModule = vi.fn<() => Promise<void>>()
const contextClose = vi.fn<() => Promise<void>>()
const sourceConnect = vi.fn()
const sourceDisconnect = vi.fn()
const nodeDisconnect = vi.fn()

// Most-recently-created worklet node, so a test can drive port frames.
let lastNode: { port: { onmessage: ((e: { data: unknown }) => void) | null } } | null = null

class FakeAudioContext {
  sampleRate: number
  audioWorklet = { addModule }
  constructor(opts?: { sampleRate?: number }) {
    this.sampleRate = opts?.sampleRate ?? 44100
  }
  createMediaStreamSource() {
    return { connect: sourceConnect, disconnect: sourceDisconnect }
  }
  close() {
    return contextClose()
  }
}

class FakeAudioWorkletNode {
  port = { onmessage: null as ((e: { data: unknown }) => void) | null }
  disconnect = nodeDisconnect
  constructor() {
    // eslint-disable-next-line @typescript-eslint/no-this-alias -- test double captures its own instance so a test can drive port frames
    lastNode = this
  }
}

function fireFrame(pcm: ArrayBuffer, level: number) {
  act(() => {
    lastNode?.port.onmessage?.({ data: { pcm, level } })
  })
}

beforeEach(() => {
  vi.useRealTimers()
  trackStop.mockClear()
  getUserMedia.mockReset().mockResolvedValue(makeStream())
  addModule.mockReset().mockResolvedValue(undefined)
  contextClose.mockReset().mockResolvedValue(undefined)
  sourceConnect.mockClear()
  sourceDisconnect.mockClear()
  nodeDisconnect.mockClear()
  lastNode = null

  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
  vi.stubGlobal('AudioContext', FakeAudioContext)
  vi.stubGlobal('AudioWorkletNode', FakeAudioWorkletNode)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const noopFrame = () => {}

/** Flush the promise chain in start() while fake timers are active. */
async function flushStart() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('useMicCapture', () => {
  it('does not touch the mic when inactive', async () => {
    renderHook(() => useMicCapture({ active: false, onFrame: noopFrame }))
    await new Promise((r) => setTimeout(r, 0))
    expect(getUserMedia).not.toHaveBeenCalled()
  })

  it('acquires a mono mic and goes live when active', async () => {
    const { result } = renderHook(() => useMicCapture({ active: true, onFrame: noopFrame }))

    await waitFor(() => expect(result.current.isCapturing).toBe(true))
    expect(getUserMedia).toHaveBeenCalledTimes(1)
    const constraints = getUserMedia.mock.calls[0]![0] as MediaStreamConstraints
    expect((constraints.audio as MediaTrackConstraints).channelCount).toBe(1)
    expect(sourceConnect).toHaveBeenCalledTimes(1)
    expect(result.current.error).toBeNull()
  })

  it('reports unsupported when mediaDevices is missing', async () => {
    vi.stubGlobal('navigator', {})
    const onError = vi.fn()
    const { result } = renderHook(() =>
      useMicCapture({ active: true, onFrame: noopFrame, onError }),
    )
    await waitFor(() => expect(result.current.error).toBe('unsupported'))
    expect(onError).toHaveBeenCalledWith('unsupported')
    expect(result.current.isCapturing).toBe(false)
  })

  it('reports insecure_context outside https', async () => {
    vi.stubGlobal('isSecureContext', false)
    const { result } = renderHook(() => useMicCapture({ active: true, onFrame: noopFrame }))
    await waitFor(() => expect(result.current.error).toBe('insecure_context'))
    expect(getUserMedia).not.toHaveBeenCalled()
  })

  it('classifies a denied permission', async () => {
    getUserMedia.mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'))
    const onError = vi.fn()
    const { result } = renderHook(() =>
      useMicCapture({ active: true, onFrame: noopFrame, onError }),
    )
    await waitFor(() => expect(result.current.error).toBe('permission_denied'))
    expect(onError).toHaveBeenCalledWith('permission_denied')
  })

  it('classifies a missing device', async () => {
    getUserMedia.mockRejectedValueOnce(new DOMException('none', 'NotFoundError'))
    const { result } = renderHook(() => useMicCapture({ active: true, onFrame: noopFrame }))
    await waitFor(() => expect(result.current.error).toBe('no_device'))
  })

  it('forwards each PCM frame and tracks the level', async () => {
    const onFrame = vi.fn()
    const { result } = renderHook(() => useMicCapture({ active: true, onFrame }))
    await waitFor(() => expect(result.current.isCapturing).toBe(true))

    const buf = new ArrayBuffer(3200)
    fireFrame(buf, 0.3)

    expect(onFrame).toHaveBeenCalledWith(buf)
    await waitFor(() => expect(result.current.level).toBeCloseTo(0.3, 5))
  })

  it('fires onUtteranceEnd after trailing silence following speech', async () => {
    vi.useFakeTimers()
    const onUtteranceEnd = vi.fn()
    const { result } = renderHook(() =>
      useMicCapture({ active: true, onFrame: noopFrame, onUtteranceEnd }),
    )
    // Flush the async start() under fake timers.
    await flushStart()
    expect(result.current.isCapturing).toBe(true)

    const buf = () => new ArrayBuffer(8)
    const speech = SPEECH_RMS_THRESHOLD + 0.05
    const silence = SILENCE_RMS_THRESHOLD - 0.005

    // Speech, then silence frames straddling the 800ms hold window.
    fireFrame(buf(), speech)
    fireFrame(buf(), silence) // marks silence start
    expect(onUtteranceEnd).not.toHaveBeenCalled()

    vi.advanceTimersByTime(900)
    fireFrame(buf(), silence) // now past the hold → boundary
    expect(onUtteranceEnd).toHaveBeenCalledTimes(1)
  })

  it('does not fire onUtteranceEnd on silence without prior speech', async () => {
    vi.useFakeTimers()
    const onUtteranceEnd = vi.fn()
    renderHook(() => useMicCapture({ active: true, onFrame: noopFrame, onUtteranceEnd }))
    await flushStart()

    const buf = () => new ArrayBuffer(8)
    fireFrame(buf(), SILENCE_RMS_THRESHOLD - 0.005)
    vi.advanceTimersByTime(2000)
    fireFrame(buf(), SILENCE_RMS_THRESHOLD - 0.005)
    expect(onUtteranceEnd).not.toHaveBeenCalled()
  })

  it('stops tracks and closes the context on teardown', async () => {
    const { result, rerender } = renderHook(
      ({ active }) => useMicCapture({ active, onFrame: noopFrame }),
      { initialProps: { active: true } },
    )
    await waitFor(() => expect(result.current.isCapturing).toBe(true))

    rerender({ active: false })

    expect(trackStop).toHaveBeenCalled()
    expect(nodeDisconnect).toHaveBeenCalled()
    expect(sourceDisconnect).toHaveBeenCalled()
    expect(contextClose).toHaveBeenCalled()
    expect(result.current.isCapturing).toBe(false)
  })
})
