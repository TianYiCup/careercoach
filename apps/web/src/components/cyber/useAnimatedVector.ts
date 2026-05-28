/**
 * useAnimatedVector — smoothly interpolates a 6-dim vector toward a
 * target whenever the target changes (Character Engine L9.2).
 *
 * The radar polygon's `points` attribute can't be CSS-transitioned, so
 * we drive the morph in JS: on each target change we ease every dim
 * from its current displayed value to the new one over `durationMs`
 * using requestAnimationFrame.
 *
 * Respects `prefers-reduced-motion`: those users get an effective
 * duration of 0, so the first frame snaps straight to the target with
 * no visible motion (and no synchronous setState in the effect).
 */

import { useEffect, useRef, useState } from 'react'
import type { RadarVector } from './CharacterRadar'

const DIMS = ['aggression', 'empathy', 'control', 'honesty', 'stability', 'power_gap'] as const

/* easeOutCubic — fast start, gentle settle. Reads as the opponent
 * "reacting" then composing themselves, which fits the mood metaphor. */
function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3
}

function lerp(from: number, to: number, t: number): number {
  return from + (to - from) * t
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

export function useAnimatedVector(target: RadarVector, durationMs = 600): RadarVector {
  const [displayed, setDisplayed] = useState<RadarVector>(target)
  // Mirror of the latest painted value, written only inside the rAF
  // tick (never during render) so an interrupted animation eases from
  // where it visually is rather than from the previous target.
  const displayedRef = useRef<RadarVector>(target)

  useEffect(() => {
    const from = displayedRef.current
    const start = performance.now()
    // Reduced motion → duration 0 → first frame lands t=1 (snap). Keeping
    // it on the rAF path avoids a synchronous setState inside the effect.
    const effectiveDuration = prefersReducedMotion() ? 0 : durationMs
    let frame = 0

    const tick = (now: number) => {
      const t = effectiveDuration <= 0 ? 1 : Math.min(1, (now - start) / effectiveDuration)
      const eased = easeOutCubic(t)

      const next = {} as RadarVector
      for (const dim of DIMS) {
        next[dim] = Math.round(lerp(from[dim], target[dim], eased))
      }
      displayedRef.current = next
      setDisplayed(next)

      if (t < 1) {
        frame = requestAnimationFrame(tick)
      }
    }

    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
    // Re-run whenever any dim of the target changes. Spreading the dims
    // (not the object identity) avoids a re-trigger when a parent passes
    // a fresh-but-equal object every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    target.aggression,
    target.empathy,
    target.control,
    target.honesty,
    target.stability,
    target.power_gap,
    durationMs,
  ])

  return displayed
}
