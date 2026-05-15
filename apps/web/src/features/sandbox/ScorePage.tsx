import { useMemo } from 'react'
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
} from 'recharts'
import { GlassCard, MascotReaction, StickerBadge } from '../../components'
import type { Score, ScoreResult } from '../../api/v1/types'

interface ScorePageProps {
  score: Score
  mascotExpression: 'godlike' | 'crashed' | 'confident'
  onBack: () => void
}

/** Map Score fields to radar chart data points */
function buildRadarData(score: Score) {
  return [
    { dimension: '气场', value: score.aura, fullMark: 10 },
    { dimension: '逻辑', value: score.logic, fullMark: 10 },
    { dimension: '共情', value: score.emotion, fullMark: 10 },
    { dimension: '专业', value: score.professionalism, fullMark: 10 },
    { dimension: '目标', value: score.goal_achieve, fullMark: 10 },
  ]
}

/** Get result display config */
function getResultConfig(result: ScoreResult) {
  switch (result) {
    case 'shenfeng':
      return { label: '封神', variant: 'green' as const, emoji: '✨' }
    case 'fanche':
      return { label: '翻车', variant: 'orange' as const, emoji: '💥' }
    case 'guolu':
      return { label: '路过', variant: 'cyan' as const, emoji: '🌀' }
  }
}

/** Seeded PRNG — deterministic per session, avoids Math.random() purity violation */
function seededRandom(seed: number) {
  // Simple LCG (Lehmer) for deterministic pseudo-random
  let s = seed
  return () => {
    s = (s * 16807 + 0) % 2147483647
    return s / 2147483647
  }
}

/** Confetti burst for shenfeng — pure CSS animation, deterministic values */
function ConfettiBurst() {
  const particles = useMemo(() => {
    const colors = ['#6C4DFF', '#FF7AB6', '#3CFFE8', '#B0FF3C', '#FF6B35']
    const rand = seededRandom(42) // fixed seed → deterministic render
    return Array.from({ length: 30 }, (_, i) => ({
      id: i,
      left: `${rand() * 100}%`,
      delay: `${rand() * 0.5}s`,
      duration: `${1.5 + rand() * 1.5}s`,
      color: colors[i % colors.length],
      size: 4 + rand() * 6,
    }))
  }, [])

  return (
    <div className="pointer-events-none fixed inset-0 z-50 overflow-hidden">
      {particles.map((p) => (
        <div
          key={p.id}
          className="absolute rounded-full animate-bounce"
          style={{
            left: p.left,
            top: '-10px',
            width: p.size,
            height: p.size,
            backgroundColor: p.color,
            animationDelay: p.delay,
            animationDuration: p.duration,
            animationIterationCount: '3',
            opacity: 0.8,
          }}
        />
      ))}
    </div>
  )
}

export function ScorePage({ score, mascotExpression, onBack }: ScorePageProps) {
  const data = buildRadarData(score)
  const config = getResultConfig(score.result)
  const isShenfeng = score.result === 'shenfeng'

  return (
    <>
      {isShenfeng && <ConfettiBurst />}
      <div className="relative min-h-screen flex flex-col items-center px-4 py-8 overflow-y-auto">
        {/* Mascot + result title */}
        <div className="text-center mb-6">
          <MascotReaction expression={mascotExpression} size="lg" showLabel />
          <div className="mt-4 flex justify-center">
            <StickerBadge variant={config.variant}>
              {config.emoji} {config.label}
            </StickerBadge>
          </div>
        </div>

        {/* Radar chart */}
        <GlassCard className="w-full max-w-sm">
          <h3 className="text-sm font-body text-ink-text-2 text-center mb-2">能力雷达</h3>
          <ResponsiveContainer width="100%" height={240}>
            <RadarChart data={data} cx="50%" cy="50%" outerRadius="70%">
              <PolarGrid stroke="var(--color-ink-line)" />
              <PolarAngleAxis
                dataKey="dimension"
                tick={{ fill: 'var(--color-ink-text-2)', fontSize: 12 }}
              />
              <PolarRadiusAxis
                angle={90}
                domain={[0, 10]}
                tick={{ fill: 'var(--color-ink-text-3)', fontSize: 10 }}
              />
              <Radar
                name="评分"
                dataKey="value"
                stroke="var(--color-vivid-purple)"
                fill="var(--color-vivid-purple)"
                fillOpacity={0.3}
              />
            </RadarChart>
          </ResponsiveContainer>
        </GlassCard>

        {/* Highlights & failures */}
        <div className="w-full max-w-sm mt-4 space-y-3">
          {score.highlights && (
            <GlassCard>
              <p className="text-xs text-tone-safe font-body mb-1">K 说你棒的地方</p>
              <p className="text-sm text-ink-text font-body">{score.highlights}</p>
            </GlassCard>
          )}
          {score.failures && (
            <GlassCard>
              <p className="text-xs text-tone-aggro font-body mb-1">可以更好的地方</p>
              <p className="text-sm text-ink-text font-body">{score.failures}</p>
            </GlassCard>
          )}
        </div>

        {/* Back button */}
        <button
          type="button"
          onClick={onBack}
          className="mt-6 px-6 py-2.5 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform"
        >
          返回首页
        </button>
      </div>
    </>
  )
}
