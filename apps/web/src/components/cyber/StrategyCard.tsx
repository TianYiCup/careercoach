/**
 * StrategyCard — coach K's read of the user's just-played turn (L8).
 *
 * Renders "你刚用了【讨好】· 没奏效 · 试试【直球】" so the user sees their
 * own game, not just three things to say next. The strategy + upgrade
 * keys come from the backend's closed set; the Chinese gloss lives here
 * (kept in sync with apps/api/.../coach_strategy.py via the contract).
 *
 * When `upgrade === strategy` the coach is saying "keep doing this", so
 * the card reads 保持 instead of 试试.
 */

import type { CoachEffectKey, CoachStrategyKey, CoachStrategyRead } from '../../api/v1/types'

interface StrategyCardProps {
  read: CoachStrategyRead
  className?: string
}

const STRATEGY_GLOSS: Record<CoachStrategyKey, string> = {
  placate: '讨好',
  concede: '示弱让步',
  avoid: '回避',
  deflect: '转移话题',
  counter: '反问',
  reason: '讲道理',
  direct: '直球',
}

const EFFECT_GLOSS: Record<CoachEffectKey, { label: string; color: string }> = {
  good: { label: '奏效', color: '#6EE7B7' },
  mixed: { label: '部分奏效', color: '#FBBF24' },
  poor: { label: '没奏效', color: '#FF3D81' },
}

export function StrategyCard({ read, className = '' }: StrategyCardProps) {
  const effect = EFFECT_GLOSS[read.effect]
  const isHold = read.upgrade === read.strategy

  return (
    <div className={className}>
      <p className="font-bebas text-[9px] tracking-[0.24em] text-cyber-cyan/80">教练读你的牌</p>
      <p className="mt-1 font-mono text-xs leading-relaxed text-white/85">
        你刚用了
        <span className="mx-1 rounded bg-white/10 px-1.5 py-0.5 font-semibold text-white">
          {STRATEGY_GLOSS[read.strategy]}
        </span>
        <span className="mx-1 font-semibold" style={{ color: effect.color }}>
          · {effect.label} ·
        </span>
        {isHold ? (
          <>
            建议
            <span className="ml-1 rounded bg-cyber-cyan/15 px-1.5 py-0.5 font-semibold text-cyber-cyan">
              保持{STRATEGY_GLOSS[read.upgrade]}
            </span>
          </>
        ) : (
          <>
            试试
            <span className="ml-1 rounded bg-cyber-cyan/15 px-1.5 py-0.5 font-semibold text-cyber-cyan">
              {STRATEGY_GLOSS[read.upgrade]}
            </span>
          </>
        )}
      </p>
    </div>
  )
}
