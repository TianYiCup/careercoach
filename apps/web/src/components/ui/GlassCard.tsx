import { clsx } from 'clsx'
import type { CSSProperties, ReactNode } from 'react'

/** GlassCard — 毛玻璃卡片（Glassmorphism）
 *  design-spec §6.2: backdrop-filter blur(24px) + 发光描边
 *  小程序降级：rgba 半透明 + 描边
 */
interface GlassCardProps {
  children: ReactNode
  className?: string
  /** 开启紫色发光边框 */
  glow?: boolean
  style?: CSSProperties
}

export function GlassCard({ children, className, glow = false, style }: GlassCardProps) {
  return (
    <div
      className={clsx('glass rounded-radius-md p-4', glow && 'glow-purple', className)}
      style={style}
    >
      {children}
    </div>
  )
}
