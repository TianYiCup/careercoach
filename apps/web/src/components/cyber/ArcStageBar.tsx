/**
 * ArcStageBar — four-segment dramatic-arc indicator (Character Engine L2).
 *
 * Shows where the conversation sits: 开场 → 冲突 → 转折 → 收尾. The L2
 * arc director emits a stage each turn; this bar highlights the active
 * segment so the user feels the dialogue building and resolving rather
 * than reading a flat wall of back-and-forth.
 *
 * Stages are ordered, but the arc can jump (a turning beat may fire at
 * turn 4 or turn 12) and `turning` is a momentary beat — so we highlight
 * the active segment by identity, not by "fill up to here". Segments
 * before the active one stay dimly lit to read as "already passed".
 */

import type { ArcStage } from '../../api/v1/types'

interface ArcStageBarProps {
  stage: ArcStage
  className?: string
}

const STAGES: { key: ArcStage; label: string; color: string }[] = [
  { key: 'opening', label: '开场', color: '#00F0FF' },
  { key: 'conflict', label: '冲突', color: '#FBBF24' },
  { key: 'turning', label: '转折', color: '#A78BFA' },
  { key: 'closing', label: '收尾', color: '#6EE7B7' },
]

const ORDER: ArcStage[] = ['opening', 'conflict', 'turning', 'closing']

export function ArcStageBar({ stage, className = '' }: ArcStageBarProps) {
  const activeIndex = ORDER.indexOf(stage)

  return (
    <div className={className}>
      <div className="flex items-center justify-between">
        <span className="font-bebas text-[9px] tracking-[0.24em] text-white/60">剧情节奏</span>
      </div>
      <div className="mt-1 flex gap-1" role="list" aria-label={`剧情阶段：${labelFor(stage)}`}>
        {STAGES.map(({ key, label, color }, i) => {
          const isActive = key === stage
          const isPast = i < activeIndex
          return (
            <div key={key} className="flex flex-1 flex-col items-center gap-0.5" role="listitem">
              <div
                className="h-1 w-full rounded-full transition-[background-color,box-shadow,opacity] duration-500"
                style={{
                  background: isActive || isPast ? color : 'rgba(255,255,255,0.12)',
                  opacity: isActive ? 1 : isPast ? 0.45 : 1,
                  boxShadow: isActive ? `0 0 8px ${color}` : 'none',
                }}
              />
              <span
                className="font-bebas text-[8px] tracking-[0.16em] transition-colors duration-500"
                style={{ color: isActive ? color : 'rgba(255,255,255,0.4)' }}
              >
                {label}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function labelFor(stage: ArcStage): string {
  return STAGES.find((s) => s.key === stage)?.label ?? stage
}
