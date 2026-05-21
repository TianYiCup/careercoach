/**
 * 弱点画像页 — design-spec §9.7
 * 迁移到 PR #131 WeaknessProfileResponse schema:
 *   - weaknesses: { tag, frequency, last_seen }[] (no percentage/remark)
 *   - recommended_scenarios: ScenarioSummary[] (no reason)
 */

import { useEffect } from 'react'
import { GlassCard, BlobBackground, MascotReaction, StickerBadge } from '../../components'
import { useWeaknesses } from './useWeaknesses'

/** Progress bar color based on frequency rank */
function barColor(rank: number, total: number): string {
  const ratio = total > 1 ? rank / (total - 1) : 1
  if (ratio <= 0.25) return 'bg-vivid-cyan'
  if (ratio <= 0.5) return 'bg-vivid-yellow'
  return 'bg-vivid-orange'
}

/** Format ISO date to compact display */
function formatDate(iso: string): string {
  return iso.slice(5).replace('-', '/')
}

export function WeaknessProfilePage({ onBack }: { onBack: () => void }) {
  const { state, refetch } = useWeaknesses()
  const { data: profile, isLoading, error } = state

  // Fetch on mount if no data yet — uses refetch callback (async setState)
  // which does not trigger react-hooks/set-state-in-effect
  useEffect(() => {
    if (!profile && !isLoading && !error) {
      void refetch()
    }
  }, [profile, isLoading, error, refetch])

  const weaknesses = profile?.weaknesses ?? []
  const topWeakness = weaknesses[0]
  const totalFreq = weaknesses.reduce((s, w) => s + w.frequency, 0)

  // Dynamic mascot: many weaknesses = caring, few = confident
  const mascotExpression = weaknesses.length >= 3 ? 'caring' : 'confident'

  if (isLoading) {
    return (
      <div className="relative min-h-screen flex items-center justify-center px-4">
        <BlobBackground />
        <p className="relative z-10 text-ink-text-2 font-body animate-pulse">分析中...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="relative min-h-screen flex items-center justify-center px-4">
        <BlobBackground />
        <div className="relative z-10 text-center space-y-4">
          <p className="text-ink-text-2 font-body">{error}</p>
          <button type="button" onClick={onBack} className="text-vivid-purple text-sm hover:underline">
            返回
          </button>
        </div>
      </div>
    )
  }

  if (!profile || weaknesses.length === 0) {
    return (
      <div className="relative min-h-screen flex items-center justify-center px-4">
        <BlobBackground />
        <div className="relative z-10 text-center space-y-4">
          <MascotReaction expression="confident" size="lg" showLabel />
          <p className="text-ink-text font-body">还没有弱点数据</p>
          <p className="text-sm text-ink-text-2 font-body">完成几次对练后 K 才能给你写画像</p>
          <button type="button" onClick={onBack} className="text-vivid-purple text-sm hover:underline">
            去对练
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="relative min-h-screen flex flex-col items-center px-4 py-8 overflow-y-auto">
      <BlobBackground />

      <div className="relative z-10 w-full max-w-md space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <button type="button" onClick={onBack} className="text-ink-text-2 text-sm hover:text-ink-text transition-colors">
            &larr; 返回
          </button>
        </div>

        {/* K intro */}
        <div className="flex items-start gap-4">
          <MascotReaction expression={mascotExpression} size="md" />
          <div className="flex-1">
            <p className="text-sm text-ink-text-2 font-body">教练 K 说：</p>
            <p className="text-base text-ink-text font-body mt-1">
              扒了 {totalFreq} 次复盘
            </p>
            <p className="text-sm text-ink-text-3 font-body">发现了这些……</p>
          </div>
        </div>

        {/* Top weakness — hero GlassCard */}
        <GlassCard glow className="space-y-3 text-center">
          <p className="text-xs text-ink-text-3 font-body">你最常翻车的瞬间</p>
          <p className="text-5xl font-display italic text-gradient-vivid">
            {topWeakness!.frequency}
          </p>
          <p className="text-lg text-ink-text font-body font-medium">
            {topWeakness!.tag}
          </p>
          <p className="text-sm text-ink-text-3 font-body">
            最近出现 {formatDate(topWeakness!.last_seen)}
          </p>
        </GlassCard>

        {/* Secondary weaknesses — ranked list */}
        {weaknesses.length > 1 && (
          <div className="space-y-3">
            {weaknesses.slice(1).map((w, idx) => (
              <div key={w.tag} className="flex items-center gap-3 px-1">
                <span className="text-xs text-ink-text-3 font-mono w-6">#{idx + 2}</span>
                <div className="flex-1">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm text-ink-text font-body">{w.tag}</span>
                    <span className="text-xs text-ink-text-3 font-body">{w.frequency} 次</span>
                  </div>
                  <div className="h-2 rounded-full bg-ink-card overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${barColor(idx, weaknesses.length - 1)}`}
                      style={{ width: `${(w.frequency / (topWeakness?.frequency ?? 1)) * 100}%` }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* K's recommendations */}
        {profile.recommended_scenarios.length > 0 && (
          <GlassCard className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-base" role="img" aria-label="训练">💪</span>
              <p className="text-sm text-ink-text font-body font-medium">K 给你开小灶</p>
            </div>
            <div className="flex gap-3 overflow-x-auto pb-2 -mx-1 px-1">
              {profile.recommended_scenarios.map(sc => (
                <button
                  key={sc.id}
                  type="button"
                  onClick={() => onBack()}
                  className="flex-shrink-0 w-40 p-3 rounded-radius-md bg-ink-card/60 border border-ink-line text-left hover:border-vivid-purple transition-colors"
                >
                  <p className="text-sm text-ink-text font-body font-medium truncate">{sc.title}</p>
                  <p className="text-xs text-ink-text-3 font-body mt-1 line-clamp-2">{sc.background}</p>
                </button>
              ))}
            </div>
          </GlassCard>
        )}

        {/* Quick stats row */}
        <div className="flex items-center justify-center gap-3">
          <StickerBadge variant="orange">{weaknesses.length} 个弱点</StickerBadge>
          <StickerBadge variant="cyan">{totalFreq} 次出现</StickerBadge>
        </div>
      </div>
    </div>
  )
}
