/**
 * StrategyProfilePage — the user's communication-strategy profile (L5).
 *
 * Consumes GET /v1/users/me/profile: per-strategy usage + win rate, the
 * total-observations experience signal, and the over-relied "crutch"
 * the opponent's intensity is built to punish.
 *
 * Cyberpunk shell mirrors WeaknessProfilePage: NeuralParticles bg, K
 * intro, a hero card for the crutch, then a win-rate bar list. Empty
 * until the user's first coached turn.
 */

import { useEffect } from 'react'
import { motion } from 'framer-motion'
import { ArrowLeft, Crosshair, Swords } from 'lucide-react'

import { MascotReaction, StickerBadge } from '../../components'
import { GlowText, HudFrame, MagneticButton, NeuralParticles } from '../../components/cyber'
import type { CoachStrategyKey, StrategyStatItem } from '../../api/v1/types'
import { StrategyRadar, type RadarDatum } from './StrategyRadar'
import { useStrategyProfile } from './useStrategyProfile'

const STRATEGY_GLOSS: Record<CoachStrategyKey, string> = {
  placate: '讨好',
  concede: '示弱让步',
  avoid: '回避',
  deflect: '转移话题',
  counter: '反问',
  reason: '讲道理',
  direct: '直球',
}

function pct(rate: number): number {
  return Math.round(rate * 100)
}

export function StrategyProfilePage({ onBack }: { onBack: () => void }) {
  const { state, refetch } = useStrategyProfile()
  const { data: profile, isLoading, error } = state

  useEffect(() => {
    if (!profile && !isLoading && !error) {
      void refetch()
    }
  }, [profile, isLoading, error, refetch])

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-cyber-void text-white">
      <NeuralParticles count={1000} />
      <div className="pointer-events-none fixed inset-0 -z-10 tech-grid opacity-20" />
      <div className="pointer-events-none fixed inset-0 -z-10 scanline" />
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 -z-10"
        style={{
          background:
            'radial-gradient(ellipse at center, transparent 0%, rgba(5,5,5,0.4) 50%, rgba(5,5,5,0.92) 100%)',
        }}
      />

      <div className="relative z-10 mx-auto flex w-full max-w-3xl flex-1 flex-col px-6 py-8">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex w-fit items-center gap-1 font-bebas text-xs tracking-[0.24em] text-white/60 transition-colors hover:text-cyber-cyan"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          BACK
        </button>

        {isLoading && (
          <div className="mt-24 flex flex-col items-center gap-4">
            <MascotReaction expression="thinking" size="md" />
            <p className="font-mono text-sm text-white/60 animate-pulse">
              ANALYZING · 策略画像生成中…
            </p>
          </div>
        )}

        {error && (
          <div className="mt-24 flex flex-col items-center gap-4">
            <MascotReaction expression="crashed" size="md" />
            <p className="font-mono text-sm text-cyber-magenta">⚠ {error}</p>
            <MagneticButton type="button" variant="magenta" onClick={onBack}>
              返回
            </MagneticButton>
          </div>
        )}

        {!isLoading && !error && profile && profile.stats.length === 0 && (
          <div className="mt-24 flex flex-col items-center gap-4 text-center">
            <MascotReaction expression="confident" size="lg" showLabel />
            <p className="font-display text-2xl italic text-white">还没有策略数据</p>
            <p className="font-mono text-xs text-white/50">EMPTY · DO A FEW SESSIONS FIRST</p>
            <MagneticButton type="button" onClick={onBack}>
              去对练 →
            </MagneticButton>
          </div>
        )}

        {!isLoading && !error && profile && profile.stats.length > 0 && (
          <ProfileContent
            stats={profile.stats}
            totalObservations={profile.total_observations}
            overrelied={profile.overrelied_strategy}
          />
        )}
      </div>
    </div>
  )
}

interface ContentProps {
  stats: StrategyStatItem[]
  totalObservations: number
  overrelied: CoachStrategyKey | null
}

function ProfileContent({ stats, totalObservations, overrelied }: ContentProps) {
  const top = stats[0]!
  const crutch = overrelied ? stats.find((s) => s.strategy === overrelied) : null
  const mascotExpression = overrelied ? 'caring' : 'confident'

  // Radar over ALL seven strategies (zero-filled for unused ones) so the
  // shape reads as the user's playstyle: a spike = a crutch, a round blob
  // = a balanced player. The over-relied strategy is dotted in magenta.
  const byStrategy = new Map(stats.map((s) => [s.strategy, s]))
  const radarData: RadarDatum[] = (Object.keys(STRATEGY_GLOSS) as CoachStrategyKey[]).map(
    (key) => ({
      label: STRATEGY_GLOSS[key],
      value: byStrategy.get(key)?.count ?? 0,
      highlight: key === overrelied,
    }),
  )

  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="mt-6 text-center"
      >
        <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-cyber-cyan/30 bg-cyber-cyan/5 px-3 py-1">
          <Swords className="h-3 w-3 text-cyber-cyan" />
          <span className="font-bebas text-[11px] tracking-[0.28em] text-cyber-cyan">
            STRATEGY · PROFILE
          </span>
        </div>
        <h1 className="font-orbitron text-6xl font-black uppercase leading-none tracking-tight">
          <GlowText variant="gradient">你的牌</GlowText>
        </h1>
      </motion.div>

      <div className="mt-8 flex items-start gap-4">
        <MascotReaction expression={mascotExpression} size="md" />
        <div className="flex-1 pt-2">
          <p className="font-bebas text-[11px] tracking-[0.24em] text-cyber-cyan">K-SAYS</p>
          <p className="mt-1 font-display text-xl italic text-white">
            {crutch
              ? `你最爱用「${STRATEGY_GLOSS[crutch.strategy]}」，但它老翻车——换换打法。`
              : `读了你 ${totalObservations} 手，路子还挺杂，继续保持。`}
          </p>
        </div>
      </div>

      {/* Crutch hero — the over-relied failing strategy the opponent punishes */}
      {crutch && (
        <HudFrame
          label="你的拐杖"
          tag={`×${crutch.count}`}
          color="rgba(255, 45, 170, 0.85)"
          className="cyber-glass-edge mt-6 rounded-3xl border-cyber-magenta/30 p-8 text-center"
        >
          <div className="inline-flex items-center gap-1.5">
            <Crosshair className="h-3.5 w-3.5 text-cyber-magenta" />
            <p className="font-bebas text-[11px] tracking-[0.28em] text-white/50">
              对手专门针对这一手
            </p>
          </div>
          <p className="mt-3 font-display text-4xl italic text-white">
            {STRATEGY_GLOSS[crutch.strategy]}
          </p>
          <p className="mt-2 font-orbitron text-[72px] font-black leading-none">
            <GlowText variant="gradient">{pct(crutch.win_rate)}%</GlowText>
          </p>
          <p className="mt-1 font-mono text-[11px] text-white/50">WIN RATE · 用了 {crutch.count} 次</p>
        </HudFrame>
      )}

      {/* Playstyle shape — usage radar over all seven strategies. */}
      <HudFrame
        label="PLAYSTYLE · SHAPE"
        tag={`${radarData.filter((d) => d.value > 0).length}/7`}
        className="cyber-glass-edge mt-8 rounded-3xl p-6"
      >
        <div className="flex flex-col items-center">
          <StrategyRadar data={radarData} size={260} />
          <p className="mt-2 text-center font-mono text-[11px] text-white/45">
            {overrelied
              ? '尖刺越突出 = 越依赖这一手'
              : '形状越圆 = 打法越均衡'}
          </p>
        </div>
      </HudFrame>

      {/* Per-strategy quality bars (good / mixed / poor) */}
      <div className="mt-8 space-y-3">
        <p className="font-bebas text-[11px] tracking-[0.28em] text-white/50">ALL · STRATEGIES</p>
        {stats.map((s) => {
          const isCrutch = s.strategy === overrelied
          return (
            <div
              key={s.strategy}
              className="flex items-center gap-3 rounded-2xl border border-cyber-hairline bg-cyber-deep/40 p-4 backdrop-blur-md"
              style={isCrutch ? { borderColor: 'rgba(255,45,170,0.4)' } : undefined}
            >
              <div className="flex-1">
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-grotesk text-sm text-white">
                    {STRATEGY_GLOSS[s.strategy]}
                    {isCrutch && <span className="ml-2 text-[11px] text-cyber-magenta">拐杖</span>}
                  </span>
                  <span className="font-mono text-[11px] text-white/50">
                    {pct(s.win_rate)}% · ×{s.count}
                  </span>
                </div>
                {/* Quality breakdown: 封神(good) / 路过(mixed) / 翻车(poor).
                 * Shows the full distribution, not just the win rate. */}
                <div className="flex h-2 overflow-hidden rounded-full bg-cyber-hairline">
                  {s.good > 0 && (
                    <div
                      className="h-full"
                      style={{ width: `${(s.good / s.count) * 100}%`, background: '#B7FF00' }}
                      title={`封神 ×${s.good}`}
                    />
                  )}
                  {s.mixed > 0 && (
                    <div
                      className="h-full"
                      style={{ width: `${(s.mixed / s.count) * 100}%`, background: '#FBBF24' }}
                      title={`路过 ×${s.mixed}`}
                    />
                  )}
                  {s.poor > 0 && (
                    <div
                      className="h-full"
                      style={{ width: `${(s.poor / s.count) * 100}%`, background: '#FF2DAA' }}
                      title={`翻车 ×${s.poor}`}
                    />
                  )}
                </div>
              </div>
            </div>
          )
        })}
        {/* Legend for the quality segments. */}
        <div className="flex justify-center gap-4 pt-1 font-mono text-[10px] text-white/40">
          <span><span className="text-cyber-lime">■</span> 封神</span>
          <span><span className="text-cyber-amber">■</span> 路过</span>
          <span><span className="text-cyber-magenta">■</span> 翻车</span>
        </div>
      </div>

      <div className="mt-8 flex justify-center gap-3">
        <StickerBadge variant="cyan">{totalObservations} 手出招</StickerBadge>
        <StickerBadge variant="orange">{stats.length} 种打法</StickerBadge>
      </div>
      {/* `top` is the highest-count strategy — surfaced as the headline number. */}
      <p className="mt-4 text-center font-mono text-[11px] text-white/40">
        最常用 · {STRATEGY_GLOSS[top.strategy]} ×{top.count}
      </p>
    </>
  )
}
