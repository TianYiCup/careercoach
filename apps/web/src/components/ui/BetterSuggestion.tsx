/**
 * BetterSuggestion — 失分弹出更佳话术组件
 *
 * PRD §3.3 US-C2 L1: 点击失分句 → 弹出「失分原因 (≤50字) + 更佳话术 (≤80字)」
 * PRD §3.3 US-C2 L5: 更佳话术支持一键复制
 *
 * 两种展示模式：
 *   inline: 嵌入在 turn card 下方展开（默认）
 *   modal:  桌面端右侧滑出 / 未来可做 popup
 */

import { useState } from 'react'
import { GlassCard } from './GlassCard'

export interface BetterSuggestionProps {
  /** 失分原因，≤50 字 */
  reason: string
  /** 更佳话术建议，≤80 字 */
  better: string
}

export function BetterSuggestion({ reason, better }: BetterSuggestionProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(better)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback: no clipboard API in some contexts
      setCopied(false)
    }
  }

  return (
    <GlassCard className="space-y-2 border-vivid-purple/20">
      <p className="text-xs font-body text-vivid-purple">
        💡 K 说：{reason}
      </p>
      <div className="flex items-start gap-2">
        <p className="flex-1 text-xs text-ink-text font-body">
          更佳话术：「{better}」
        </p>
        <button
          type="button"
          onClick={handleCopy}
          className="flex-shrink-0 px-2 py-0.5 rounded-radius-pill text-[10px] font-body border transition-colors"
          style={{
            color: copied ? 'var(--color-vivid-green)' : 'var(--color-vivid-purple)',
            borderColor: copied ? 'var(--color-vivid-green/40)' : 'var(--color-vivid-purple/30)',
            background: copied ? 'rgba(96,255,200,0.1)' : 'transparent',
          }}
        >
          {copied ? '已复制' : '复制'}
        </button>
      </div>
    </GlassCard>
  )
}
