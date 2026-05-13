import { clsx } from 'clsx'

/** VibePill — 当前情绪标签
 *  design-spec §6.2 / §9.2: 首页顶栏情绪切换
 */
export type VibeType = '燃爆' | '想躺平' | '莫名烦' | '雄心勃勃' | '佛系'

interface VibePillProps {
  vibe: VibeType
  /** 点击回调 */
  onClick?: () => void
  className?: string
}

const VIBE_CONFIG: Record<VibeType, { emoji: string; color: string }> = {
  '燃爆': { emoji: '🔥', color: 'border-tone-aggro text-tone-aggro' },
  '想躺平': { emoji: '💤', color: 'border-ink-text-3 text-ink-text-3' },
  '莫名烦': { emoji: '😤', color: 'border-vivid-pink text-vivid-pink' },
  '雄心勃勃': { emoji: '💪', color: 'border-vivid-purple text-vivid-purple' },
  '佛系': { emoji: '🧘', color: 'border-tone-safe text-tone-safe' },
}

export function VibePill({ vibe, onClick, className }: VibePillProps) {
  const cfg = VIBE_CONFIG[vibe]
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'inline-flex items-center gap-1 px-3 py-1 rounded-radius-pill',
        'border text-sm font-body font-medium',
        'bg-white/5 hover:bg-white/10 transition-colors',
        cfg.color,
        className,
      )}
    >
      <span role="img" aria-label={vibe}>{cfg.emoji}</span>
      <span>{vibe}</span>
    </button>
  )
}
