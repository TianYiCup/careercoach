/**
 * StrategyRadar — an N-axis "playstyle shape" radar for the strategy
 * profile. Hand-rolled SVG (same cyber treatment as CharacterRadar) so
 * it renders deterministically in tests without recharts' size-dependent
 * ResponsiveContainer.
 *
 * Values are normalised to the max in the set, so the busiest strategy
 * reaches the outer ring and the shape reads as "where the user leans".
 * A flat round blob = a balanced player; a spike = a one-trick crutch.
 */

export interface RadarDatum {
  label: string
  value: number
  /** Optional emphasis (e.g. the over-relied crutch) — drawn as a dot. */
  highlight?: boolean
}

interface StrategyRadarProps {
  data: RadarDatum[]
  /** Outer pixel size (height === width). Default 240. */
  size?: number
  color?: string
  className?: string
}

const VIEW_BOX = 200
const CENTER = 100
const RADIUS = 56
const LABEL_RADIUS = RADIUS + 22
const RING_LEVELS = [0.25, 0.5, 0.75, 1] as const

/** θ in math convention (0° = +x, 90° = up); SVG y grows downward. */
function pointAt(angleDeg: number, dist: number): [number, number] {
  const rad = (angleDeg * Math.PI) / 180
  return [CENTER + dist * Math.cos(rad), CENTER - dist * Math.sin(rad)]
}

/** Axis i sits at 12 o'clock + i steps clockwise. */
function axisAngle(i: number, n: number): number {
  return 90 - (360 / n) * i
}

function anchorFor(x: number): 'start' | 'middle' | 'end' {
  if (x < CENTER - 4) {
    return 'end'
  }
  if (x > CENTER + 4) {
    return 'start'
  }
  return 'middle'
}

export function StrategyRadar({
  data,
  size = 240,
  color = '#00F0FF',
  className = '',
}: StrategyRadarProps) {
  const n = data.length
  const max = Math.max(1, ...data.map(d => d.value))

  const ringPath = (level: number): string =>
    data
      .map((_, i) => {
        const [x, y] = pointAt(axisAngle(i, n), RADIUS * level)
        return `${x.toFixed(2)},${y.toFixed(2)}`
      })
      .join(' ')

  const polygonPoints = data
    .map((d, i) => {
      const dist = (d.value / max) * RADIUS
      const [x, y] = pointAt(axisAngle(i, n), dist)
      return `${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')

  return (
    <svg
      viewBox={`0 0 ${VIEW_BOX} ${VIEW_BOX}`}
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="strategy usage radar"
    >
      {/* Grid rings — polygons so the grid follows the axis count. */}
      {RING_LEVELS.map(level => (
        <polygon
          key={level}
          points={ringPath(level)}
          fill="none"
          stroke="rgba(255,255,255,0.08)"
          strokeWidth={0.5}
        />
      ))}

      {/* Spokes */}
      {data.map((d, i) => {
        const [x, y] = pointAt(axisAngle(i, n), RADIUS)
        return (
          <line
            key={`spoke-${d.label}`}
            x1={CENTER}
            y1={CENTER}
            x2={x.toFixed(2)}
            y2={y.toFixed(2)}
            stroke="rgba(255,255,255,0.1)"
            strokeWidth={0.5}
          />
        )
      })}

      {/* Data polygon */}
      <polygon
        points={polygonPoints}
        fill={color}
        fillOpacity={0.18}
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
      />

      {/* Vertex dots — emphasised when highlighted (the crutch). */}
      {data.map((d, i) => {
        const dist = (d.value / max) * RADIUS
        const [x, y] = pointAt(axisAngle(i, n), dist)
        return (
          <circle
            key={`dot-${d.label}`}
            cx={x.toFixed(2)}
            cy={y.toFixed(2)}
            r={d.highlight ? 3 : 1.6}
            fill={d.highlight ? '#FF2DAA' : color}
          />
        )
      })}

      {/* Axis labels */}
      {data.map((d, i) => {
        const [x, y] = pointAt(axisAngle(i, n), LABEL_RADIUS)
        return (
          <text
            key={`label-${d.label}`}
            x={x.toFixed(2)}
            y={y.toFixed(2)}
            textAnchor={anchorFor(x)}
            dominantBaseline="middle"
            fontSize={8}
            fill={d.highlight ? '#FF2DAA' : 'rgba(255,255,255,0.6)'}
            className="font-mono"
          >
            {d.label}
          </text>
        )
      })}
    </svg>
  )
}
