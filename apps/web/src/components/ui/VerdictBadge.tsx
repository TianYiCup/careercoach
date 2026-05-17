/**
 * VerdictBadge — 三色标记组件
 *
 * 用于复盘逐句分析 + 沙盘评分页结果标签。
 * 色盲友好：图标 + 文字 + 颜色三重编码（PRD §3.3 US-C2 L5）。
 *
 * 评分语义色：
 *   win     = ✨ 封神 (green)  — 加分句
 *   neutral = 🌀 路过 (cyan)   — 中性句
 *   lose    = 💥 翻车 (orange) — 失分句
 */

import type { ReviewVerdict } from '../../api/v1/types'

export interface VerdictBadgeProps {
  verdict: ReviewVerdict
  /** Show icon + label (default: true); set false for compact mode */
  showLabel?: boolean
  /** Size variant */
  size?: 'sm' | 'md'
}

const VERDICT_STYLES: Record<ReviewVerdict, { icon: string; label: string; bg: string; text: string; border: string }> = {
  win: {
    icon: '✨',
    label: '封神',
    bg: 'bg-vivid-green/15',
    text: 'text-vivid-green',
    border: 'border-vivid-green/40',
  },
  neutral: {
    icon: '🌀',
    label: '路过',
    bg: 'bg-ink-text-3/10',
    text: 'text-ink-text-3',
    border: 'border-ink-text-3/20',
  },
  lose: {
    icon: '💥',
    label: '翻车',
    bg: 'bg-vivid-orange/15',
    text: 'text-vivid-orange',
    border: 'border-vivid-orange/40',
  },
}

export function VerdictBadge({ verdict, showLabel = true, size = 'sm' }: VerdictBadgeProps) {
  const s = VERDICT_STYLES[verdict]
  const sizeClass = size === 'md' ? 'px-3 py-1 text-xs' : 'px-2 py-0.5 text-[10px]'

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-radius-pill border font-body font-medium ${s.bg} ${s.text} ${s.border} ${sizeClass}`}
    >
      <span aria-hidden="true">{s.icon}</span>
      {showLabel && <span>{s.label}</span>}
    </span>
  )
}
