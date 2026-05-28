/**
 * CharacterRadar — 6-axis radar chart for the Character Engine L1 vector.
 *
 * Hand-rolled SVG (same approach as NeonChart) so we control the cyber
 * glow filter and don't bend recharts to a hexagonal shape. Six axes in
 * VECTOR_DIMENSIONS order — aggression at the top, then clockwise to
 * empathy / control / honesty / stability / power_gap.
 *
 * v1 is a static snapshot of the opponent's profile at session start.
 * When L3 Mood Arbiter lands, the same component will receive a moving
 * vector (transitions handled by the `<polygon>` `points` interpolation).
 */

import { useId } from 'react'

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

const VIEW_BOX = 160
const CENTER = 80
const RADIUS = 50
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
  className = '',
}: CharacterRadarProps) {
  const id = useId().replace(/:/g, '')

  const polygonPoints = AXES.map(({ key, angleDeg }) => {
    const [x, y] = pointAt(angleDeg, valueToDist(vector[key as AxisKey]))
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
        const [x, y] = pointAt(angleDeg, valueToDist(vector[key as AxisKey]))
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

      {/* Axis labels. Placed just outside the radius ring so they sit
       * inside the viewBox margin without colliding with the polygon. */}
      {AXES.map(({ key, label, angleDeg }) => {
        const [x, y] = pointAt(angleDeg, RADIUS + 14)
        return (
          <text
            key={key}
            x={x.toFixed(2)}
            y={y.toFixed(2)}
            textAnchor="middle"
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
