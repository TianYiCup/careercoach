/**
 * TiltCard — 3D mouse-tracking tilt + reactive sheen for any card.
 *
 * Pointer position drives a clamped 3D rotateX / rotateY on the card,
 * plus a translucent radial sheen that follows the cursor. The combined
 * effect is the "premium hover" pattern used on Awwwards-tier sites.
 *
 * Implementation choices:
 *   · CSS transforms only — framer-motion is overkill here and adds
 *     re-render pressure when many cards live on one page.
 *   · We listen on the card element (not document) so there's no
 *     leak risk on unmount.
 *   · Tilt range is ±8° — sharper than that and the content starts
 *     looking warped at large card sizes.
 */

import { useRef, type MouseEvent, type ReactNode } from 'react'

interface TiltCardProps {
  children: ReactNode
  /** Max tilt angle in degrees. Default 8°. */
  maxTiltDeg?: number
  /** Sheen highlight colour. */
  sheenColor?: string
  className?: string
}

export function TiltCard({
  children,
  maxTiltDeg = 8,
  sheenColor = 'rgba(0, 240, 255, 0.18)',
  className = '',
}: TiltCardProps) {
  const ref = useRef<HTMLDivElement>(null)

  function onMouseMove(e: MouseEvent<HTMLDivElement>) {
    const el = ref.current
    if (!el) {
      return
    }
    const rect = el.getBoundingClientRect()
    const x = (e.clientX - rect.left) / rect.width // 0..1
    const y = (e.clientY - rect.top) / rect.height // 0..1
    const tiltX = (0.5 - y) * 2 * maxTiltDeg
    const tiltY = (x - 0.5) * 2 * maxTiltDeg
    el.style.setProperty('--tilt-x', `${tiltX}deg`)
    el.style.setProperty('--tilt-y', `${tiltY}deg`)
    el.style.setProperty('--sheen-x', `${x * 100}%`)
    el.style.setProperty('--sheen-y', `${y * 100}%`)
    el.style.setProperty('--sheen-opacity', '1')
  }

  function onMouseLeave() {
    const el = ref.current
    if (!el) {
      return
    }
    el.style.setProperty('--tilt-x', '0deg')
    el.style.setProperty('--tilt-y', '0deg')
    el.style.setProperty('--sheen-opacity', '0')
  }

  return (
    <div
      ref={ref}
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
      className={`relative overflow-hidden transition-transform duration-200 ease-out ${className}`.trim()}
      style={
        {
          transform:
            'perspective(1200px) rotateX(var(--tilt-x, 0deg)) rotateY(var(--tilt-y, 0deg))',
          '--sheen-x': '50%',
          '--sheen-y': '50%',
          '--sheen-opacity': '0',
        } as React.CSSProperties
      }
    >
      {children}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 transition-opacity duration-300"
        style={{
          background: `radial-gradient(320px circle at var(--sheen-x) var(--sheen-y), ${sheenColor} 0%, transparent 65%)`,
          opacity: 'var(--sheen-opacity)',
        }}
      />
    </div>
  )
}
