import { clsx } from 'clsx'
import type { CSSProperties, ReactNode } from 'react'

/** StickerBadge — 贴纸式徽章
 *  design-spec §6.3: 描边 + 微旋转 ±5° + 偏移阴影(实体感)
 *
 *  旋转角度通过 `rotation` prop 传入（保证渲染纯函数），
 *  不传则无旋转。调用方可用 useMemo + Math.random() 在组件外层
 *  生成随机的稳定角度。
 */
interface StickerBadgeProps {
  children: ReactNode
  className?: string
  /** 强调色，默认 vivid-purple */
  variant?: 'purple' | 'green' | 'orange' | 'pink' | 'cyan' | 'yellow'
  /** 旋转角度（度），建议 -5 ~ 5 */
  rotation?: number
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

/** 每种 variant 有一个固定的微旋转角度，避免 Math.random() */
const DEFAULT_ROTATION: Record<NonNullable<StickerBadgeProps['variant']>, number> = {
  purple: -2.3,
  green: 3.1,
  orange: -4.0,
  pink: 1.8,
  cyan: -3.5,
  yellow: 2.7,
}

export function StickerBadge({
  children,
  className,
  variant = 'purple',
  rotation,
  style,
}: StickerBadgeProps) {
  const rot = rotation ?? DEFAULT_ROTATION[variant]

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
        transform: `rotate(${rot}deg)`,
        ...style,
      }}
    >
      {children}
    </span>
  )
}
