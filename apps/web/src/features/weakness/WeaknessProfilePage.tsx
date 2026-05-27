/**
 * WeaknessProfilePage — cyberpunk redesign (design-spec §9.7).
 *
 * Data layer untouched: `useWeaknesses` hook, same `weaknesses[]` shape
 * (tag / frequency / last_seen) + `recommended_scenarios`.
 *
 * Visual rebuild only:
 *   · NeuralParticles background.
 *   · K intro line + frequency hero in HudFrame "TOP WEAKNESS".
 *   · Secondary weaknesses → bar list with cyan→amber→magenta gradient
 *     by rank (replaces the old vivid-cyan/yellow/orange ladder).
 *   · K's recommended scenarios → cyber TiltCard mini-cards.
 *   · StickerBadge stats row preserved (Z-gen sticker accent).
 */

import { useEffect } from 'react'
import { motion } from 'framer-motion'
import { ArrowLeft, Flame, Target } from 'lucide-react'

import { MascotReaction, StickerBadge } from '../../components'
import {
  GlowText,
  HudFrame,
  MagneticButton,
  NeuralParticles,
} from '../../components/cyber'
import { useWeaknesses } from './useWeaknesses'

function barAccent(rank: number, total: number): string {
  const ratio = total > 1 ? rank / (total - 1) : 1
  if (ratio <= 0.25) {
    return '#00F0FF'
  }
  if (ratio <= 0.5) {
    return '#FFE94D'
  }
  return '#FF2DAA'
}

function formatDate(iso: string): string {
  return iso.slice(5).replace('-', '/')
}

export function WeaknessProfilePage({ onBack }: { onBack: () => void }) {
  const { state, refetch } = useWeaknesses()
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
          className="inline-flex w-fit items-center gap-1 font-bebas text-xs tracking-[0.24em] text-white/60 transition-colors hover:text-cyber-magenta"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          BACK
        </button>

        {isLoading && (
          <div className="mt-24 flex flex-col items-center gap-4">
            <MascotReaction expression="thinking" size="md" />
            <p className="font-mono text-sm text-white/60 animate-pulse">
              ANALYZING · 弱点画像生成中…
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

        {!isLoading && !error && profile && profile.weaknesses.length === 0 && (
          <div className="mt-24 flex flex-col items-center gap-4 text-center">
            <MascotReaction expression="confident" size="lg" showLabel />
            <p className="font-display text-2xl italic text-white">还没有弱点数据</p>
            <p className="font-mono text-xs text-white/50">
              EMPTY · DO 2-3 SESSIONS FIRST
            </p>
            <MagneticButton type="button" onClick={onBack}>
              去对练 →
            </MagneticButton>
          </div>
        )}

        {!isLoading && !error && profile && profile.weaknesses.length > 0 && (
          <WeaknessContent
            weaknesses={profile.weaknesses}
            scenarios={profile.recommended_scenarios}
            onBack={onBack}
          />
        )}
      </div>
    </div>
  )
}

interface ContentProps {
  weaknesses: { tag: string; frequency: number; last_seen: string }[]
  scenarios: { id: string; title: string; background: string }[]
  onBack: () => void
}

function WeaknessContent({ weaknesses, scenarios, onBack }: ContentProps) {
  const topWeakness = weaknesses[0]!
  const totalFreq = weaknesses.reduce((s, w) => s + w.frequency, 0)
  const mascotExpression = weaknesses.length >= 3 ? 'caring' : 'confident'

  return (
    <>
      {/* Hero */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="mt-6 text-center"
      >
        <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-cyber-magenta/30 bg-cyber-magenta/5 px-3 py-1">
          <Target className="h-3 w-3 text-cyber-magenta" />
          <span className="font-bebas text-[11px] tracking-[0.28em] text-cyber-magenta">
            WEAKNESS · PROFILE
          </span>
        </div>
        <h1 className="font-orbitron text-6xl font-black uppercase leading-none tracking-tight">
          <GlowText variant="stroke" color="#FF2DAA">
            ANALYSIS
          </GlowText>
        </h1>
      </motion.div>

      {/* K intro */}
      <div className="mt-8 flex items-start gap-4">
        <MascotReaction expression={mascotExpression} size="md" />
        <div className="flex-1 pt-2">
          <p className="font-bebas text-[11px] tracking-[0.24em] text-cyber-cyan">K-SAYS</p>
          <p className="mt-1 font-display text-xl italic text-white">
            扒了 {totalFreq} 次复盘 · 发现了这些……
          </p>
        </div>
      </div>

      {/* Top weakness */}
      <HudFrame
        label="TOP · WEAKNESS"
        tag={`×${topWeakness.frequency}`}
        color="rgba(255, 45, 170, 0.85)"
        className="cyber-glass-edge mt-6 rounded-3xl border-cyber-magenta/30 p-8 text-center"
      >
        <p className="font-bebas text-[11px] tracking-[0.28em] text-white/50">
          你最常翻车的瞬间
        </p>
        <p className="mt-2 font-orbitron text-[96px] font-black leading-none">
          <GlowText variant="gradient">{topWeakness.frequency}</GlowText>
        </p>
        <p className="mt-2 font-display text-2xl italic text-white">{topWeakness.tag}</p>
        <p className="mt-2 font-mono text-[11px] text-white/50">
          LAST · {formatDate(topWeakness.last_seen)}
        </p>
      </HudFrame>

      {/* Secondary weaknesses */}
      {weaknesses.length > 1 && (
        <div className="mt-8 space-y-3">
          <p className="font-bebas text-[11px] tracking-[0.28em] text-white/50">
            OTHER · WEAKNESSES
          </p>
          {weaknesses.slice(1).map((w, idx) => {
            const accent = barAccent(idx, weaknesses.length - 1)
            return (
              <div
                key={w.tag}
                className="flex items-center gap-3 rounded-2xl border border-cyber-hairline bg-cyber-deep/40 p-4 backdrop-blur-md"
              >
                <span className="font-mono text-xs text-white/40">
                  #{(idx + 2).toString().padStart(2, '0')}
                </span>
                <div className="flex-1">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-grotesk text-sm text-white">{w.tag}</span>
                    <span className="font-mono text-[11px] text-white/50">×{w.frequency}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-cyber-hairline">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${(w.frequency / topWeakness.frequency) * 100}%`,
                        background: accent,
                        boxShadow: `0 0 8px ${accent}88`,
                      }}
                    />
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Recommendations */}
      {scenarios.length > 0 && (
        <HudFrame
          label="K · RECOMMENDS"
          tag={`${scenarios.length}`}
          className="cyber-glass-edge mt-8 rounded-3xl p-6"
        >
          <div className="flex items-center gap-2">
            <Flame className="h-4 w-4 text-cyber-amber" />
            <p className="font-bebas text-[11px] tracking-[0.28em] text-cyber-amber">
              开 · 小灶
            </p>
          </div>
          <div className="mt-4 -mx-2 flex gap-3 overflow-x-auto px-2 pb-2">
            {scenarios.map(sc => (
              <button
                key={sc.id}
                type="button"
                onClick={onBack}
                className="w-44 flex-shrink-0 rounded-2xl border border-cyber-hairline bg-cyber-deep/50 p-3 text-left transition-all hover:border-cyber-cyan/40 hover:bg-cyber-deep/80"
              >
                <p className="font-grotesk truncate text-sm font-medium text-white">
                  {sc.title}
                </p>
                <p className="mt-1 line-clamp-2 font-mono text-[11px] text-white/50">
                  {sc.background}
                </p>
              </button>
            ))}
          </div>
        </HudFrame>
      )}

      {/* Stats sticker row */}
      <div className="mt-8 flex justify-center gap-3">
        <StickerBadge variant="orange">{weaknesses.length} 个弱点</StickerBadge>
        <StickerBadge variant="cyan">{totalFreq} 次出现</StickerBadge>
      </div>
    </>
  )
}
