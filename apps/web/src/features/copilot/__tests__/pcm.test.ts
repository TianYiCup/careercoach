/**
 * Unit tests for the PCM conversion + VAD helpers.
 *
 * @vitest-environment node
 */

import { describe, expect, it } from 'vitest'

import { floatTo16BitPCM, resampleLinear, rms } from '../pcm'

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

describe('resampleLinear', () => {
  it('returns a copy unchanged when rates are equal', () => {
    const input = new Float32Array([0.1, 0.2, 0.3])
    const out = resampleLinear(input, 16000, 16000)
    expect(out.length).toBe(3)
    expect(out[0]).toBeCloseTo(0.1, 5)
    expect(out[1]).toBeCloseTo(0.2, 5)
    expect(out[2]).toBeCloseTo(0.3, 5)
    expect(out).not.toBe(input)
  })

  it('returns empty for empty input', () => {
    expect(resampleLinear(new Float32Array(0), 48000, 16000).length).toBe(0)
  })

  it('decimates 48k→16k by ~3x', () => {
    // 7 native samples at 48k → ceil((7-1)/3)+1 = 3 output samples
    const input = new Float32Array([0, 1, 2, 3, 4, 5, 6])
    const out = resampleLinear(input, 48000, 16000)
    expect(out.length).toBe(3)
    // positions 0, 3, 6 → exact samples (integer step)
    expect(out[0]).toBeCloseTo(0, 5)
    expect(out[1]).toBeCloseTo(3, 5)
    expect(out[2]).toBeCloseTo(6, 5)
  })

  it('linearly interpolates on a non-integer ratio (44.1k→16k)', () => {
    const input = new Float32Array([0, 10, 20, 30])
    const out = resampleLinear(input, 44100, 16000)
    // step = 2.75625; pos1 = 2.75625 → 20 + 0.75625*(30-20) ≈ 27.56
    expect(out[0]).toBeCloseTo(0, 5)
    expect(out[1]).toBeCloseTo(27.5625, 3)
  })

  it('halves the length when downsampling by 2x (32k→16k)', () => {
    const input = new Float32Array(Array.from({ length: 9 }, (_, i) => i))
    const out = resampleLinear(input, 32000, 16000)
    // ceil((9-1)/2)+1 = 5
    expect(out.length).toBe(5)
    expect(Array.from(out)).toEqual([0, 2, 4, 6, 8])
  })

  it('preserves DC silence as silence', () => {
    const out = resampleLinear(new Float32Array(480).fill(0), 48000, 16000)
    expect(out.every((s) => s === 0)).toBe(true)
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

  it('is higher for a loud frame than a quiet one', () => {
    const quiet = new Float32Array(128).fill(0.005)
    const loud = new Float32Array(128).fill(0.2)
    expect(rms(loud)).toBeGreaterThan(rms(quiet))
  })
})
