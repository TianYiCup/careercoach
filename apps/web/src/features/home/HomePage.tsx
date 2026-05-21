/**
 * 首页 — design-spec §9.2
 * 顶栏：问候 + VibePill + StreakFire
 * 导航：沙盘对练 / 实战副驾 / 复盘师 / Wrapped / 弱点画像
 */

import { useEffect } from 'react'
import { BlobBackground, GlassCard, MascotReaction, VibePill, StreakFire, StickerBadge } from '../../components'
import { useAuth } from '../auth'
import { useStreak } from './useStreak'
import { useVibe } from './useVibe'
import type { UiVibeType } from './useVibe'

type Page = 'home' | 'sandbox' | 'copilot' | 'wrapped' | 'score' | 'reviewUpload' | 'reviewResult' | 'weakness'

export function HomePage({
  onNavigate,
}: {
  onNavigate: (page: Page) => void
}) {
  const { user, logout } = useAuth()
  const isMinor = user?.is_minor ?? false
  const { state: streakState, refetch: refetchStreak } = useStreak()
  const { state: vibeState, setVibe } = useVibe()
  const allVibes: UiVibeType[] = ['燃爆', '想躺平', '莫名烦', '雄心勃勃', '佛系']

  // Fetch streak on mount if not loaded
  useEffect(() => {
    if (streakState.currentDays === 0 && !streakState.isLoading && !streakState.error) {
      void refetchStreak()
    }
  }, [streakState.currentDays, streakState.isLoading, streakState.error, refetchStreak])

  return (
    <div className="relative min-h-screen flex flex-col items-center px-4 py-12 overflow-hidden">
      <BlobBackground />

      {/* Top-right: nickname + 退出 */}
      {user && (
        <div className="relative z-10 self-end flex items-center gap-3 text-xs font-body animate-fade-in">
          <span className="text-ink-text-2">{user.nickname}</span>
          <button type="button" onClick={logout} className="text-ink-text-3 hover:text-ink-text-2 transition-colors">退出</button>
        </div>
      )}

      <div className="relative z-10 text-center max-w-lg mt-8 animate-slide-up" style={{ animationDelay: '0ms' }}>
        <MascotReaction expression="confident" size="lg" showLabel />
        <h1 className="mt-6 text-5xl md:text-6xl font-display italic tracking-tight text-gradient-vivid">
          CareerCoach AI
        </h1>
        <p className="mt-2 text-xl md:text-2xl text-ink-text-2 font-body">
          不教你说违心话，只教你说真话还能赢。
        </p>
        <div className="mt-4 flex items-center justify-center gap-2">
          {allVibes.map(v => (
            <VibePill
              key={v}
              vibe={v}
              onClick={() => setVibe(v)}
              className={vibeState.activeVibe === v ? 'ring-2 ring-vivid-purple/60' : ''}
            />
          ))}
        </div>
        <div className="mt-3 flex justify-center">
          <StreakFire days={streakState.currentDays} />
        </div>
      </div>

      <div className="relative z-10 mt-12 w-full max-w-3xl space-y-6 animate-slide-up" style={{ animationDelay: '150ms' }}>
        <GlassCard className="text-center">
          <p className="text-sm text-ink-text-2 mb-3">选择体验</p>
          <div className="flex flex-wrap items-center justify-center gap-3">
            <button
              type="button"
              onClick={() => onNavigate('sandbox')}
              className="px-6 py-3 rounded-radius-md bg-vivid-purple/20 border border-vivid-purple/40 text-vivid-purple font-body font-medium hover:bg-vivid-purple/30 transition-colors"
            >
              🥊 沙盘对练
            </button>
            {/* PRD §1.5: minors cannot use copilot */}
            {isMinor ? (
              <span className="px-6 py-3 rounded-radius-md bg-ink-card/40 border border-ink-line text-ink-text-3 font-body font-medium cursor-not-allowed" title="未成年人暂不开放此功能">
                🎧 实战副驾（未开放）
              </span>
            ) : (
              <button
                type="button"
                onClick={() => onNavigate('copilot')}
                className="px-6 py-3 rounded-radius-md gradient-vivid text-white font-body font-medium hover:scale-105 transition-transform shadow-[0_0_20px_rgba(124,58,237,0.3)]"
              >
                🎧 实战副驾
              </button>
            )}
            <button
              type="button"
              onClick={() => onNavigate('reviewUpload' as Page)}
              className="px-6 py-3 rounded-radius-md bg-vivid-green/20 border border-vivid-green/40 text-vivid-green font-body font-medium hover:bg-vivid-green/30 transition-colors"
            >
              🔍 复盘师
            </button>
            <button
              type="button"
              onClick={() => onNavigate('wrapped')}
              className="px-6 py-3 rounded-radius-md bg-vivid-green/20 border border-vivid-green/40 text-vivid-green font-body font-medium hover:bg-vivid-green/30 transition-colors"
            >
              📊 Wrapped 战报
            </button>
            <button
              type="button"
              onClick={() => onNavigate('weakness')}
              className="px-6 py-3 rounded-radius-md bg-vivid-orange/20 border border-vivid-orange/40 text-vivid-orange font-body font-medium hover:bg-vivid-orange/30 transition-colors"
            >
              🔥 弱点画像
            </button>
          </div>
        </GlassCard>
        <GlassCard glow className="text-center">
          <div className="flex flex-wrap items-center justify-center gap-2">
            <StickerBadge variant="green">✨ 封神</StickerBadge>
            <StickerBadge variant="orange">💥 翻车</StickerBadge>
            <StickerBadge variant="cyan">🐶 稳如老狗</StickerBadge>
            <StickerBadge variant="yellow">🤡 整活儿</StickerBadge>
          </div>
        </GlassCard>
      </div>
    </div>
  )
}
