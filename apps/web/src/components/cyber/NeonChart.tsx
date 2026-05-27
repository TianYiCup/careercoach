/**
 * NeonChart — minimal neon line chart for the analytics surface.
 *
 * Hand-rolled SVG (no recharts) so we can fully control the glow filter
 * — the prompt §Dashboard UI calls for "neon curves, soft glow, smooth
 * motion, minimal noise" which recharts can't deliver without heavy
 * styling overrides. The component takes a list of points (0..1
 * normalised) and renders a Catmull-Rom-ish smooth path with a glow
 * filter.
 */

import { useId } from 'react'

interface NeonChartProps {
  /** Y values, normalised 0..1. */
  data: number[]
  /** Stroke colour. */
  color?: string
  /** Stroke width in px. */
  strokeWidth?: number
  /** Height in px. Width is 100%. */
  height?: number
  /** Render the area under the curve as a soft fill. */
  fill?: boolean
  className?: string
}

/* Convert a list of normalised values into a smooth SVG cubic path.
 * Uses a simple "midpoint cubic" — sufficient for marketing visuals,
 * no kink artefacts at typical chart densities (8–30 points). */
function smoothPath(data: number[], width: number, height: number): string {
  if (data.length === 0) {
    return ''
  }
  const stepX = width / Math.max(1, data.length - 1)
  const toY = (v: number) => height - v * height
  const pts = data.map((v, i) => [i * stepX, toY(v)] as const)
  let d = `M ${pts[0]![0]} ${pts[0]![1]}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i]!
    const p1 = pts[i]!
    const p2 = pts[i + 1]!
    const p3 = pts[i + 2] ?? p2
    // Catmull-Rom → cubic Bezier conversion (tension = 0.5).
    const cp1x = p1[0] + (p2[0] - p0[0]) / 6
    const cp1y = p1[1] + (p2[1] - p0[1]) / 6
    const cp2x = p2[0] - (p3[0] - p1[0]) / 6
    const cp2y = p2[1] - (p3[1] - p1[1]) / 6
    d += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p2[0]} ${p2[1]}`
  }
  return d
}

export function NeonChart({
  data,
  color = '#00F0FF',
  strokeWidth = 2,
  height = 80,
  fill = true,
  className = '',
}: NeonChartProps) {
  const width = 320
  const id = useId().replace(/:/g, '')
  const path = smoothPath(data, width, height)
  const areaPath = data.length > 0 ? `${path} L ${width} ${height} L 0 ${height} Z` : ''

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={`w-full ${className}`.trim()}
      style={{ height }}
      aria-hidden="true"
    >
      <defs>
        <filter id={`glow-${id}`} x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <linearGradient id={`area-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>

      {fill && <path d={areaPath} fill={`url(#area-${id})`} />}
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        filter={`url(#glow-${id})`}
      />
    </svg>
  )
}
