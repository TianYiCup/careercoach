import { useMemo, useState } from 'react'
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
} from 'recharts'
import { GlassCard, MascotReaction, VerdictBadge, WrappedCard } from '../../components'
import type { Score, ScoreResult } from '../../api/v1/types'
import { useShareCard } from './useShareCard'

interface ScorePageProps {
  score: Score
  mascotExpression: 'godlike' | 'crashed' | 'confident'
  /** Session id for generating sharecard */
  sessionId?: string
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

/** Map ScoreResult to ReviewVerdict for VerdictBadge reuse */
function toVerdict(result: ScoreResult): 'win' | 'neutral' | 'lose' {
  switch (result) {
    case 'shenfeng': return 'win'
    case 'guolu': return 'neutral'
    case 'fanche': return 'lose'
  }
}

/** Derive Wrapped comment from score — K's锐评 */
function deriveComment(score: Score): string {
  if (score.result === 'shenfeng') {
    return score.highlights || '今天的表现无可挑剔'
  }
  if (score.result === 'fanche') {
    return score.failures || '翻车了，但还能救'
  }
  return score.failures || '中规中矩，还能更好'
}

/** Derive gradient from ScoreResult */
function deriveGradient(result: ScoreResult): 'vivid' | 'glory' | 'crash' {
  switch (result) {
    case 'shenfeng': return 'glory'
    case 'guolu': return 'vivid'
    case 'fanche': return 'crash'
  }
}

/** Derive K expression emoji from ScoreResult */
function deriveExpressionEmoji(result: ScoreResult): string {
  switch (result) {
    case 'shenfeng': return '✨'
    case 'guolu': return '😎'
    case 'fanche': return '😅'
  }
}

/** Build badge labels from score deltas */
function buildBadges(score: Score): string[] {
  const badges: string[] = []
  if (score.aura >= 7) badges.push('气场+1')
  if (score.logic >= 7) badges.push('逻辑+1')
  if (score.emotion >= 7) badges.push('共情+1')
  if (score.professionalism >= 7) badges.push('专业+1')
  if (score.goal_achieve >= 7) badges.push('达成+1')
  if (score.result === 'shenfeng') badges.push('封神时刻')
  if (badges.length === 0) badges.push('继续努力')
  return badges.slice(0, 4)
}

/** Seeded PRNG — deterministic per session */
function seededRandom(seed: number) {
  let s = seed
  return () => {
    s = (s * 16807 + 0) % 2147483647
    return s / 2147483647
  }
}

/** Confetti burst for shenfeng */
function ConfettiBurst() {
  const particles = useMemo(() => {
    const colors = ['#6C4DFF', '#FF7AB6', '#3CFFE8', '#B0FF3C', '#FF6B35']
    const rand = seededRandom(42)
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

export function ScorePage({ score, mascotExpression, sessionId, onBack }: ScorePageProps) {
  const data = buildRadarData(score)
  const verdict = toVerdict(score.result)
  const isShenfeng = score.result === 'shenfeng'
  const [showWrapped, setShowWrapped] = useState(false)
  const { state: shareState, generateSessionCard, dismissError } = useShareCard()

  /** Trigger server-side sharecard generation */
  const handleGenerateWrapped = async () => {
    if (!sessionId) {
      // No session id — fall back to client-side Canvas rendering only
      setShowWrapped(true)
      return
    }
    await generateSessionCard(sessionId, { include_qrcode: false })
    setShowWrapped(true)
  }

  /** Download the server-rendered PNG */
  const handleDownloadServerPng = () => {
    if (!shareState.card?.png_url) return
    const link = document.createElement('a')
    link.href = shareState.card.png_url
    link.download = `careercoach-wrapped-${Date.now()}.png`
    link.target = '_blank'
    link.click()
  }

  return (
    <>
      {isShenfeng && <ConfettiBurst />}
      <div className="relative min-h-screen flex flex-col items-center px-4 py-8 overflow-y-auto">
        {/* Mascot + result title */}
        <div className="text-center mb-6">
          <MascotReaction expression={mascotExpression} size="lg" showLabel />
          <div className="mt-4 flex justify-center">
            <VerdictBadge verdict={verdict} size="md" />
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

        {/* Generate Wrapped card button — design-spec §9.4 */}
        {!showWrapped && (
          <button
            type="button"
            onClick={handleGenerateWrapped}
            disabled={shareState.isGenerating}
            className="mt-6 px-6 py-2.5 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform disabled:opacity-50 glow-purple"
          >
            {shareState.isGenerating ? '生成中...' : '生成 Wrapped 战报'}
          </button>
        )}

        {/* ShareCard error */}
        {shareState.error && (
          <div className="mt-3 w-full max-w-sm flex items-center justify-between px-4 py-2 bg-vivid-orange/15 border border-vivid-orange/40 rounded-radius-md">
            <span className="text-sm text-vivid-orange font-body">{shareState.error}</span>
            <button
              type="button"
              onClick={dismissError}
              className="text-vivid-orange/80 hover:text-vivid-orange text-lg leading-none"
            >
              &times;
            </button>
          </div>
        )}

        {/* Wrapped card preview — client-side Canvas rendering */}
        {showWrapped && (
          <div className="w-full max-w-sm mt-6 space-y-4">
            <GlassCard glow className="text-center space-y-4">
              <p className="text-sm text-ink-text-2 font-body">你的战报卡</p>

              {/* Client-side Canvas render (always available) */}
              <WrappedCard
                score={score.aura + score.logic + score.professionalism + score.emotion + score.goal_achieve > 35
                  ? ((score.aura + score.logic + score.professionalism + score.emotion + score.goal_achieve) / 5)
                  : score.aura}
                comment={deriveComment(score)}
                gradient={deriveGradient(score.result)}
                expression={deriveExpressionEmoji(score.result)}
                badges={buildBadges(score)}
              />

              {/* Server-rendered PNG (if available from API) */}
              {shareState.card && (
                <div className="space-y-2">
                  <p className="text-xs text-ink-text-3 font-body">
                    高清版（服务端渲染）
                  </p>
                  <button
                    type="button"
                    onClick={handleDownloadServerPng}
                    className="px-5 py-2 rounded-radius-pill bg-vivid-green/20 border border-vivid-green/40 text-vivid-green font-body text-sm hover:bg-vivid-green/30 transition-colors"
                  >
                    下载高清 PNG
                  </button>
                </div>
              )}
            </GlassCard>
          </div>
        )}

        {/* Back button */}
        <button
          type="button"
          onClick={onBack}
          className="mt-6 px-6 py-2.5 rounded-radius-pill bg-ink-card border border-ink-line text-ink-text text-sm font-body hover:bg-ink-card-2 transition-colors"
        >
          返回首页
        </button>
      </div>
    </>
  )
}
