/**
 * 复盘结果页 — 三栏 UI — PRD §3.3 US-C2 / design-spec §9.6
 *
 * 左栏：上传内容摘要（句数统计）
 * 中栏：逐句三色标记 + K 更佳话术
 * 右栏：战报（总分 + 三大翻车 + 神回话术）
 *
 * v0.1 简化：桌面端三栏并排，移动端单列堆叠。
 */

import { useState, useEffect } from 'react'

import { BlobBackground, GlassCard, MascotReaction } from '../../components'
import { apiClient, ApiError } from '../../api/v1'
import type { ReviewUploadResponse, ReviewTurn, ReviewVerdict } from '../../api/v1'

// --- Verdict display mapping ---

const VERDICT_CONFIG: Record<ReviewVerdict, { label: string; color: string; icon: string }> = {
  win: { label: '封神', color: 'text-vivid-green', icon: '✨' },
  neutral: { label: '路过', color: 'text-ink-text-3', icon: '🌀' },
  lose: { label: '翻车', color: 'text-vivid-orange', icon: '💥' },
}

export function ReviewResultPage({
  uploadId,
  onBack,
}: {
  uploadId: string
  onBack: () => void
}) {
  const [data, setData] = useState<ReviewUploadResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)

  useEffect(() => {
    const fetchResult = async () => {
      try {
        const res = await apiClient.get<ReviewUploadResponse>(
          `/review/uploads/${uploadId}`,
        )
        setData(res)
      } catch (e) {
        if (e instanceof ApiError) {
          setError('加载失败，请稍后重试')
        } else {
          setError('网络异常')
        }
      }
    }
    fetchResult()
  }, [uploadId])

  if (error) {
    return (
      <div className="relative min-h-screen flex flex-col items-center justify-center px-4 py-12 overflow-hidden">
        <BlobBackground />
        <div className="relative z-10 text-center space-y-4">
          <p className="text-vivid-orange font-body">{error}</p>
          <button
            type="button"
            onClick={onBack}
            className="px-5 py-2 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium"
          >
            返回
          </button>
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="relative min-h-screen flex flex-col items-center justify-center px-4 overflow-hidden">
        <BlobBackground />
        <div className="relative z-10 text-center space-y-4">
          <MascotReaction expression="thinking" size="lg" />
          <p className="text-ink-text-2 font-body">正在分析对话…</p>
        </div>
      </div>
    )
  }

  const userTurns = data.turns.filter((t) => t.speaker === 'user')
  const opponentTurns = data.turns.filter((t) => t.speaker === 'opponent')
  const loseTurns = data.turns.filter((t) => t.verdict === 'lose')

  return (
    <div className="relative min-h-screen flex flex-col overflow-hidden">
      <BlobBackground />

      {/* Header */}
      <header className="relative z-10 flex items-center justify-between px-4 py-3 glass">
        <button
          type="button"
          onClick={onBack}
          className="text-ink-text-2 text-sm hover:text-ink-text transition-colors"
        >
          ← 返回
        </button>
        <div className="flex items-center gap-2">
          <MascotReaction expression="confident" size="sm" />
          <span className="text-sm font-body text-ink-text">
            K 来扒一扒
          </span>
        </div>
        <div className="w-14" />
      </header>

      {/* Three-column layout */}
      <main className="relative z-10 flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-6xl grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Left: Upload summary */}
          <LeftPanel
            totalTurns={data.turns.length}
            userCount={userTurns.length}
            opponentCount={opponentTurns.length}
            loseCount={loseTurns.length}
          />

          {/* Center: Turn-by-turn analysis */}
          <CenterPanel
            turns={data.turns}
            expandedIdx={expandedIdx}
            onToggle={setExpandedIdx}
          />

          {/* Right: Summary report */}
          <RightPanel summary={data.summary} />
        </div>
      </main>
    </div>
  )
}

// --- Left Panel: Upload summary ---

function LeftPanel({
  totalTurns,
  userCount,
  opponentCount,
  loseCount,
}: {
  totalTurns: number
  userCount: number
  opponentCount: number
  loseCount: number
}) {
  return (
    <GlassCard className="space-y-3">
      <h2 className="text-sm font-body font-medium text-ink-text-2">📥 上传内容</h2>
      <div className="space-y-2 text-sm font-body">
        <div className="flex justify-between">
          <span className="text-ink-text-3">已识别</span>
          <span className="text-ink-text">{totalTurns} 句</span>
        </div>
        <div className="flex justify-between">
          <span className="text-ink-text-3">我说的</span>
          <span className="text-ink-text">{userCount} 句</span>
        </div>
        <div className="flex justify-between">
          <span className="text-ink-text-3">对方说的</span>
          <span className="text-ink-text">{opponentCount} 句</span>
        </div>
        {loseCount > 0 && (
          <div className="flex justify-between">
            <span className="text-ink-text-3">翻车句</span>
            <span className="text-vivid-orange">{loseCount} 句</span>
          </div>
        )}
      </div>
    </GlassCard>
  )
}

// --- Center Panel: Turn-by-turn analysis ---

function CenterPanel({
  turns,
  expandedIdx,
  onToggle,
}: {
  turns: ReviewTurn[]
  expandedIdx: number | null
  onToggle: (idx: number | null) => void
}) {
  return (
    <GlassCard className="space-y-3">
      <h2 className="text-sm font-body font-medium text-ink-text-2">🔬 逐句分析</h2>
      <div className="space-y-2">
        {turns.map((turn) => {
          const cfg = VERDICT_CONFIG[turn.verdict]
          const isExpanded = expandedIdx === turn.turn_idx
          const isLose = turn.verdict === 'lose'

          return (
            <div key={turn.turn_idx} className="space-y-1">
              <button
                type="button"
                onClick={() => onToggle(isExpanded ? null : turn.turn_idx)}
                className={`w-full text-left rounded-radius-md px-3 py-2 text-sm font-body transition-colors ${
                  isLose
                    ? 'bg-vivid-orange/10 border border-vivid-orange/20 hover:bg-vivid-orange/20'
                    : turn.speaker === 'user'
                      ? 'bg-vivid-green/5 border border-vivid-green/10'
                      : 'bg-ink-card border border-ink-line'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1">
                    <span className="text-ink-text-3 text-xs">
                      {turn.speaker === 'opponent' ? '🤵 对方' : '👤 我'}
                    </span>
                    <p className="text-ink-text mt-0.5">{turn.content}</p>
                  </div>
                  <span className={`flex-shrink-0 text-xs ${cfg.color}`}>
                    {cfg.icon} {cfg.label}
                  </span>
                </div>
              </button>

              {/* Expanded: K's better suggestion */}
              {isExpanded && isLose && turn.reason && (
                <div className="ml-4 rounded-radius-md px-3 py-2 bg-vivid-purple/10 border border-vivid-purple/20">
                  <p className="text-xs text-vivid-purple font-body">
                    💡 K 说：{turn.reason}
                  </p>
                  {turn.better && (
                    <p className="text-xs text-ink-text font-body mt-1">
                      更佳话术：「{turn.better}」
                    </p>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </GlassCard>
  )
}

// --- Right Panel: Summary report ---

function RightPanel({
  summary,
}: {
  summary: ReviewUploadResponse['summary']
}) {
  if (!summary) {
    return (
      <GlassCard className="text-center">
        <p className="text-sm text-ink-text-3 font-body">暂无分析结果</p>
      </GlassCard>
    )
  }

  return (
    <GlassCard className="space-y-4">
      <h2 className="text-sm font-body font-medium text-ink-text-2">📊 战报</h2>

      {/* Score circle */}
      <div className="flex flex-col items-center">
        <div className="w-20 h-20 rounded-full border-4 border-vivid-purple flex items-center justify-center">
          <span className="text-2xl font-display text-vivid-purple italic">
            {summary.score}
          </span>
        </div>
        <span className="text-xs text-ink-text-3 font-body mt-1">总分 / 10</span>
      </div>

      {/* Top failures */}
      {summary.top_failures.length > 0 && (
        <div>
          <h3 className="text-xs font-body font-medium text-vivid-orange mb-2">
            💥 三大翻车
          </h3>
          <ol className="space-y-1">
            {summary.top_failures.map((f, i) => (
              <li key={i} className="text-sm text-ink-text font-body flex items-start gap-1.5">
                <span className="text-ink-text-3">{i + 1}.</span>
                {f}
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Improvements */}
      {summary.improvements.length > 0 && (
        <div>
          <h3 className="text-xs font-body font-medium text-vivid-green mb-2">
            ✨ 神回话术
          </h3>
          <ol className="space-y-1">
            {summary.improvements.map((s, i) => (
              <li key={i} className="text-sm text-ink-text font-body flex items-start gap-1.5">
                <span className="text-ink-text-3">{i + 1}.</span>
                {s}
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Training plan CTA */}
      <button
        type="button"
        className="w-full px-4 py-2 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform"
      >
        生成训练计划
      </button>
    </GlassCard>
  )
}
