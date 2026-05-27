/**
 * ReviewResultPage — cyberpunk redesign (PRD §3.3 US-C2 / design-spec §9.6).
 *
 * Data layer unchanged: GET `/v1/review/uploads/:id`, ReviewUploadResponse
 * shape, VerdictBadge + BetterSuggestion components reused as-is.
 *
 * Visual rebuild only:
 *   · Three-column dashboard inside NeuralParticles background.
 *   · Left "STATS" HudFrame summary panel.
 *   · Center "TURNS" scrollable column with cyber bubble cards per turn.
 *   · Right "REPORT" HudFrame with Orbitron score numeric + top failures
 *     + improvements + Training plan CTA.
 */

import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowLeft, Sparkles } from 'lucide-react'

import { BetterSuggestion, MascotReaction, VerdictBadge } from '../../components'
import {
  GlowText,
  HudFrame,
  MagneticButton,
  NeuralParticles,
} from '../../components/cyber'
import { apiClient, ApiError } from '../../api/v1'
import type { ReviewTurn, ReviewUploadResponse } from '../../api/v1'

interface ReviewResultPageProps {
  uploadId: string
  onBack: () => void
  /** "生成训练计划" CTA — typically routes to the weakness profile. */
  onTrain: () => void
}

export function ReviewResultPage({
  uploadId,
  onBack,
  onTrain,
}: ReviewResultPageProps) {
  const [data, setData] = useState<ReviewUploadResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)

  useEffect(() => {
    const fetchResult = async () => {
      try {
        const res = await apiClient.get<ReviewUploadResponse>(`/review/uploads/${uploadId}`)
        setData(res)
      } catch (e) {
        if (e instanceof ApiError) {
          setError('加载失败，请稍后重试')
        } else {
          setError('网络异常')
        }
      }
    }
    void fetchResult()
  }, [uploadId])

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

      <header className="relative z-10 mx-auto flex w-full max-w-7xl items-center justify-between px-6 pt-6">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1 font-bebas text-xs tracking-[0.24em] text-white/60 transition-colors hover:text-cyber-cyan"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          BACK
        </button>
        <div className="flex items-center gap-3">
          <MascotReaction
            expression={
              data
                ? data.turns.filter(t => t.verdict === 'lose').length / Math.max(1, data.turns.length) > 0.4
                  ? 'caring'
                  : data.turns.length > 0 &&
                      data.turns.filter(t => t.verdict === 'lose').length === 0
                    ? 'godlike'
                    : 'confident'
                : 'thinking'
            }
            size="sm"
          />
          <div>
            <p className="font-bebas text-[10px] tracking-[0.28em] text-cyber-cyan">
              REVIEW · DISSECTION
            </p>
            <p className="font-orbitron text-sm font-semibold text-white">
              {data ? `${data.turns.length} TURNS` : 'LOADING…'}
            </p>
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto w-full max-w-7xl flex-1 px-6 py-6">
        {error && (
          <div className="mt-20 flex flex-col items-center gap-4">
            <MascotReaction expression="crashed" size="md" />
            <p className="font-mono text-sm text-cyber-magenta">⚠ {error}</p>
            <MagneticButton type="button" variant="magenta" onClick={onBack}>
              返回
            </MagneticButton>
          </div>
        )}

        {!error && !data && (
          <div className="mt-20 flex flex-col items-center gap-4">
            <MascotReaction expression="thinking" size="md" />
            <p className="font-mono text-sm text-white/60 animate-pulse">
              ANALYZING · 正在分析对话…
            </p>
          </div>
        )}

        {data && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="grid grid-cols-1 gap-5 md:grid-cols-12"
          >
            <div className="md:col-span-3">
              <LeftPanel
                totalTurns={data.turns.length}
                userCount={data.turns.filter(t => t.speaker === 'user').length}
                opponentCount={data.turns.filter(t => t.speaker === 'opponent').length}
                loseCount={data.turns.filter(t => t.verdict === 'lose').length}
              />
            </div>
            <div className="md:col-span-6">
              <CenterPanel
                turns={data.turns}
                expandedIdx={expandedIdx}
                onToggle={setExpandedIdx}
              />
            </div>
            <div className="md:col-span-3">
              <RightPanel summary={data.summary} />
            </div>
          </motion.div>
        )}
      </main>
    </div>
  )
}

/* ── Left · Stats summary ───────────────────────────────────────── */

interface LeftProps {
  totalTurns: number
  userCount: number
  opponentCount: number
  loseCount: number
}

function LeftPanel({ totalTurns, userCount, opponentCount, loseCount }: LeftProps) {
  return (
    <HudFrame label="STATS" tag={`${totalTurns}T`} className="cyber-glass-edge rounded-3xl p-5">
      <p className="font-bebas text-[11px] tracking-[0.28em] text-cyber-cyan">UPLOAD · SUMMARY</p>
      <div className="mt-4 space-y-3 font-mono text-xs">
        <Row label="TOTAL TURNS" value={totalTurns} accent="#FFFFFF" />
        <Row label="MY TURNS" value={userCount} accent="#00F0FF" />
        <Row label="THEIR TURNS" value={opponentCount} accent="#A892FF" />
        {loseCount > 0 && <Row label="CRASHED" value={loseCount} accent="#FF2DAA" />}
      </div>
    </HudFrame>
  )
}

function Row({ label, value, accent }: { label: string; value: number; accent: string }) {
  return (
    <div className="flex items-center justify-between border-b border-cyber-hairline pb-2">
      <span className="text-white/50">{label}</span>
      <span
        className="font-orbitron text-lg font-bold"
        style={{ color: accent, textShadow: `0 0 8px ${accent}66` }}
      >
        ×{value}
      </span>
    </div>
  )
}

/* ── Center · Turn list ─────────────────────────────────────────── */

interface CenterProps {
  turns: ReviewTurn[]
  expandedIdx: number | null
  onToggle: (idx: number | null) => void
}

function CenterPanel({ turns, expandedIdx, onToggle }: CenterProps) {
  return (
    <HudFrame
      label="TURNS · ANALYSIS"
      tag="K-SCAN"
      className="cyber-glass-edge rounded-3xl p-5"
    >
      <div className="space-y-3">
        {turns.map(turn => (
          <ReviewTurnCard
            key={turn.turn_idx}
            turn={turn}
            isExpanded={expandedIdx === turn.turn_idx}
            onToggle={() => onToggle(expandedIdx === turn.turn_idx ? null : turn.turn_idx)}
          />
        ))}
      </div>
    </HudFrame>
  )
}

function ReviewTurnCard({
  turn,
  isExpanded,
  onToggle,
}: {
  turn: ReviewTurn
  isExpanded: boolean
  onToggle: () => void
}) {
  const isLose = turn.verdict === 'lose'
  const baseStyle = isLose
    ? 'border-cyber-magenta/30 bg-cyber-magenta/10 hover:bg-cyber-magenta/15'
    : turn.speaker === 'user'
      ? 'border-cyber-lime/20 bg-cyber-lime/5 hover:bg-cyber-lime/10'
      : 'border-cyber-hairline bg-cyber-deep/40 hover:bg-cyber-deep/60'

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onToggle}
        className={`group w-full rounded-2xl border px-4 py-3 text-left transition-all ${baseStyle}`}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1">
            <p className="font-mono text-[10px] uppercase tracking-wider text-white/40">
              {turn.speaker === 'opponent' ? '🤵 对方' : '👤 我'} · #{turn.turn_idx + 1}
            </p>
            <p className="font-grotesk mt-1 text-sm leading-relaxed text-white">{turn.content}</p>
          </div>
          <VerdictBadge verdict={turn.verdict} size="sm" />
        </div>
      </button>

      {isExpanded && isLose && turn.reason && (
        <div className="pl-3">
          <BetterSuggestion reason={turn.reason ?? ''} better={turn.better ?? ''} />
        </div>
      )}
    </div>
  )
}

/* ── Right · Summary report ─────────────────────────────────────── */

function RightPanel({ summary }: { summary: ReviewUploadResponse['summary'] }) {
  if (!summary) {
    return (
      <HudFrame label="REPORT" className="cyber-glass-edge rounded-3xl p-5 text-center">
        <p className="font-mono text-xs text-white/40">EMPTY · NO RESULT</p>
      </HudFrame>
    )
  }

  return (
    <HudFrame label="REPORT" tag={`${summary.score}/10`} className="cyber-glass-edge rounded-3xl p-5">
      {/* Score */}
      <div className="flex flex-col items-center">
        <p className="font-bebas text-[11px] tracking-[0.28em] text-cyber-cyan">OVERALL</p>
        <p className="mt-1 font-orbitron text-[64px] font-black leading-none">
          <GlowText variant="gradient">{summary.score}</GlowText>
        </p>
        <p className="font-mono text-[10px] text-white/40">/ 10</p>
      </div>

      {/* Top failures */}
      {summary.top_failures.length > 0 && (
        <div className="mt-6">
          <p className="font-bebas text-[11px] tracking-[0.28em] text-cyber-magenta">
            💥 TOP · CRASHES
          </p>
          <ol className="mt-2 space-y-2">
            {summary.top_failures.map((f, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-white/80">
                <span className="font-mono text-cyber-magenta">{String(i + 1).padStart(2, '0')}</span>
                <span className="font-grotesk leading-relaxed">{f}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Improvements */}
      {summary.improvements.length > 0 && (
        <div className="mt-6">
          <p className="font-bebas text-[11px] tracking-[0.28em] text-cyber-lime">
            ✨ K · UPGRADES
          </p>
          <ol className="mt-2 space-y-2">
            {summary.improvements.map((s, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-white/80">
                <span className="font-mono text-cyber-lime">{String(i + 1).padStart(2, '0')}</span>
                <span className="font-grotesk leading-relaxed">{s}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="mt-6 flex justify-center">
        <MagneticButton type="button" variant="lime" onClick={onTrain}>
          <Sparkles className="h-3.5 w-3.5" />
          生成训练计划
        </MagneticButton>
      </div>
    </HudFrame>
  )
}
