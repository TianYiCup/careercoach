/**
 * Tests for useAnimatedVector (L9.2).
 *
 * rAF + performance.now are stubbed so we can drive the easing loop
 * deterministically and assert convergence + the reduced-motion snap.
 *
 * @vitest-environment jsdom
 */

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAnimatedVector } from '../useAnimatedVector'
import type { RadarVector } from '../CharacterRadar'

const A: RadarVector = {
  aggression: 20,
  empathy: 20,
  control: 20,
  honesty: 20,
  stability: 20,
  power_gap: 20,
}
const B: RadarVector = {
  aggression: 80,
  empathy: 80,
  control: 80,
  honesty: 80,
  stability: 80,
  power_gap: 80,
}

// Manual rAF pump: callbacks queue here keyed by id so cancellation
// actually removes them (mirrors the browser). `flush(now)` invokes the
// pending callbacks with a controlled timestamp so we step the easing
// loop frame by frame.
let rafQueue = new Map<number, FrameRequestCallback>()
let rafId = 0
let nowValue = 0

function flush(now: number) {
  nowValue = now
  const callbacks = [...rafQueue.values()]
  rafQueue = new Map()
  for (const cb of callbacks) cb(now)
}

beforeEach(() => {
  rafQueue = new Map()
  rafId = 0
  nowValue = 0
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    rafId += 1
    rafQueue.set(rafId, cb)
    return rafId
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => {
    rafQueue.delete(id)
  })
  vi.spyOn(performance, 'now').mockImplementation(() => nowValue)
  // Default: motion allowed.
  vi.stubGlobal('matchMedia', () => ({ matches: false }) as MediaQueryList)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useAnimatedVector', () => {
  it('initialises to the target', () => {
    const { result } = renderHook(() => useAnimatedVector(A))
    expect(result.current).toEqual(A)
  })

  it('eases toward a new target and converges by the end of the duration', () => {
    const { result, rerender } = renderHook(({ v }) => useAnimatedVector(v, 600), {
      initialProps: { v: A },
    })

    // Change the target — schedules the first frame.
    act(() => {
      rerender({ v: B })
    })

    // Midway: should be between A and B, not yet at B.
    act(() => {
      flush(300)
    })
    const mid = result.current.aggression
    expect(mid).toBeGreaterThan(A.aggression)
    expect(mid).toBeLessThan(B.aggression)

    // End of duration: snaps to exactly the target.
    act(() => {
      flush(600)
    })
    expect(result.current).toEqual(B)
  })

  it('snaps to target on the first frame when prefers-reduced-motion is set', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }) as MediaQueryList)

    const { result, rerender } = renderHook(({ v }) => useAnimatedVector(v), {
      initialProps: { v: A },
    })

    act(() => {
      rerender({ v: B })
    })

    // Reduced motion → effective duration 0 → the very first frame lands
    // t=1, so a single flush snaps straight to B with no intermediate
    // values (no visible motion), and no further frames are scheduled.
    act(() => {
      flush(1)
    })
    expect(result.current).toEqual(B)
    expect(rafQueue.size).toBe(0)
  })

  it('eases from the current displayed value when interrupted mid-flight', () => {
    const { result, rerender } = renderHook(({ v }) => useAnimatedVector(v, 600), {
      initialProps: { v: A },
    })

    act(() => {
      rerender({ v: B })
    })
    act(() => {
      flush(300) // partway to B
    })
    const interruptedAt = result.current.aggression
    expect(interruptedAt).toBeLessThan(B.aggression)

    // New target mid-flight — should ease from interruptedAt, not from A.
    // The interrupt animation starts at the current clock (300), so it
    // converges one full duration later, at 900.
    const C: RadarVector = { ...A, aggression: 20 }
    act(() => {
      rerender({ v: C })
    })
    act(() => {
      flush(900)
    })
    // Converges to C exactly.
    expect(result.current.aggression).toBe(20)
  })
})
