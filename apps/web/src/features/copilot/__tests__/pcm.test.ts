/**
 * Unit tests for the PCM conversion + VAD helpers.
 *
 * @vitest-environment node
 */

import { describe, expect, it } from 'vitest'

import { floatTo16BitPCM, rms, SILENCE_RMS_THRESHOLD, SPEECH_RMS_THRESHOLD } from '../pcm'

describe('floatTo16BitPCM', () => {
  it('maps full-scale positive to Int16 max', () => {
    const out = floatTo16BitPCM(new Float32Array([1]))
    expect(out[0]).toBe(0x7fff)
  })

  it('maps full-scale negative to Int16 min', () => {
    const out = floatTo16BitPCM(new Float32Array([-1]))
    expect(out[0]).toBe(-0x8000)
  })

  it('maps silence to zero', () => {
    const out = floatTo16BitPCM(new Float32Array([0, 0, 0]))
    expect(Array.from(out)).toEqual([0, 0, 0])
  })

  it('clamps out-of-range samples instead of wrapping', () => {
    const out = floatTo16BitPCM(new Float32Array([2, -2]))
    expect(out[0]).toBe(0x7fff)
    expect(out[1]).toBe(-0x8000)
  })

  it('preserves frame length', () => {
    const out = floatTo16BitPCM(new Float32Array(128))
    expect(out.length).toBe(128)
  })

  it('produces a little-endian-addressable Int16Array buffer', () => {
    // Int16Array over the platform's native byte order; the .buffer is
    // what we ship on the wire, so assert the byte length matches.
    const out = floatTo16BitPCM(new Float32Array([0.5, -0.5]))
    expect(out.buffer.byteLength).toBe(4)
  })
})

describe('rms', () => {
  it('returns 0 for an empty frame', () => {
    expect(rms(new Float32Array(0))).toBe(0)
  })

  it('returns 0 for pure silence', () => {
    expect(rms(new Float32Array([0, 0, 0, 0]))).toBe(0)
  })

  it('returns the constant amplitude for a DC frame', () => {
    expect(rms(new Float32Array([0.5, 0.5, 0.5]))).toBeCloseTo(0.5, 5)
  })

  it('computes RMS of a symmetric signal', () => {
    // [1, -1, 1, -1] → sqrt(mean(1,1,1,1)) = 1
    expect(rms(new Float32Array([1, -1, 1, -1]))).toBeCloseTo(1, 5)
  })

  it('classifies a quiet frame as below the silence threshold', () => {
    const quiet = new Float32Array(128).fill(0.005)
    expect(rms(quiet)).toBeLessThan(SILENCE_RMS_THRESHOLD)
  })

  it('classifies a loud frame as above the speech threshold', () => {
    const loud = new Float32Array(128).fill(0.2)
    expect(rms(loud)).toBeGreaterThan(SPEECH_RMS_THRESHOLD)
  })

  it('keeps speech threshold above silence threshold (hysteresis)', () => {
    expect(SPEECH_RMS_THRESHOLD).toBeGreaterThan(SILENCE_RMS_THRESHOLD)
  })
})
