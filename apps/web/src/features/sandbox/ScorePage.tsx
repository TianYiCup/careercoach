/**
 * ScorePage — cyberpunk redesign (design-spec §9.4).
 *
 * Data layer untouched: `useShareCard` for server-rendered PNG, recharts
 * `RadarChart`, local seeded `ConfettiBurst`, `WrappedCard` Canvas, and
 * `VerdictBadge` all preserved.
 *
 * Visual rebuild only:
 *   · NeuralParticles + tech-grid background (matches Home).
 *   · Massive Orbitron score numeric — cyber gradient on 封神, magenta
 *     stroke on 翻车, cyan stroke on 路过.
 *   · HudFrame around the radar chart with cyan grid + magenta radar.
 *   · Highlights / failures in dual HudFrame cards (lime / amber tone).
 *   · MagneticButton CTA for Wrapped card generation + return-home.
 */

import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowLeft, Download, Sparkles } from 'lucide-react'
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from 'recharts'

import { MascotReaction, VerdictBadge, WrappedCard } from '../../components'
import {
  GlowText,
  HudFrame,
  MagneticButton,
  NeuralParticles,
} from '../../components/cyber'
import type { Score, ScoreResult } from '../../api/v1/types'
import { useShareCard } from './useShareCard'

interface ScorePageProps {
  score: Score
  mascotExpression: 'godlike' | 'crashed' | 'confident'
  sessionId?: string
  onBack: () => void
}

function buildRadarData(score: Score) {
  return [
    { dimension: '气场', value: score.aura, fullMark: 10 },
    { dimension: '逻辑', value: score.logic, fullMark: 10 },
    { dimension: '共情', value: score.emotion, fullMark: 10 },
    { dimension: '专业', value: score.professionalism, fullMark: 10 },
    { dimension: '目标', value: score.goal_achieve, fullMark: 10 },
  ]
}

function toVerdict(result: ScoreResult): 'win' | 'neutral' | 'lose' {
  switch (result) {
    case 'shenfeng':
      return 'win'
    case 'guolu':
      return 'neutral'
    case 'fanche':
      return 'lose'
  }
}

function deriveComment(score: Score): string {
  if (score.result === 'shenfeng') {
    return score.highlights || '今天的表现无可挑剔'
  }
  if (score.result === 'fanche') {
    return score.failures || '翻车了，但还能救'
  }
  return score.failures || '中规中矩，还能更好'
}

function deriveGradient(result: ScoreResult): 'vivid' | 'glory' | 'crash' {
  switch (result) {
    case 'shenfeng':
      return 'glory'
    case 'guolu':
      return 'vivid'
    case 'fanche':
      return 'crash'
  }
}

function deriveExpressionEmoji(result: ScoreResult): string {
  switch (result) {
    case 'shenfeng':
      return '✨'
    case 'guolu':
      return '😎'
    case 'fanche':
      return '😅'
  }
}

function buildBadges(score: Score): string[] {
  const badges: string[] = []
  if (score.aura >= 7) {
    badges.push('气场+1')
  }
  if (score.logic >= 7) {
    badges.push('逻辑+1')
  }
  if (score.emotion >= 7) {
    badges.push('共情+1')
  }
  if (score.professionalism >= 7) {
    badges.push('专业+1')
  }
  if (score.goal_achieve >= 7) {
    badges.push('达成+1')
  }
  if (score.result === 'shenfeng') {
    badges.push('封神时刻')
  }
  if (badges.length === 0) {
    badges.push('继续努力')
  }
  return badges.slice(0, 4)
}

function seededRandom(seed: number) {
  let s = seed
  return () => {
    s = (s * 16807 + 0) % 2147483647
    return s / 2147483647
  }
}

function ConfettiBurst() {
  const particles = useMemo(() => {
    const colors = ['#00F0FF', '#B7FF00', '#FF2DAA', '#FFE94D', '#A892FF']
    const rand = seededRandom(42)
    return Array.from({ length: 36 }, (_, i) => ({
      id: i,
      left: `${rand() * 100}%`,
      delay: `${rand() * 0.5}s`,
      duration: `${1.5 + rand() * 1.5}s`,
      color: colors[i % colors.length]!,
      size: 4 + rand() * 6,
    }))
  }, [])

  return (
    <div className="pointer-events-none fixed inset-0 z-50 overflow-hidden">
      {particles.map(p => (
        <div
          key={p.id}
          className="absolute animate-bounce rounded-sm"
          style={{
            left: p.left,
            top: '-10px',
            width: p.size,
            height: p.size,
            backgroundColor: p.color,
            boxShadow: `0 0 ${p.size * 2}px ${p.color}`,
            animationDelay: p.delay,
            animationDuration: p.duration,
            animationIterationCount: '3',
            opacity: 0.85,
          }}
        />
      ))}
    </div>
  )
}

function averageScore(s: Score): number {
  return (s.aura + s.logic + s.professionalism + s.emotion + s.goal_achieve) / 5
}

function pickScoreColor(result: ScoreResult): string {
  switch (result) {
    case 'shenfeng':
      return '#B7FF00'
    case 'guolu':
      return '#00F0FF'
    case 'fanche':
      return '#FF6B35'
  }
}

export function ScorePage({ score, mascotExpression, sessionId, onBack }: ScorePageProps) {
  const data = buildRadarData(score)
  const verdict = toVerdict(score.result)
  const isShenfeng = score.result === 'shenfeng'
  const avg = averageScore(score)
  const accent = pickScoreColor(score.result)
  const [showWrapped, setShowWrapped] = useState(false)
  const { state: shareState, generateSessionCard, dismissError } = useShareCard()

  const handleGenerateWrapped = async () => {
    if (!sessionId) {
      setShowWrapped(true)
      return
    }
    await generateSessionCard(sessionId, { include_qrcode: false })
    setShowWrapped(true)
  }

  const handleDownloadServerPng = () => {
    if (!shareState.card?.png_url) {
      return
    }
    const link = document.createElement('a')
    link.href = shareState.card.png_url
    link.download = `careercoach-wrapped-${Date.now()}.png`
    link.target = '_blank'
    link.click()
  }

  return (
    <>
      {isShenfeng && <ConfettiBurst />}
      <div className="relative flex min-h-screen flex-col overflow-hidden bg-cyber-void text-white">
        <NeuralParticles count={1200} />
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

        <div className="relative z-10 mx-auto flex w-full max-w-4xl flex-1 flex-col px-6 py-8">
          {/* Top bar */}
          <div className="mb-6 flex items-center justify-between">
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-1 font-bebas text-xs tracking-[0.24em] text-white/60 transition-colors hover:text-cyber-cyan"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              HOME
            </button>
            <span className="font-mono text-[10px] text-white/40">
              SESSION · {sessionId ? sessionId.slice(-8).toUpperCase() : 'LOCAL'}
            </span>
          </div>

          {/* Hero */}
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="text-center"
          >
            <div className="flex justify-center">
              <MascotReaction expression={mascotExpression} size="lg" showLabel />
            </div>
            <div className="mt-4 flex justify-center">
              <VerdictBadge verdict={verdict} size="md" />
            </div>
            <p className="mt-6 font-bebas text-[11px] tracking-[0.32em] text-white/50">
              OVERALL · SCORE
            </p>
            <div className="mt-1 font-orbitron text-[120px] font-black leading-none tracking-tight md:text-[160px]">
              {score.result === 'shenfeng' ? (
                <GlowText variant="gradient">{avg.toFixed(1)}</GlowText>
              ) : (
                <GlowText variant="stroke" color={accent}>
                  {avg.toFixed(1)}
                </GlowText>
              )}
            </div>
            <p className="font-mono text-xs text-white/50">
              AURA {score.aura} · LOGIC {score.logic} · EMOTION {score.emotion} · PRO{' '}
              {score.professionalism} · GOAL {score.goal_achieve}
            </p>
          </motion.div>

          {/* Two-column: radar + highlights */}
          <div className="mt-10 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <HudFrame
              label="RADAR · ABILITY"
              tag="5D"
              className="cyber-glass-edge rounded-3xl p-6"
            >
              <ResponsiveContainer width="100%" height={260}>
                <RadarChart data={data} cx="50%" cy="50%" outerRadius="72%">
                  <PolarGrid stroke="rgba(0, 240, 255, 0.18)" />
                  <PolarAngleAxis
                    dataKey="dimension"
                    tick={{ fill: '#00F0FF', fontSize: 12 }}
                  />
                  <PolarRadiusAxis
                    angle={90}
                    domain={[0, 10]}
                    tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 10 }}
                  />
                  <Radar
                    name="评分"
                    dataKey="value"
                    stroke="#FF2DAA"
                    fill="#FF2DAA"
                    fillOpacity={0.28}
                    strokeWidth={2}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </HudFrame>

            <div className="space-y-4">
              {score.highlights && (
                <HudFrame
                  label="HIGHLIGHT · K-SAYS"
                  tag="✨"
                  color="rgba(183, 255, 0, 0.85)"
                  className="cyber-glass-edge rounded-3xl border-cyber-lime/30 p-6"
                >
                  <p className="font-bebas text-[11px] tracking-[0.28em] text-cyber-lime">
                    PROPS
                  </p>
                  <p className="mt-2 text-sm leading-relaxed text-white/85">{score.highlights}</p>
                </HudFrame>
              )}
              {score.failures && (
                <HudFrame
                  label="IMPROVE · K-SAYS"
                  tag="⚠"
                  color="rgba(255, 107, 53, 0.85)"
                  className="cyber-glass-edge rounded-3xl border-cyber-amber/30 p-6"
                >
                  <p className="font-bebas text-[11px] tracking-[0.28em] text-cyber-amber">
                    NEXT
                  </p>
                  <p className="mt-2 text-sm leading-relaxed text-white/85">{score.failures}</p>
                </HudFrame>
              )}
            </div>
          </div>

          {/* Wrapped CTA */}
          {!showWrapped && (
            <div className="mt-10 flex justify-center">
              <MagneticButton
                type="button"
                onClick={handleGenerateWrapped}
                disabled={shareState.isGenerating}
                variant={isShenfeng ? 'lime' : 'cyan'}
              >
                <Sparkles className="h-4 w-4" />
                {shareState.isGenerating ? '生成中…' : '生成 Wrapped 战报'}
              </MagneticButton>
            </div>
          )}

          {shareState.error && (
            <div
              role="alert"
              className="mt-4 flex items-center justify-between rounded-2xl border border-cyber-magenta/40 bg-cyber-magenta/10 px-4 py-2"
            >
              <span className="font-mono text-xs text-cyber-magenta">⚠ {shareState.error}</span>
              <button
                type="button"
                onClick={dismissError}
                className="text-cyber-magenta/70 hover:text-cyber-magenta"
              >
                ×
              </button>
            </div>
          )}

          {/* Wrapped preview */}
          {showWrapped && (
            <HudFrame
              label="WRAPPED · 9:16"
              tag="SHARE"
              className="cyber-glass-edge mt-10 rounded-3xl p-6"
            >
              <div className="flex flex-col items-center gap-4">
                <p className="font-bebas text-[11px] tracking-[0.28em] text-cyber-cyan">
                  YOUR · CARD
                </p>
                <WrappedCard
                  score={avg > 0 ? avg : score.aura}
                  comment={deriveComment(score)}
                  gradient={deriveGradient(score.result)}
                  expression={deriveExpressionEmoji(score.result)}
                  badges={buildBadges(score)}
                />

                {shareState.card && (
                  <div className="mt-2 flex flex-col items-center gap-2">
                    <p className="font-mono text-[11px] text-white/50">
                      HIGH-RES · SERVER RENDERED
                    </p>
                    <MagneticButton
                      type="button"
                      variant="lime"
                      onClick={handleDownloadServerPng}
                    >
                      <Download className="h-4 w-4" />
                      下载高清 PNG
                    </MagneticButton>
                  </div>
                )}
              </div>
            </HudFrame>
          )}

          {/* Footer */}
          <div className="mt-10 flex justify-center">
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-2 rounded-full border border-cyber-hairline px-5 py-2 font-bebas text-xs tracking-[0.24em] text-white/70 transition-colors hover:border-cyber-cyan/40 hover:text-cyber-cyan"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              RETURN · HOME
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
