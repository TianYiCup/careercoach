import { clsx } from 'clsx'
import type { CSSProperties, ReactNode } from 'react'

/** StickerBadge — 贴纸式徽章
 *  design-spec §6.3: 描边 + 微旋转 ±5° + 偏移阴影(实体感)
 */
interface StickerBadgeProps {
  children: ReactNode
  className?: string
  /** 强调色，默认 vivid-purple */
  variant?: 'purple' | 'green' | 'orange' | 'pink' | 'cyan' | 'yellow'
  style?: CSSProperties
}

const VARIANT_BG: Record<NonNullable<StickerBadgeProps['variant']>, string> = {
  purple: 'bg-vivid-purple text-white',
  green: 'bg-vivid-green text-ink-bg',
  orange: 'bg-vivid-orange text-white',
  pink: 'bg-vivid-pink text-white',
  cyan: 'bg-vivid-cyan text-ink-bg',
  yellow: 'bg-vivid-yellow text-ink-bg',
}

export function StickerBadge({
  children,
  className,
  variant = 'purple',
  style,
}: StickerBadgeProps) {
  // Random rotation between -5° and 5° for sticker feel
  const rotation = ((Math.random() - 0.5) * 10).toFixed(1)

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 px-3 py-1 rounded-radius-pill',
        'border border-black/60 text-sm font-bold whitespace-nowrap',
        'drop-shadow-[2px_3px_0_rgba(0,0,0,0.6)]',
        VARIANT_BG[variant],
        className,
      )}
      style={{
        transform: `rotate(${rotation}deg)`,
        ...style,
      }}
    >
      {children}
    </span>
  )
}
