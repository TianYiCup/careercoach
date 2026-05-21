/**
 * 沙盘对练房 — design-spec §9.3
 * D6-B: SSE 流式交互 + 流式光标 + 自动滚动 + 回合计数器
 * H-1: moderation redirect/block 帧消费
 */

import { useState, useRef, useEffect } from 'react'
import { BlobBackground, GlassCard, MascotReaction, HintCardV2 } from '../../components'
import { useSandboxSession } from './useSandboxSession'
import type { Score } from '../../api/v1/types'
import type { MascotExpression } from '../../components/mascot/types'

/** Narrow MascotExpression to the 3 expressions that make sense on the score page */
function toScoreExpression(expr: MascotExpression): 'godlike' | 'crashed' | 'confident' {
  if (expr === 'godlike' || expr === 'crashed' || expr === 'confident') return expr
  return 'confident'
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

export function SandboxRoom({
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
    dismissModeration,
  } = useSandboxSession()

  const [input, setInput] = useState('')
  const [showExitConfirm, setShowExitConfirm] = useState(false)
  const chatEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when messages or streaming text change
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [state.messages, state.streamingText])

  // Auto-start a session on mount
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

  const handleExitClick = () => {
    if (state.started && !state.score) {
      setShowExitConfirm(true)
    } else {
      onExit()
    }
  }

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

      {/* Header */}
      <header className="relative z-10 flex items-center justify-between px-4 py-3 glass">
        <button type="button" onClick={handleExitClick} className="text-ink-text-2 text-sm hover:text-ink-text transition-colors">
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
          <span className="text-xs text-ink-text-3">{state.turnsUsed}/{totalTurns} 回合</span>
          <div className="w-12 h-1 rounded-full bg-ink-line overflow-hidden">
            <div className="h-full rounded-full transition-all duration-300" style={{ width: `${turnProgress}%`, background: state.turnsLeft <= 3 ? 'var(--color-vivid-orange)' : 'var(--color-vivid-purple)' }} />
          </div>
        </div>
      </header>

      {/* Error banner */}
      {state.error && (
        <div className="relative z-10 flex items-center justify-between px-4 py-2 bg-vivid-orange/15 border-b border-vivid-orange/40" role="alert">
          <span className="text-sm text-vivid-orange font-body">{state.error}</span>
          <button type="button" onClick={dismissError} aria-label="关闭" className="text-vivid-orange/80 hover:text-vivid-orange text-lg leading-none px-2">×</button>
        </div>
      )}

      {/* Turn limit warning */}
      {state.turnsLeft > 0 && state.turnsLeft <= 3 && !state.score && (
        <div className="relative z-10 px-4 py-1.5 bg-vivid-orange/10 border-b border-vivid-orange/30 text-center">
          <span className="text-xs text-vivid-orange font-body">还剩 {state.turnsLeft} 回合，抓紧表现！</span>
        </div>
      )}

      {/* Chat area */}
      <main className="relative z-10 flex-1 overflow-y-auto px-4 py-6 space-y-4">
        {state.messages.map((msg, i) =>
          msg.role === 'opponent' ? <OpponentBubble key={i} text={msg.text} /> : <UserBubble key={i} text={msg.text} />,
        )}
        {state.isStreaming && state.streamingText && <OpponentBubble text={state.streamingText} isStreaming />}
        {state.isStreaming && !state.streamingText && (
          <div className="flex items-start gap-2 max-w-[80%]">
            <div className="w-8 h-8 rounded-full bg-ink-card-2 flex items-center justify-center text-xs flex-shrink-0">👔</div>
            <div className="rounded-radius-md px-4 py-3 bg-ink-card-2 border border-ink-line">
              <div className="flex gap-1">
                <span className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          </div>
        )}
        {state.hints && (
          <div className="mt-4">
            <HintCardV2 hint={state.activeTone === 'safe' ? state.hints.safe : state.activeTone === 'fun' ? state.hints.humor : state.hints.aggressive} activeTone={state.activeTone} onToneChange={setTone} />
          </div>
        )}
        {state.score && (
          <div className="flex justify-center mt-6">
            <button type="button" onClick={() => onScore(state.score!.score, toScoreExpression(state.mascotExpression), state.sessionId)} className="px-6 py-3 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform">查看评分</button>
          </div>
        )}
        {state.started && !state.score && (
          <div className="flex justify-center mt-4">
            <button type="button" onClick={endSession} disabled={state.isStreaming} className="px-5 py-2 rounded-radius-pill bg-ink-card/60 border border-ink-line text-ink-text-2 text-sm font-body hover:bg-ink-card transition-colors disabled:opacity-50">结束对练</button>
          </div>
        )}
        <div ref={chatEndRef} />
      </main>

      {/* Input footer */}
      {state.started && !state.score && (
        <footer className="relative z-10 glass px-4 py-3">
          <div className="flex items-center gap-2">
            <input type="text" value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={handleKeyDown} disabled={state.isStreaming} placeholder="说点什么..." className="flex-1 rounded-radius-pill bg-ink-card px-4 py-2 text-sm text-ink-text font-body border border-ink-line focus:border-vivid-purple focus:outline-none transition-colors disabled:opacity-50" />
            <button type="button" onClick={handleSend} disabled={state.isStreaming || !input.trim()} className="w-10 h-10 rounded-full gradient-vivid flex items-center justify-center text-white text-lg hover:scale-105 transition-transform disabled:opacity-50 disabled:hover:scale-100" aria-label="发送">🎤</button>
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
              <button type="button" onClick={() => setShowExitConfirm(false)} className="px-5 py-2 rounded-radius-pill bg-ink-card border border-ink-line text-ink-text text-sm font-body hover:bg-ink-card-2 transition-colors">继续练</button>
              <button type="button" onClick={handleExitConfirm} className="px-5 py-2 rounded-radius-pill bg-vivid-orange/20 border border-vivid-orange/40 text-vivid-orange text-sm font-body hover:bg-vivid-orange/30 transition-colors">确定退出</button>
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
            <p className="text-sm text-ink-text-2">为保护未成年人，22:00-08:00 期间无法使用对练功能</p>
            <div className="flex gap-3 justify-center pt-2">
              <button type="button" onClick={() => { dismissQuietHours(); onExit() }} className="px-5 py-2 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform">我知道了</button>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Moderation redirect — crisis hotline. PRD §3.0.5 red lines. */}
      {state.redirectResource && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-bg/70 backdrop-blur-sm">
          <GlassCard className="mx-4 max-w-sm w-full space-y-4 text-center">
            <MascotReaction expression="caring" size="md" showLabel />
            <p className="text-lg font-body text-ink-text">{state.redirectResource.title}</p>
            <a href={state.redirectResource.url} target="_blank" rel="noopener noreferrer" className="inline-block px-5 py-2 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform">查看求助资源</a>
            <button type="button" onClick={dismissModeration} className="block mx-auto text-sm text-ink-text-2 hover:text-ink-text transition-colors">关闭</button>
          </GlassCard>
        </div>
      )}
    </div>
  )
}
