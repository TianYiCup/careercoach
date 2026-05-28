/**
 * moodPressure — collapse the 6-dim mood vector into a single 0-100
 * "情绪气压" scalar for the MoodGauge (Character Engine L9.2).
 *
 * Lives in its own module (not MoodGauge.tsx) so the component file
 * only exports a component — keeps Vite fast-refresh happy and lets
 * tests import the math without rendering.
 */

import type { RadarVector } from './CharacterRadar'

/* Weighted blend of the three dims that read as "pressure on the user":
 *   aggression    — overt hostility (primary signal)
 *   100-stability — volatility, how close to losing it
 *   power_gap     — stakes; an上位者 raising their voice lands harder
 * Empathy / control / honesty shape *how* they push, not *how hard*, so
 * they stay out of the scalar. */
export function moodPressure(vector: RadarVector): number {
  const clamp = (v: number) => Math.max(0, Math.min(100, v))
  const aggression = clamp(vector.aggression)
  const volatility = 100 - clamp(vector.stability)
  const power = clamp(vector.power_gap)
  return Math.round(0.45 * aggression + 0.3 * volatility + 0.25 * power)
}

export interface PressureBand {
  label: string
  color: string
}

/* Four legible bands. The label gives a colour-blind-safe word next to
 * the colour (CLAUDE.md anti-pattern #3: never colour-only). */
export function pressureBand(pressure: number): PressureBand {
  if (pressure < 30) {
    return { label: '平静', color: '#00F0FF' } // cyan
  }
  if (pressure < 55) {
    return { label: '紧张', color: '#6EE7B7' } // teal-green
  }
  if (pressure < 75) {
    return { label: '紧绷', color: '#FBBF24' } // amber
  }
  return { label: '爆发边缘', color: '#FF3D81' } // magenta
}
