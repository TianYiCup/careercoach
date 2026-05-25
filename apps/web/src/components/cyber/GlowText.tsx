/**
 * GlowText — display headline with neon glow + outlined-stroke variants.
 *
 * Three render variants:
 *   · `fill`     — solid colour fill with glow (default)
 *   · `stroke`   — transparent fill, neon stroked outline (the "FUTURE"
 *                   transparent-outlined word from the prompt §Hero)
 *   · `gradient` — cyber gradient text fill (animated shift)
 *
 * Combine layered instances to get the prompt's "layered typography":
 *   <GlowText variant="stroke">FUTURE</GlowText>
 *   <GlowText variant="gradient">FUTURE</GlowText>   ← overlaid slightly offset
 */

import type { ReactNode } from 'react'

type Variant = 'fill' | 'stroke' | 'gradient'

interface GlowTextProps {
  children: ReactNode
  variant?: Variant
  /** Glow colour for fill / stroke variants. */
  color?: string
  className?: string
  /** Render as h1/h2/span etc. Defaults to span. */
  as?: 'h1' | 'h2' | 'h3' | 'span' | 'p' | 'div'
}

export function GlowText({
  children,
  variant = 'fill',
  color = '#00F0FF',
  className = '',
  as: Tag = 'span',
}: GlowTextProps) {
  if (variant === 'gradient') {
    return (
      <Tag className={`text-gradient-cyber ${className}`.trim()}>
        {children}
      </Tag>
    )
  }

  if (variant === 'stroke') {
    return (
      <Tag
        className={`inline-block ${className}`.trim()}
        style={{
          WebkitTextStroke: `1px ${color}`,
          color: 'transparent',
          textShadow: `0 0 16px ${color}66`,
        }}
      >
        {children}
      </Tag>
    )
  }

  // fill
  return (
    <Tag
      className={`inline-block ${className}`.trim()}
      style={{
        color,
        textShadow: `0 0 12px ${color}88, 0 0 32px ${color}44`,
      }}
    >
      {children}
    </Tag>
  )
}
