/**
 * MoodGauge — single-scalar "情绪气压计" for the opponent's live mood
 * (Character Engine L9.2).
 *
 * The 6-dim radar shows *shape*; this gauge collapses it into one
 * 0-100 "how heated is this person right now" reading so the user gets
 * an at-a-glance threat sense without parsing the polygon. Rises when
 * the opponent is hostile (aggression), volatile (low stability), or
 * holds power over the user (power_gap).
 *
 * The bar width + colour animate via CSS transition (width IS
 * transitionable, unlike SVG points), so the gauge slides in step with
 * the radar morph after every L3 mood update.
 */

import type { RadarVector } from './CharacterRadar'
import { moodPressure, pressureBand } from './moodPressure'

interface MoodGaugeProps {
  vector: RadarVector
  className?: string
}

export function MoodGauge({ vector, className = '' }: MoodGaugeProps) {
  const pressure = moodPressure(vector)
  const { label, color } = pressureBand(pressure)

  return (
    <div className={className}>
      <div className="flex items-center justify-between">
        <span className="font-bebas text-[9px] tracking-[0.24em] text-white/60">情绪压力</span>
        <span
          className="font-orbitron text-[10px] font-bold tabular-nums"
          style={{ color }}
          aria-label={`情绪压力 ${pressure} / 100，${label}`}
        >
          {pressure} · {label}
        </span>
      </div>
      <div
        className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-cyber-hairline"
        role="meter"
        aria-valuenow={pressure}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="opponent mood pressure"
      >
        <div
          className="h-full rounded-full transition-[width,background-color] duration-500 ease-out"
          style={{
            width: `${pressure}%`,
            background: color,
            boxShadow: `0 0 8px ${color}`,
          }}
        />
      </div>
    </div>
  )
}
