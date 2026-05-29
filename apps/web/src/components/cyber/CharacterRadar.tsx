/**
 * CharacterRadar — 6-axis radar chart for the Character Engine vector.
 *
 * Hand-rolled SVG (same approach as NeonChart) so we control the cyber
 * glow filter and don't bend recharts to a hexagonal shape. Six axes in
 * VECTOR_DIMENSIONS order — aggression at the top, then clockwise to
 * empathy / control / honesty / stability / power_gap.
 *
 * L9.2: when `animate` is on (default) the polygon morphs smoothly from
 * its previous shape to the new `vector` via `useAnimatedVector` — the
 * L3 mood arbiter moves the vector every turn, and the morph makes that
 * change legible instead of a jarring snap. `prefers-reduced-motion`
 * falls back to a snap inside the hook.
 */

import { useId } from 'react'
import { useAnimatedVector } from './useAnimatedVector'

export interface RadarVector {
  aggression: number
  empathy: number
  control: number
  honesty: number
  stability: number
  power_gap: number
}

interface CharacterRadarProps {
  vector: RadarVector
  /** Outer pixel size (height === width). Default 200. */
  size?: number
  /** Stroke + fill colour. */
  color?: string
  /** Morph between vectors on change. Default true. Set false for a
   * static one-shot render (e.g. a snapshot in a share card). */
  animate?: boolean
  className?: string
}

/* Axis configuration. The order here is the on-screen clockwise order
 * starting from 12 o'clock. Matches VECTOR_DIMENSIONS in the API. */
const AXES = [
  { key: 'aggression', label: '攻击', angleDeg: 90 },
  { key: 'empathy', label: '共情', angleDeg: 30 },
  { key: 'control', label: '控制', angleDeg: -30 },
  { key: 'honesty', label: '诚实', angleDeg: -90 },
  { key: 'stability', label: '稳定', angleDeg: -150 },
  { key: 'power_gap', label: '权力', angleDeg: 150 },
] as const

type AxisKey = (typeof AXES)[number]['key']

// The viewBox carries a generous margin around the RADIUS ring so the
// axis labels sit fully outside the data polygon (max radius === RADIUS)
// and never get clipped at the edge. Polygon spans CENTER ± RADIUS
// (40..140); labels live in the margin band beyond that.
const VIEW_BOX = 180
const CENTER = 90
const RADIUS = 50
// Labels are pushed this far past the outer ring — comfortably clear of
// the polygon vertices + their glow so the cyan shape never covers text.
const LABEL_RADIUS = RADIUS + 20
const RING_LEVELS = [0.25, 0.5, 0.75, 1] as const

/* Math → SVG point. θ is measured in standard math convention (0° = +x,
 * 90° = up), but SVG y goes down, so the y component is subtracted. */
function pointAt(angleDeg: number, dist: number): [number, number] {
  const rad = (angleDeg * Math.PI) / 180
  return [CENTER + dist * Math.cos(rad), CENTER - dist * Math.sin(rad)]
}

function valueToDist(value: number): number {
  // Clamp into [0, 100] so a future bad payload doesn't punch through the chart.
  const clamped = Math.max(0, Math.min(100, value))
  return (clamped / 100) * RADIUS
}

function ringPath(level: number): string {
  const points = AXES.map(({ angleDeg }) => {
    const [x, y] = pointAt(angleDeg, RADIUS * level)
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })
  return points.join(' ')
}

export function CharacterRadar({
  vector,
  size = 200,
  color = '#00F0FF',
  animate = true,
  className = '',
}: CharacterRadarProps) {
  const id = useId().replace(/:/g, '')
  // Hook is called unconditionally (rules of hooks); when `animate` is
  // off we discard its output and render the raw target directly.
  const animated = useAnimatedVector(vector)
  const shown = animate ? animated : vector

  const polygonPoints = AXES.map(({ key, angleDeg }) => {
    const [x, y] = pointAt(angleDeg, valueToDist(shown[key as AxisKey]))
    return `${x.toFixed(2)},${y.toFixed(2)}`
  }).join(' ')

  return (
    <svg
      viewBox={`0 0 ${VIEW_BOX} ${VIEW_BOX}`}
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="opponent character vector radar"
    >
      <defs>
        <filter id={`radar-glow-${id}`} x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="2" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* Concentric grid rings. Drawn as polygons so the hex grid follows
       * the data shape, not a circular grid that would imply continuous
       * dimensions. */}
      {RING_LEVELS.map((level) => (
        <polygon
          key={level}
          points={ringPath(level)}
          fill="none"
          stroke="rgba(255,255,255,0.08)"
          strokeWidth={0.5}
        />
      ))}

      {/* Spokes — one per axis. */}
      {AXES.map(({ key, angleDeg }) => {
        const [x, y] = pointAt(angleDeg, RADIUS)
        return (
          <line
            key={key}
            x1={CENTER}
            y1={CENTER}
            x2={x.toFixed(2)}
            y2={y.toFixed(2)}
            stroke="rgba(255,255,255,0.1)"
            strokeWidth={0.5}
          />
        )
      })}

      {/* Data polygon — the persona shape. */}
      <polygon
        points={polygonPoints}
        fill={color}
        fillOpacity={0.18}
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        filter={`url(#radar-glow-${id})`}
      />

      {/* Value dots at each vertex — pops the high dims visually. */}
      {AXES.map(({ key, angleDeg }) => {
        const [x, y] = pointAt(angleDeg, valueToDist(shown[key as AxisKey]))
        return (
          <circle
            key={key}
            cx={x.toFixed(2)}
            cy={y.toFixed(2)}
            r={2}
            fill={color}
            filter={`url(#radar-glow-${id})`}
          />
        )
      })}

      {/* Axis labels. Placed in the viewBox margin beyond the outer ring,
       * and anchored *away* from the chart per side (right axes start at
       * their point and run right, left axes end at their point and run
       * left) so the text extends outward into the margin instead of back
       * over the polygon. */}
      {AXES.map(({ key, label, angleDeg }) => {
        const [x, y] = pointAt(angleDeg, LABEL_RADIUS)
        const cos = Math.cos((angleDeg * Math.PI) / 180)
        const textAnchor = cos > 0.3 ? 'start' : cos < -0.3 ? 'end' : 'middle'
        return (
          <text
            key={key}
            x={x.toFixed(2)}
            y={y.toFixed(2)}
            textAnchor={textAnchor}
            dominantBaseline="middle"
            className="fill-white/70"
            style={{ fontSize: 9, letterSpacing: '0.1em', fontFamily: 'inherit' }}
          >
            {label}
          </text>
        )
      })}
    </svg>
  )
}
