/**
 * MagneticButton — cursor-pulled neon button.
 *
 * The button "leans" toward the cursor when the mouse is within ~120px
 * of its centre, then snaps back on leave. Combined with a permanent
 * neon glow + gradient border, this is the prompt §Interactions
 * "magnetic hover" CTA pattern.
 *
 * Use sparingly — at most one or two magnetic actions per viewport, or
 * the cursor flits between them and the effect stops feeling premium.
 */

import { useRef, type ButtonHTMLAttributes, type MouseEvent, type ReactNode } from 'react'

type Variant = 'cyan' | 'magenta' | 'lime'

interface MagneticButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode
  variant?: Variant
  /** Magnet pull strength (0..1). Default 0.3 — gentle. */
  strength?: number
}

const VARIANT_STYLES: Record<Variant, { glow: string; ring: string; text: string }> = {
  cyan: {
    glow: 'shadow-[0_0_32px_rgba(0,240,255,0.45),0_0_72px_rgba(0,240,255,0.2)]',
    ring: 'from-cyber-cyan to-cyber-blue',
    text: 'text-white',
  },
  magenta: {
    glow: 'shadow-[0_0_32px_rgba(255,45,170,0.45),0_0_72px_rgba(255,45,170,0.2)]',
    ring: 'from-cyber-magenta to-vivid-purple',
    text: 'text-white',
  },
  lime: {
    glow: 'shadow-[0_0_32px_rgba(183,255,0,0.45)]',
    ring: 'from-cyber-lime to-cyber-cyan',
    text: 'text-cyber-void',
  },
}

export function MagneticButton({
  children,
  variant = 'cyan',
  strength = 0.3,
  className = '',
  ...rest
}: MagneticButtonProps) {
  const ref = useRef<HTMLButtonElement>(null)
  const styles = VARIANT_STYLES[variant]

  function onMouseMove(e: MouseEvent<HTMLButtonElement>) {
    const el = ref.current
    if (!el) {
      return
    }
    const rect = el.getBoundingClientRect()
    const cx = rect.left + rect.width / 2
    const cy = rect.top + rect.height / 2
    el.style.setProperty('--mx', `${(e.clientX - cx) * strength}px`)
    el.style.setProperty('--my', `${(e.clientY - cy) * strength}px`)
  }

  function onMouseLeave() {
    const el = ref.current
    if (!el) {
      return
    }
    el.style.setProperty('--mx', '0px')
    el.style.setProperty('--my', '0px')
  }

  return (
    <button
      ref={ref}
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
      className={`group relative inline-flex items-center gap-2 rounded-pill bg-gradient-to-r p-[1.5px] transition-transform duration-200 ${styles.ring} ${styles.glow} ${className}`.trim()}
      style={{
        transform: 'translate(var(--mx, 0), var(--my, 0))',
      }}
      {...rest}
    >
      <span
        className={`relative inline-flex items-center justify-center gap-2 rounded-pill bg-cyber-void px-7 py-3 font-orbitron text-sm font-semibold uppercase tracking-[0.18em] ${styles.text} transition-colors group-hover:bg-cyber-deep`}
      >
        {children}
      </span>
    </button>
  )
}
