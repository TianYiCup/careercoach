import { clsx } from 'clsx'

/** CoachBubble — 教练 K 吐槽气泡
 *  design-spec §6.2: 带尖角 + 教练表情
 */
interface CoachBubbleProps {
  /** 教练说的话 */
  message: string
  /** 教练表情 emoji */
  expression?: string
  className?: string
}

export function CoachBubble({ message, expression = '😎', className }: CoachBubbleProps) {
  return (
    <div className={clsx('relative glass rounded-radius-md p-4', className)}>
      {/* 左上尖角 */}
      <div className="absolute -left-2 top-4 w-0 h-0 border-t-8 border-t-transparent border-r-8 border-r-white/10 border-b-8 border-b-transparent" />

      {/* 表情 + 消息 */}
      <div className="flex items-start gap-2">
        <span className="text-lg flex-shrink-0" role="img" aria-label="教练 K">{expression}</span>
        <p className="text-sm text-ink-text-2 font-body">{message}</p>
      </div>
    </div>
  )
}
