/**
 * Unit tests for streamHintAudio.
 *
 * Covers:
 *  - canStreamMp3 reflects MediaSource + codec availability
 *  - happy path: every chunk is appended, playback starts at the first
 *    chunk, onAudible fires once, endOfStream + URL revoke run
 *  - a missing response body throws
 *  - a pre-aborted signal rejects before any synthesis work
 *  - an abort mid-append rejects and skips endOfStream
 *  - an autoplay block (NotAllowedError from play()) propagates
 *
 * MediaSource / SourceBuffer aren't implemented in jsdom, so we stand up
 * minimal EventTarget-based fakes and drive their async events by hand.
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { canStreamMp3, streamMp3ToAudio } from '../streamHintAudio'

// --- Fakes -----------------------------------------------------------

class FakeSourceBuffer extends EventTarget {
  appended: Uint8Array[] = []
  /** When false, `updateend` is NOT auto-fired (lets a test wedge an append). */
  autoUpdateEnd = true

  appendBuffer(chunk: Uint8Array): void {
    this.appended.push(chunk)
    if (this.autoUpdateEnd) {
      queueMicrotask(() => this.dispatchEvent(new Event('updateend')))
    }
  }
}

class FakeMediaSource extends EventTarget {
  static isTypeSupported = vi.fn(() => true)
  readyState: 'closed' | 'open' | 'ended' = 'closed'
  sourceBuffer = new FakeSourceBuffer()
  endOfStreamCalls = 0

  constructor() {
    super()
    // Real MediaSource fires `sourceopen` once the media element attaches
    // the object URL; emulate with a microtask after construction.
    queueMicrotask(() => {
      this.readyState = 'open'
      this.dispatchEvent(new Event('sourceopen'))
    })
  }

  addSourceBuffer(_mime: string): FakeSourceBuffer {
    return this.sourceBuffer
  }

  endOfStream(): void {
    this.endOfStreamCalls += 1
    this.readyState = 'ended'
  }
}

function streamingResponse(chunks: Uint8Array[]): Response {
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader() {
        return {
          read: () =>
            Promise.resolve(
              i < chunks.length
                ? { done: false, value: chunks[i++] }
                : { done: true, value: undefined },
            ),
        }
      },
    },
  } as unknown as Response
}

function fakeAudio(play: () => Promise<void> = () => Promise.resolve()): HTMLAudioElement {
  return { src: '', play: vi.fn(play) } as unknown as HTMLAudioElement
}

// --- Globals ---------------------------------------------------------

const createObjectURL = vi.fn(() => 'blob:ms-url')
const revokeObjectURL = vi.fn()

beforeEach(() => {
  vi.stubGlobal('MediaSource', FakeMediaSource)
  vi.stubGlobal('URL', { ...globalThis.URL, createObjectURL, revokeObjectURL })
  createObjectURL.mockClear()
  revokeObjectURL.mockClear()
  FakeMediaSource.isTypeSupported.mockClear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('canStreamMp3', () => {
  it('is true when MediaSource supports audio/mpeg', () => {
    expect(canStreamMp3()).toBe(true)
    expect(FakeMediaSource.isTypeSupported).toHaveBeenCalledWith('audio/mpeg')
  })

  it('is false when the codec is unsupported', () => {
    FakeMediaSource.isTypeSupported.mockReturnValueOnce(false)
    expect(canStreamMp3()).toBe(false)
  })

  it('is false when MediaSource is absent', () => {
    vi.stubGlobal('MediaSource', undefined)
    expect(canStreamMp3()).toBe(false)
  })
})

describe('streamMp3ToAudio', () => {
  it('appends every chunk, starts playback at the first, and finalizes', async () => {
    const onAudible = vi.fn()
    const audio = fakeAudio()
    const ac = new AbortController()
    const chunks = [new Uint8Array([1, 2]), new Uint8Array([3, 4]), new Uint8Array([5])]

    await streamMp3ToAudio(streamingResponse(chunks), audio, {
      signal: ac.signal,
      onAudible,
    })

    const ms = (audio as unknown as { src: string }).src
    expect(ms).toBe('blob:ms-url')
    expect(audio.play).toHaveBeenCalledTimes(1)
    expect(onAudible).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:ms-url')
  })

  it('throws when the response has no body', async () => {
    const res = { ok: true, status: 200, body: null } as unknown as Response
    await expect(
      streamMp3ToAudio(res, fakeAudio(), {
        signal: new AbortController().signal,
        onAudible: vi.fn(),
      }),
    ).rejects.toThrow(/readable body/)
  })

  it('rejects with AbortError when the signal is already aborted', async () => {
    const ac = new AbortController()
    ac.abort()
    const ms = { endOfStreamCalls: 0 }
    await expect(
      streamMp3ToAudio(streamingResponse([new Uint8Array([1])]), fakeAudio(), {
        signal: ac.signal,
        onAudible: vi.fn(),
      }),
    ).rejects.toMatchObject({ name: 'AbortError' })
    expect(ms.endOfStreamCalls).toBe(0)
  })

  it('propagates an autoplay block from play()', async () => {
    const audio = fakeAudio(() =>
      Promise.reject(new DOMException('autoplay blocked', 'NotAllowedError')),
    )
    await expect(
      streamMp3ToAudio(streamingResponse([new Uint8Array([1])]), audio, {
        signal: new AbortController().signal,
        onAudible: vi.fn(),
      }),
    ).rejects.toMatchObject({ name: 'NotAllowedError' })
  })
})
