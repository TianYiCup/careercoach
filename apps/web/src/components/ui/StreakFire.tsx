import { clsx } from 'clsx'

/** StreakFire — 连胜火焰图标 + 数字
 *  design-spec §6.2 / §9.2:
 *    数字 > 7 时火焰加大 + 微抖动
 */
interface StreakFireProps {
  /** 连胜天数 */
  days: number
  className?: string
}

export function StreakFire({ days, className }: StreakFireProps) {
  const isLong = days > 7

  return (
    <div className={clsx('flex items-center gap-2', className)}>
      <span
        className={clsx(
          isLong ? 'text-2xl' : 'text-lg',
          isLong && 'animate-[shake_0.3s_ease-in-out_infinite]',
        )}
        role="img"
        aria-label="连胜火焰"
      >
        🔥
      </span>
      <span className={clsx(
        'font-body font-bold',
        isLong ? 'text-tone-aggro' : 'text-ink-text-2',
        isLong ? 'text-lg' : 'text-sm',
      )}>
        已经卷了 {days} 天
      </span>
    </div>
  )
}
