import { useState, useRef, useEffect } from 'react'
import {
  BlobBackground,
  GlassCard,
  MascotReaction,
  HintCardV2,
  VibePill,
  StreakFire,
  StickerBadge,
  WrappedCard,
} from './components'

import { useSandboxSession } from './features/sandbox/useSandboxSession'
import { ScorePage } from './features/sandbox/ScorePage'
import { useShareCard } from './features/sandbox/useShareCard'
import type { Score } from './api/v1/types'
import type { MascotExpression } from './components/mascot/types'
import { AuthProvider, LoginPage, AgeGatePage, useAuth } from './features/auth'
import { ReviewUploadPage } from './features/review/ReviewUploadPage'
import { ReviewResultPage } from './features/review/ReviewResultPage'
import { CopilotPage } from './features/copilot'
import { WeaknessProfilePage } from './features/weakness'

type Page = 'home' | 'sandbox' | 'copilot' | 'wrapped' | 'score' | 'reviewUpload' | 'reviewResult' | 'weakness'

/** Narrow MascotExpression to the 3 expressions that make sense on the score page */
function toScoreExpression(expr: MascotExpression): 'godlike' | 'crashed' | 'confident' {
  if (expr === 'godlike' || expr === 'crashed' || expr === 'confident') return expr
  return 'confident'
}

/**
 * 沙盘对练房 — design-spec §9.3
 * D6-B: SSE 流式交互 + 流式光标 + 自动滚动 + 回合计数器
 */
function SandboxRoom({
  onExit,
  onScore,
}: {
  onExit: () => void
  onScore: (score: Score, expression: 'godlike' | 'crashed' | 'confident', sessionId: string | null) => void
}) {
  const {
    state,
    startSession,
    sendTurn,
    endSession,
    setTone,
    dismissError,
    dismissQuietHours,
  } = useSandboxSession()

  const [input, setInput] = useState('')
  const [showExitConfirm, setShowExitConfirm] = useState(false)
  const chatEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Auto-scroll to bottom when messages or streaming text change
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [state.messages, state.streamingText])

  // Auto-start a session on mount (MSW mock will respond)
  useEffect(() => {
    startSession({
      mode: 'sandbox',
      scenario_id: 'scenario_campus_overtime',
      persona_id: 'boss_strict',
      user_goal: '拒绝加班且不撕破脸',
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 30-round limit: auto-end when turns run out
  useEffect(() => {
    if (state.started && state.turnsLeft === 0 && !state.isStreaming && !state.score) {
      endSession()
    }
  }, [state.turnsLeft, state.started, state.isStreaming, state.score, endSession])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || state.isStreaming) return
    setInput('')
    await sendTurn(text)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  /** Handle exit: if session active, show confirm; otherwise go back */
  const handleExitClick = () => {
    if (state.started && !state.score) {
      setShowExitConfirm(true)
    } else {
      onExit()
    }
  }

  /** Confirm exit → end session + go back */
  const handleExitConfirm = async () => {
    setShowExitConfirm(false)
    await endSession()
    onExit()
  }

  const totalTurns = state.turnsUsed + state.turnsLeft
  const turnProgress = totalTurns > 0 ? (state.turnsUsed / totalTurns) * 100 : 0

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden">
      <BlobBackground />

      {/* Header — design-spec §9.3 */}
      <header className="relative z-10 flex items-center justify-between px-4 py-3 glass">
        <button
          type="button"
          onClick={handleExitClick}
          className="text-ink-text-2 text-sm hover:text-ink-text transition-colors"
        >
          ← 退出
        </button>
        <div className="flex items-center gap-2">
          <MascotReaction expression={state.mascotExpression} size="sm" />
          <div className="flex flex-col">
            <span className="text-sm font-body text-ink-text leading-tight">赵总（刚）</span>
            <span className="text-xs text-ink-text-3 leading-tight">拒绝加班</span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-0.5">
          <span className="text-xs text-ink-text-3">
            {state.turnsUsed}/{totalTurns} 回合
          </span>
          <div className="w-12 h-1 rounded-full bg-ink-line overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{
                width: `${turnProgress}%`,
                background: state.turnsLeft <= 3
                  ? 'var(--color-vivid-orange)'
                  : 'var(--color-vivid-purple)',
              }}
            />
          </div>
        </div>
      </header>

      {/* Error banner — surfaces non-401 startSession/endSession failures.
          401s never reach here; AuthProvider intercepts and routes to LoginPage. */}
      {state.error && (
        <div
          className="relative z-10 flex items-center justify-between px-4 py-2 bg-vivid-orange/15 border-b border-vivid-orange/40"
          role="alert"
        >
          <span className="text-sm text-vivid-orange font-body">
            {state.error}
          </span>
          <button
            type="button"
            onClick={dismissError}
            aria-label="关闭"
            className="text-vivid-orange/80 hover:text-vivid-orange text-lg leading-none px-2"
          >
            ×
          </button>
        </div>
      )}

      {/* Turn limit warning */}
      {state.turnsLeft > 0 && state.turnsLeft <= 3 && !state.score && (
        <div className="relative z-10 px-4 py-1.5 bg-vivid-orange/10 border-b border-vivid-orange/30 text-center">
          <span className="text-xs text-vivid-orange font-body">
            还剩 {state.turnsLeft} 回合，抓紧表现！
          </span>
        </div>
      )}

      {/* Chat area */}
      <main className="relative z-10 flex-1 overflow-y-auto px-4 py-6 space-y-4">
        {state.messages.map((msg, i) =>
          msg.role === 'opponent' ? (
            <OpponentBubble key={i} text={msg.text} />
          ) : (
            <UserBubble key={i} text={msg.text} />
          ),
        )}

        {/* Streaming opponent text + blinking cursor */}
        {state.isStreaming && state.streamingText && (
          <OpponentBubble text={state.streamingText} isStreaming />
        )}

        {/* Three-dot typing indicator (before first delta arrives) */}
        {state.isStreaming && !state.streamingText && (
          <div className="flex items-start gap-2 max-w-[80%]">
            <div className="w-8 h-8 rounded-full bg-ink-card-2 flex items-center justify-center text-xs flex-shrink-0">
              👔
            </div>
            <div className="rounded-radius-md px-4 py-3 bg-ink-card-2 border border-ink-line">
              <div className="flex gap-1">
                <span
                  className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce"
                  style={{ animationDelay: '0ms' }}
                />
                <span
                  className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce"
                  style={{ animationDelay: '150ms' }}
                />
                <span
                  className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce"
                  style={{ animationDelay: '300ms' }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Coach K hint card */}
        {state.hints && (
          <div className="mt-4">
            <HintCardV2
              hint={
                state.activeTone === 'safe'
                  ? state.hints.safe
                  : state.activeTone === 'fun'
                    ? state.hints.humor
                    : state.hints.aggressive
              }
              activeTone={state.activeTone}
              onToneChange={setTone}
            />
          </div>
        )}

        {/* Score result → navigate to score page */}
        {state.score && (
          <div className="flex justify-center mt-6">
            <button
              type="button"
              onClick={() => onScore(state.score!.score, toScoreExpression(state.mascotExpression), state.sessionId)}
              className="px-6 py-3 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform"
            >
              查看评分
            </button>
          </div>
        )}

        {/* End session button */}
        {state.started && !state.score && (
          <div className="flex justify-center mt-4">
            <button
              type="button"
              onClick={endSession}
              disabled={state.isStreaming}
              className="px-5 py-2 rounded-radius-pill bg-ink-card/60 border border-ink-line text-ink-text-2 text-sm font-body hover:bg-ink-card transition-colors disabled:opacity-50"
            >
              结束对练
            </button>
          </div>
        )}

        <div ref={chatEndRef} />
      </main>

      {/* Input footer */}
      {state.started && !state.score && (
        <footer className="relative z-10 glass px-4 py-3">
          <div className="flex items-center gap-2">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={state.isStreaming}
              placeholder="说点什么..."
              className="flex-1 rounded-radius-pill bg-ink-card px-4 py-2 text-sm text-ink-text font-body border border-ink-line focus:border-vivid-purple focus:outline-none transition-colors disabled:opacity-50"
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={state.isStreaming || !input.trim()}
              className="w-10 h-10 rounded-full gradient-vivid flex items-center justify-center text-white text-lg hover:scale-105 transition-transform disabled:opacity-50 disabled:hover:scale-100"
              aria-label="发送"
            >
              🎤
            </button>
          </div>
        </footer>
      )}

      {/* Exit confirmation modal */}
      {showExitConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-bg/70 backdrop-blur-sm">
          <GlassCard className="mx-4 max-w-sm w-full space-y-4 text-center">
            <MascotReaction expression="caring" size="md" showLabel />
            <p className="text-lg font-body text-ink-text">确定要退出对练吗？</p>
            <p className="text-sm text-ink-text-2">当前进度不会被保存</p>
            <div className="flex gap-3 justify-center pt-2">
              <button
                type="button"
                onClick={() => setShowExitConfirm(false)}
                className="px-5 py-2 rounded-radius-pill bg-ink-card border border-ink-line text-ink-text text-sm font-body hover:bg-ink-card-2 transition-colors"
              >
                继续练
              </button>
              <button
                type="button"
                onClick={handleExitConfirm}
                className="px-5 py-2 rounded-radius-pill bg-vivid-orange/20 border border-vivid-orange/40 text-vivid-orange text-sm font-body hover:bg-vivid-orange/30 transition-colors"
              >
                确定退出
              </button>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Minor quiet hours modal — PRD §3.0.5 C */}
      {state.isQuietHours && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-bg/70 backdrop-blur-sm">
          <GlassCard className="mx-4 max-w-sm w-full space-y-4 text-center">
            <MascotReaction expression="caring" size="md" showLabel />
            <p className="text-lg font-body text-ink-text">现在是静默时段</p>
            <p className="text-sm text-ink-text-2">
              为保护未成年人，22:00-08:00 期间无法使用对练功能
            </p>
            <div className="flex gap-3 justify-center pt-2">
              <button
                type="button"
                onClick={() => {
                  dismissQuietHours()
                  onExit()
                }}
                className="px-5 py-2 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform"
              >
                我知道了
              </button>
            </div>
          </GlassCard>
        </div>
      )}
    </div>
  )
}

/** Opponent chat bubble */
function OpponentBubble({
  text,
  isStreaming,
}: {
  text: string
  isStreaming?: boolean
}) {
  return (
    <div className="flex items-start gap-2 max-w-[80%] animate-slide-in-left">
      <div className="w-8 h-8 rounded-full bg-ink-card-2 flex items-center justify-center text-xs flex-shrink-0">
        👔
      </div>
      <div className="rounded-radius-md rounded-tl-none px-4 py-3 bg-ink-card-2 border border-ink-line">
        <p className="text-sm text-ink-text font-body">
          {text}
          {isStreaming && (
            <span className="inline-block w-0.5 h-4 ml-0.5 bg-vivid-purple animate-pulse align-text-bottom" />
          )}
        </p>
      </div>
    </div>
  )
}

/** User chat bubble */
function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2 max-w-[80%] ml-auto flex-row-reverse animate-slide-in-right">
      <div className="w-8 h-8 rounded-full gradient-vivid flex items-center justify-center text-xs flex-shrink-0">
        我
      </div>
      <div className="rounded-radius-md rounded-tr-none px-4 py-3 gradient-vivid">
        <p className="text-sm text-white font-body">{text}</p>
      </div>
    </div>
  )
}

/** Wrapped 卡演示页 — design-spec §10
 *  D14-B: 三种分享卡模式 (session / weekly / wrapped)
 *  + useShareCard 服务端生成 + WrappedCard Canvas 端渲染
 */
function WrappedPage({ onBack }: { onBack: () => void }) {
  const [activeTab, setActiveTab] = useState<'weekly' | 'wrapped'>('weekly')
  const {
    state: shareState,
    generateWeeklyCard,
    generateWrappedCard,
    dismissError,
    reset,
  } = useShareCard()

  const currentYear = new Date().getFullYear()

  /** Generate card based on active tab */
  const handleGenerate = async () => {
    if (activeTab === 'weekly') {
      await generateWeeklyCard({ include_qrcode: false })
    } else {
      await generateWrappedCard(currentYear, { include_qrcode: true })
    }
  }

  /** Download the server-rendered PNG */
  const handleDownloadServerPng = () => {
    if (!shareState.card?.png_url) return
    const link = document.createElement('a')
    link.href = shareState.card.png_url
    link.download = `careercoach-${activeTab}-${Date.now()}.png`
    link.target = '_blank'
    link.click()
  }

  return (
    <div className="relative min-h-screen flex flex-col items-center px-4 py-12 overflow-y-auto">
      <BlobBackground />

      <div className="relative z-10 w-full max-w-md space-y-6 text-center">
        {/* Header */}
        <button
          type="button"
          onClick={() => {
            reset()
            onBack()
          }}
          className="self-start text-ink-text-2 text-sm hover:text-ink-text transition-colors"
        >
          &larr; 返回
        </button>
        <h1 className="text-3xl font-display text-ink-text mb-1">Wrapped 战报</h1>
        <p className="text-sm text-ink-text-2">生成你的专属分享卡</p>

        {/* Tab switcher */}
        <div className="flex justify-center gap-3">
          <button
            type="button"
            onClick={() => { setActiveTab('weekly'); reset() }}
            className={`px-5 py-2 rounded-radius-pill font-body text-sm transition-colors ${
              activeTab === 'weekly'
                ? 'gradient-vivid text-white font-medium'
                : 'bg-ink-card border border-ink-line text-ink-text-2 hover:bg-ink-card-2'
            }`}
          >
            周报
          </button>
          <button
            type="button"
            onClick={() => { setActiveTab('wrapped'); reset() }}
            className={`px-5 py-2 rounded-radius-pill font-body text-sm transition-colors ${
              activeTab === 'wrapped'
                ? 'gradient-vivid text-white font-medium'
                : 'bg-ink-card border border-ink-line text-ink-text-2 hover:bg-ink-card-2'
            }`}
          >
            {currentYear} 年报
          </button>
        </div>

        {/* Generate button */}
        {!shareState.card && (
          <GlassCard className="space-y-4">
            <p className="text-sm text-ink-text-2 font-body">
              {activeTab === 'weekly'
                ? '回顾你这周的对练表现，生成周报战报卡'
                : `${currentYear} 年度 Wrapped，看看你的沟通成长轨迹`}
            </p>
            <button
              type="button"
              onClick={handleGenerate}
              disabled={shareState.isGenerating}
              className="px-6 py-2.5 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform disabled:opacity-50 glow-purple"
            >
              {shareState.isGenerating ? '生成中...' : `生成${activeTab === 'weekly' ? '周报' : '年报'}卡`}
            </button>
          </GlassCard>
        )}

        {/* Error banner */}
        {shareState.error && (
          <div className="flex items-center justify-between px-4 py-2 bg-vivid-orange/15 border border-vivid-orange/40 rounded-radius-md">
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

        {/* Card result — Canvas preview + server PNG */}
        {shareState.card && (
          <GlassCard glow className="space-y-4">
            <p className="text-sm text-ink-text-2 font-body">
              {activeTab === 'weekly' ? '本周战报' : `${currentYear} Wrapped`}
            </p>

            {/* Client-side Canvas demo card */}
            <WrappedCard
              score={activeTab === 'weekly' ? 7.5 : 8.2}
              comment={activeTab === 'weekly' ? '这周表现可圈可点' : '今年的你，我都服了'}
              gradient={activeTab === 'weekly' ? 'vivid' : 'glory'}
              expression={activeTab === 'weekly' ? '😎' : '✨'}
              badges={activeTab === 'weekly' ? ['气场+1', '逻辑+1'] : ['封神时刻', '气场+1', '共情+1']}
            />

            {/* Server-rendered PNG download */}
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

            {/* Wrapped multi-page indicator */}
            {activeTab === 'wrapped' && shareState.card.pages.length > 1 && (
              <p className="text-xs text-ink-text-3 font-body">
                共 {shareState.card.pages.length} 页（首页为封面）
              </p>
            )}
          </GlassCard>
        )}
      </div>
    </div>
  )
}

/** 首页 — design-spec §9.2 */
function HomePage({
  onNavigate,
}: {
  onNavigate: (page: Page) => void
}) {
  const { user, logout } = useAuth()
  const isMinor = user?.is_minor ?? false
  return (
    <div className="relative min-h-screen flex flex-col items-center px-4 py-12 overflow-hidden">
      <BlobBackground />

      {/* Top-right: nickname + 退出 */}
      {user && (
        <div className="relative z-10 self-end flex items-center gap-3 text-xs font-body animate-fade-in">
          <span className="text-ink-text-2">{user.nickname}</span>
          <button
            type="button"
            onClick={logout}
            className="text-ink-text-3 hover:text-ink-text-2 transition-colors"
          >
            退出
          </button>
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
          <VibePill vibe="燃爆" />
          <VibePill vibe="雄心勃勃" />
          <VibePill vibe="佛系" />
        </div>
        <div className="mt-3 flex justify-center">
          <StreakFire days={12} />
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
            {/* PRD §1.5: minors cannot use copilot (real-time audio recording) */}
            {isMinor ? (
              <span
                className="px-6 py-3 rounded-radius-md bg-ink-card/40 border border-ink-line text-ink-text-3 font-body font-medium cursor-not-allowed"
                title="未成年人暂不开放此功能"
              >
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

/**
 * AppGate — auth boundary. Renders <LoginPage> until the user has a
 * token (per useAuth.isAuthenticated). After login the provider
 * re-renders and we drop into the real navigation below.
 *
 * Routing is a tiny union ('home' | 'sandbox' | 'wrapped') instead of
 * react-router for now — the surface is three pages and the gate is
 * one bit. We'll bring in router when the route count grows.
 */
function AppGate() {
  const { isAuthenticated, needsAge } = useAuth()
  const [page, setPage] = useState<Page>('home')
  const [scoreData, setScoreData] = useState<{
    score: Score
    expression: 'godlike' | 'crashed' | 'confident'
    sessionId: string | null
  } | null>(null)
  const [reviewUploadId, setReviewUploadId] = useState<string | null>(null)

  if (!isAuthenticated) return <LoginPage />
  if (needsAge) return <AgeGatePage />

  const handleScore = (score: Score, expression: 'godlike' | 'crashed' | 'confident', sessionId: string | null) => {
    setScoreData({ score, expression, sessionId })
    setPage('score')
  }

  return (
    <>
      {page === 'home' && <HomePage onNavigate={setPage} />}
      {page === 'sandbox' && (
        <SandboxRoom
          onExit={() => setPage('home')}
          onScore={handleScore}
        />
      )}
      {page === 'copilot' && (
        <CopilotPage onBack={() => setPage('home')} />
      )}
      {page === 'weakness' && (
        <WeaknessProfilePage onBack={() => setPage('home')} />
      )}
      {page === 'wrapped' && <WrappedPage onBack={() => setPage('home')} />}
      {page === 'reviewUpload' && (
        <ReviewUploadPage
          onResult={(uploadId) => {
            setReviewUploadId(uploadId)
            setPage('reviewResult')
          }}
          onBack={() => setPage('home')}
        />
      )}
      {page === 'reviewResult' && reviewUploadId && (
        <ReviewResultPage
          uploadId={reviewUploadId}
          onBack={() => setPage('home')}
        />
      )}
      {page === 'score' && scoreData && (
        <ScorePage
          score={scoreData.score}
          mascotExpression={scoreData.expression}
          sessionId={scoreData.sessionId ?? undefined}
          onBack={() => {
            setScoreData(null)
            setPage('home')
          }}
        />
      )}
    </>
  )
}

function App() {
  return (
    <AuthProvider>
      <AppGate />
    </AuthProvider>
  )
}

export default App
