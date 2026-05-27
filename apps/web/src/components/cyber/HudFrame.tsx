/**
 * HudFrame — cyberpunk corner-bracket frame around any content.
 *
 * Draws four 12px L-shaped corner brackets in cyan / magenta over the
 * content. Pairs with the `cyber-glass-edge` surface utility on the
 * inner element to produce the "AI operating system" HUD look the
 * redesign prompt §Visual Details calls for.
 *
 * Optional `label` renders a small uppercase Bebas Neue tag in the top-
 * left bracket region — used for "AI CORE / NEURAL / SYSTEM" markers.
 *
 * The brackets are pure SVG so they scale crisply on hover transforms
 * (which TiltCard applies). They are positioned absolutely; the wrapper
 * stays `position: relative` so consumers can size it freely.
 */

import type { ReactNode } from 'react'

interface HudFrameProps {
  children: ReactNode
  /** Top-left HUD label (e.g. "AI CORE", "NEURAL", "01"). */
  label?: string
  /** Tag rendered top-right (e.g. session id, frame number). */
  tag?: string
  /** Corner stroke colour. */
  color?: string
  /** Padding of the inner content area. */
  className?: string
}

function CornerBracket({
  position,
  color,
}: {
  position: 'tl' | 'tr' | 'bl' | 'br'
  color: string
}) {
  // Each bracket is a 24×24 SVG. The `path` for top-left looks like ┌.
  // We rotate per position so a single path covers all four corners.
  const rotation = { tl: 0, tr: 90, br: 180, bl: 270 }[position]
  const posStyle: Record<typeof position, string> = {
    tl: '-top-px -left-px',
    tr: '-top-px -right-px',
    bl: '-bottom-px -left-px',
    br: '-bottom-px -right-px',
  }

  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      className={`absolute ${posStyle[position]} pointer-events-none`}
      style={{ transform: `rotate(${rotation}deg)` }}
      aria-hidden="true"
    >
      <path
        d="M1 8 L1 1 L8 1"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function HudFrame({
  children,
  label,
  tag,
  color = 'rgba(0, 240, 255, 0.85)',
  className = '',
}: HudFrameProps) {
  return (
    <div className={`relative ${className}`.trim()}>
      <CornerBracket position="tl" color={color} />
      <CornerBracket position="tr" color={color} />
      <CornerBracket position="bl" color={color} />
      <CornerBracket position="br" color={color} />

      {label && (
        <span
          className="font-bebas absolute top-2 left-4 text-[11px] tracking-[0.25em] text-cyber-cyan animate-hud-flicker"
          style={{ textShadow: '0 0 8px rgba(0, 240, 255, 0.6)' }}
        >
          {label}
        </span>
      )}

      {tag && (
        <span className="font-mono absolute top-2 right-4 text-[10px] tracking-wider text-cyber-cyan/60">
          {tag}
        </span>
      )}

      {children}
    </div>
  )
}
