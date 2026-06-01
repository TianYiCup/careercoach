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

// --- Web Audio fakes -------------------------------------------------

const trackStop = vi.fn()
function makeStream(): MediaStream {
  return {
    getTracks: () => [{ stop: trackStop }],
  } as unknown as MediaStream
}

const getUserMedia = vi.fn<(constraints?: MediaStreamConstraints) => Promise<MediaStream>>()
const addModule = vi.fn<() => Promise<void>>()
const contextClose = vi.fn<() => Promise<void>>()
const contextResume = vi.fn<() => Promise<void>>()
const sourceConnect = vi.fn()
const sourceDisconnect = vi.fn()
const nodeConnect = vi.fn()
const nodeDisconnect = vi.fn()

// Lets a test force the context to start suspended (real Bluetooth /
// post-gesture behavior) and assert we resume it.
let nextContextState: 'running' | 'suspended' = 'running'

// Most-recently-created worklet node, so a test can drive port frames.
let lastNode: { port: { onmessage: ((e: { data: unknown }) => void) | null } } | null = null

class FakeAudioContext {
  sampleRate: number
  state: 'running' | 'suspended'
  destination = { id: 'destination' }
  audioWorklet = { addModule }
  constructor(opts?: { sampleRate?: number }) {
    this.sampleRate = opts?.sampleRate ?? 44100
    this.state = nextContextState
  }
  createMediaStreamSource() {
    return { connect: sourceConnect, disconnect: sourceDisconnect }
  }
  resume() {
    this.state = 'running'
    return contextResume()
  }
  close() {
    return contextClose()
  }
}

class FakeAudioWorkletNode {
  port = { onmessage: null as ((e: { data: unknown }) => void) | null }
  connect = nodeConnect
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
  nextContextState = 'running'
  trackStop.mockClear()
  getUserMedia.mockReset().mockResolvedValue(makeStream())
  addModule.mockReset().mockResolvedValue(undefined)
  contextClose.mockReset().mockResolvedValue(undefined)
  contextResume.mockReset().mockResolvedValue(undefined)
  sourceConnect.mockClear()
  sourceDisconnect.mockClear()
  nodeConnect.mockClear()
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

  it('routes the worklet to the destination so the graph schedules it', async () => {
    // Regression: a worklet connected to nothing is never pulled by the
    // render graph, so process() never runs and zero frames flow.
    const { result } = renderHook(() => useMicCapture({ active: true, onFrame: noopFrame }))
    await waitFor(() => expect(result.current.isCapturing).toBe(true))
    expect(nodeConnect).toHaveBeenCalledTimes(1)
    expect(nodeConnect.mock.calls[0]![0]).toMatchObject({ id: 'destination' })
  })

  it('resumes a context that starts suspended', async () => {
    // Regression: created after `await getUserMedia`, off the gesture
    // stack, the context can start suspended and never run the worklet.
    nextContextState = 'suspended'
    const { result } = renderHook(() => useMicCapture({ active: true, onFrame: noopFrame }))
    await waitFor(() => expect(result.current.isCapturing).toBe(true))
    expect(contextResume).toHaveBeenCalledTimes(1)
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

  // The adaptive VAD's segmentation logic is unit-tested in vad.test.ts;
  // here we only assert the hook wires frame levels → VAD → onUtteranceEnd.
  it('fires onUtteranceEnd after speech then trailing silence', async () => {
    vi.useFakeTimers()
    const onUtteranceEnd = vi.fn()
    const { result } = renderHook(() =>
      useMicCapture({ active: true, onFrame: noopFrame, onUtteranceEnd }),
    )
    await flushStart()
    expect(result.current.isCapturing).toBe(true)

    const buf = () => new ArrayBuffer(8)
    // Floor seeds at an AGC-pumped 0.04 — above the OLD fixed threshold.
    fireFrame(buf(), 0.04)
    // Sustained speech (≥ speechFramesToLatch) so the VAD latches.
    fireFrame(buf(), 0.3)
    fireFrame(buf(), 0.3)
    fireFrame(buf(), 0.3)
    fireFrame(buf(), 0.04) // silence begins
    expect(onUtteranceEnd).not.toHaveBeenCalled()

    vi.advanceTimersByTime(900)
    fireFrame(buf(), 0.04) // past the 500ms hold → boundary
    expect(onUtteranceEnd).toHaveBeenCalledTimes(1)
  })

  it('does not fire onUtteranceEnd on silence without prior speech', async () => {
    vi.useFakeTimers()
    const onUtteranceEnd = vi.fn()
    renderHook(() => useMicCapture({ active: true, onFrame: noopFrame, onUtteranceEnd }))
    await flushStart()

    const buf = () => new ArrayBuffer(8)
    fireFrame(buf(), 0.04)
    vi.advanceTimersByTime(2000)
    fireFrame(buf(), 0.04)
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
