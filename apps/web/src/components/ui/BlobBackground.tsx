import { clsx } from 'clsx'
import type { CSSProperties } from 'react'

/** BlobBackground — 漂浮渐变 blob 装饰层
 *  design-spec §4.6: 每个核心页面 1-2 个漂浮 Blob，增强氛围
 */
interface BlobBackgroundProps {
  className?: string
  style?: CSSProperties
}

export function BlobBackground({ className, style }: BlobBackgroundProps) {
  return (
    <div className={clsx('pointer-events-none absolute inset-0 overflow-hidden', className)} style={style}>
      <div
        className="absolute w-80 h-80 rounded-[50%_40%_60%_50%/50%_60%_40%_50%] opacity-35 blur-[80px] animate-[float_8s_ease-in-out_infinite]"
        style={{
          background: '#6C4DFF',
          top: '-10%',
          left: '-10%',
        }}
      />
      <div
        className="absolute w-80 h-80 rounded-[50%_40%_60%_50%/50%_60%_40%_50%] opacity-35 blur-[80px] animate-[float_8s_ease-in-out_infinite_2s]"
        style={{
          background: '#FF7AB6',
          bottom: '-10%',
          right: '-10%',
        }}
      />
    </div>
  )
}
