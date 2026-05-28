/**
 * SandboxRoom — cyberpunk redesign (design-spec §9.3).
 *
 * Data layer untouched: same `useSandboxSession` + `useCustomScenario`
 * hooks, same SSE consumption + moderation + quiet-hours flows.
 *
 * Visual rebuild only:
 *   · Background: NeuralParticles + scanline + tech-grid (matches Home).
 *   · Scenario picker: 3 preset TiltCards + custom HudFrame textarea.
 *   · Active session: HUD top bar with turn meter, opponent bubbles in
 *     dark cyan-edged glass, user bubbles in magenta gradient, K avatar
 *     reflects current mascotExpression.
 *   · Input footer: cyber-glass strip + MagneticButton send.
 *   · Modals: HudFrame cards over deep-void backdrop.
 */

import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowLeft, Lock, Send, ShieldAlert, Sparkles, Swords } from 'lucide-react'

import { HintCardV2, MascotReaction } from '../../components'
import {
  CharacterRadar,
  GlowText,
  HudFrame,
  MagneticButton,
  MoodGauge,
  NeuralParticles,
  TiltCard,
} from '../../components/cyber'
import type { Score } from '../../api/v1/types'
import type { MascotExpression } from '../../components/mascot/types'
import { useCustomScenario } from './useCustomScenario'
import { useSandboxSession } from './useSandboxSession'

type ScoreExpression = 'godlike' | 'crashed' | 'confident'

function toScoreExpression(expr: MascotExpression): ScoreExpression {
  if (expr === 'godlike' || expr === 'crashed' || expr === 'confident') {
    return expr
  }
  return 'confident'
}

// PR-D4: was 30 — judges in a demo write a short phrase, not a paragraph.
// Lowering the gate to 6 catches "导师骂我怎么办" while still rejecting
// pure-garbage one-character inputs.
const CUSTOM_MIN_LENGTH = 6

// PR-D7: ids MUST match `SCENARIO_CATALOG` in
// `apps/api/app/services/scenarios/seed_data.py`. The earlier
// `scenario_*` strings landed on the backend's FALLBACK_RECORD
// (opening_line="我们来聊聊吧。") because they don't exist in the
// catalog dict. persona ids match `apps/api/app/services/personas/
// catalog.py` (`p_mild` / `p_hard` / `p_pua` / `p_passive_aggressive`)
// — currently informational only at the backend, but kept aligned so
// the next PR can wire it into the prompt builder.
const PRESETS = [
  {
    id: 'sc_001',
    persona: 'p_hard',
    goal: '拒绝加班且不撕破脸',
    title: '周末加班谈判',
    en: 'OVERTIME · REFUSE',
    color: '#FF2DAA',
  },
  {
    id: 'sc_002',
    persona: 'p_hard',
    goal: '薪资谈判不卑不亢',
    title: '实习转正谈薪资',
    en: 'SALARY · NEGOTIATE',
    color: '#00F0FF',
  },
  {
    id: 'sc_003',
    persona: 'p_passive_aggressive',
    goal: '让室友安静但不撕逼',
    title: '室友深夜打游戏',
    en: 'ROOMMATE · CONFLICT',
    color: '#B7FF00',
  },
] as const

interface SandboxRoomProps {
  onExit: () => void
  onScore: (score: Score, expression: ScoreExpression, sessionId: string | null) => void
}

export function SandboxRoom({ onExit, onScore }: SandboxRoomProps) {
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
  const { state: customState, generate: generateCustom } = useCustomScenario()

  const [input, setInput] = useState('')
  const [showExitConfirm, setShowExitConfirm] = useState(false)
  const [customDesc, setCustomDesc] = useState('')
  const chatEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [state.messages, state.streamingText])

  useEffect(() => {
    if (state.started && state.turnsLeft === 0 && !state.isStreaming && !state.score) {
      void endSession()
    }
  }, [state.turnsLeft, state.started, state.isStreaming, state.score, endSession])

  const handleStartPreset = (id: string, persona: string, goal: string) => {
    startSession({ mode: 'sandbox', scenario_id: id, persona_id: persona, user_goal: goal })
  }

  const handleStartCustom = async () => {
    const res = await generateCustom(customDesc)
    if (res) {
      startSession({
        mode: 'sandbox',
        scenario_id: res.scenario_id,
        persona_id: res.persona_title,
        user_goal: customDesc.trim(),
      })
    }
  }

  const handleSend = async () => {
    const text = input.trim()
    if (!text || state.isStreaming) {
      return
    }
    setInput('')
    await sendTurn(text)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handleSend()
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
  const turnDangerColor = state.turnsLeft <= 3 ? '#FF6B35' : '#00F0FF'

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
            'radial-gradient(ellipse at center, transparent 0%, rgba(5,5,5,0.5) 60%, rgba(5,5,5,0.92) 100%)',
        }}
      />

      {/* ── Scenario picker view ─────────────────────────────────── */}
      {!state.started && !state.score && (
        <div className="relative z-10 flex-1 overflow-y-auto px-6 py-8">
          <div className="mx-auto w-full max-w-3xl space-y-8">
            <div className="flex items-center justify-between">
              <button
                type="button"
                onClick={onExit}
                className="inline-flex items-center gap-1 font-bebas text-xs tracking-[0.24em] text-white/60 transition-colors hover:text-cyber-cyan"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                BACK
              </button>
              <MascotReaction expression="confident" size="sm" />
            </div>

            <div className="text-center">
              <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-cyber-cyan/30 bg-cyber-cyan/5 px-3 py-1">
                <Swords className="h-3 w-3 text-cyber-cyan" />
                <span className="font-bebas text-[11px] tracking-[0.28em] text-cyber-cyan">
                  MISSION · SELECT
                </span>
              </div>
              <h1 className="font-orbitron text-5xl font-black uppercase leading-none tracking-tight">
                <GlowText variant="gradient">SANDBOX</GlowText>
              </h1>
              <p className="mt-3 font-display text-xl italic text-white/80">
                选个对手开练。每局 30 回合，AI 实时打分。
              </p>
            </div>

            <section>
              <p className="mb-3 font-bebas text-[11px] tracking-[0.28em] text-white/50">
                PRESET · MISSIONS
              </p>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                {PRESETS.map((p, i) => (
                  <motion.div
                    key={p.id}
                    initial={{ opacity: 0, y: 16 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.4, delay: i * 0.08 }}
                  >
                    <TiltCard className="rounded-3xl">
                      <HudFrame
                        label={`MISSION · 0${i + 1}`}
                        tag="30R"
                        className="cyber-glass-edge rounded-3xl p-6"
                      >
                        <button
                          type="button"
                          onClick={() => handleStartPreset(p.id, p.persona, p.goal)}
                          className="flex h-full w-full flex-col items-start gap-3 text-left"
                        >
                          <span
                            className="font-bebas text-[11px] tracking-[0.24em]"
                            style={{ color: p.color }}
                          >
                            {p.en}
                          </span>
                          <p
                            className="font-orbitron text-xl font-bold uppercase tracking-tight"
                            style={{ color: p.color, textShadow: `0 0 12px ${p.color}66` }}
                          >
                            {p.title}
                          </p>
                          <p className="text-sm text-white/60">{p.goal}</p>
                          <span
                            className="mt-auto inline-flex items-center gap-1 font-bebas text-xs tracking-[0.2em] pt-3"
                            style={{ color: p.color }}
                          >
                            ENGAGE →
                          </span>
                        </button>
                      </HudFrame>
                    </TiltCard>
                  </motion.div>
                ))}
              </div>
            </section>

            <section>
              <p className="mb-3 font-bebas text-[11px] tracking-[0.28em] text-white/50">
                CUSTOM · MISSION
              </p>
              <HudFrame
                label="CUSTOM · INPUT"
                tag={`${customDesc.trim().length}/${CUSTOM_MIN_LENGTH}`}
                className="cyber-glass-edge rounded-3xl p-6"
              >
                <div className="space-y-3">
                  <textarea
                    value={customDesc}
                    onChange={e => setCustomDesc(e.target.value)}
                    placeholder="描述你想练的场景，比如「导师骂我怎么办」、「室友打游戏吵我睡觉」…"
                    rows={3}
                    className="font-grotesk w-full resize-none rounded-2xl border border-cyber-hairline bg-cyber-deep/60 px-4 py-3 text-sm text-white placeholder:text-white/30 focus:border-cyber-cyan focus:bg-cyber-deep/80 focus:outline-none focus:ring-2 focus:ring-cyber-cyan/30"
                  />
                  {customState.error && (
                    <p className="font-mono text-xs text-cyber-magenta">⚠ {customState.error}</p>
                  )}
                  <div className="flex items-center justify-between">
                    <p className="font-mono text-[10px] text-white/40">
                      {customDesc.trim().length < CUSTOM_MIN_LENGTH
                        ? `再写 ${CUSTOM_MIN_LENGTH - customDesc.trim().length} 个字就能开练`
                        : '场景就绪'}
                    </p>
                    <MagneticButton
                      type="button"
                      onClick={handleStartCustom}
                      disabled={
                        customState.isGenerating ||
                        customDesc.trim().length < CUSTOM_MIN_LENGTH
                      }
                    >
                      <Sparkles className="h-4 w-4" />
                      {customState.isGenerating ? '生成中…' : '生成并开始'}
                    </MagneticButton>
                  </div>
                </div>
              </HudFrame>
            </section>
          </div>
        </div>
      )}

      {/* ── Active session view ──────────────────────────────────── */}
      {state.started && (
        <>
          <header className="relative z-10 mx-auto flex w-full max-w-5xl items-center justify-between gap-4 px-6 pt-6">
            <button
              type="button"
              onClick={handleExitClick}
              className="inline-flex items-center gap-1 font-bebas text-xs tracking-[0.24em] text-white/60 transition-colors hover:text-cyber-magenta"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              EXIT
            </button>

            <div className="flex items-center gap-3">
              <MascotReaction expression={state.mascotExpression} size="sm" />
              <div>
                <p className="font-bebas text-[10px] tracking-[0.28em] text-cyber-cyan">
                  COMBAT · IN PROGRESS
                </p>
                <p className="font-orbitron text-sm font-semibold text-white">
                  ROUND {state.turnsUsed.toString().padStart(2, '0')} /{' '}
                  {totalTurns.toString().padStart(2, '0')}
                </p>
              </div>
            </div>

            <div className="flex flex-col items-end gap-1">
              <span className="font-mono text-[10px] text-white/50">TURNS LEFT</span>
              <div className="flex items-center gap-2">
                <span
                  className="font-orbitron text-lg font-bold"
                  style={{ color: turnDangerColor, textShadow: `0 0 8px ${turnDangerColor}88` }}
                >
                  {state.turnsLeft}
                </span>
                <div className="h-1 w-20 overflow-hidden rounded-full bg-cyber-hairline">
                  <div
                    className="h-full rounded-full transition-all duration-300"
                    style={{
                      width: `${turnProgress}%`,
                      background: turnDangerColor,
                      boxShadow: `0 0 8px ${turnDangerColor}`,
                    }}
                  />
                </div>
              </div>
            </div>
          </header>

          {state.error && (
            <div
              role="alert"
              className="relative z-10 mx-auto mt-4 flex w-full max-w-5xl items-center justify-between rounded-2xl border border-cyber-magenta/40 bg-cyber-magenta/10 px-4 py-2"
            >
              <span className="font-mono text-xs text-cyber-magenta">⚠ {state.error}</span>
              <button
                type="button"
                onClick={dismissError}
                aria-label="关闭"
                className="text-cyber-magenta/70 hover:text-cyber-magenta"
              >
                ×
              </button>
            </div>
          )}

          {state.turnsLeft > 0 && state.turnsLeft <= 3 && !state.score && (
            <div className="relative z-10 mx-auto mt-2 w-full max-w-5xl px-6 text-center">
              <span className="font-bebas text-[11px] tracking-[0.24em] text-cyber-amber animate-hud-flicker">
                ⚡ LOW · 还剩 {state.turnsLeft} 回合，抓紧表现
              </span>
            </div>
          )}

          {state.characterVector && (
            <div className="relative z-10 mx-auto mt-4 flex w-full max-w-5xl justify-end px-6">
              <HudFrame
                label="对手情绪"
                tag="LIVE MOOD"
                color="#00F0FF"
                className="w-48 px-3 py-3"
              >
                <CharacterRadar vector={state.characterVector} size={160} />
                <MoodGauge vector={state.characterVector} className="mt-1 px-1" />
              </HudFrame>
            </div>
          )}

          <main className="relative z-10 mx-auto mt-4 flex w-full max-w-5xl flex-1 flex-col gap-4 overflow-y-auto px-6 pb-32">
            {state.messages.map((msg, i) =>
              msg.role === 'opponent' ? (
                <OpponentBubble key={i} text={msg.text} />
              ) : (
                <UserBubble key={i} text={msg.text} />
              ),
            )}
            {state.isStreaming && state.streamingText && (
              <OpponentBubble text={state.streamingText} isStreaming />
            )}
            {state.isStreaming && !state.streamingText && <OpponentTyping />}

            {state.hints && (
              <div className="mt-2">
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

            {state.score && (
              <div className="mt-6 flex justify-center">
                <MagneticButton
                  type="button"
                  variant="lime"
                  onClick={() =>
                    onScore(
                      state.score!.score,
                      toScoreExpression(state.mascotExpression),
                      state.sessionId,
                    )
                  }
                >
                  <Sparkles className="h-4 w-4" />
                  查看评分
                </MagneticButton>
              </div>
            )}

            {state.started && !state.score && (
              <div className="mt-2 flex justify-center">
                <button
                  type="button"
                  onClick={() => void endSession()}
                  disabled={state.isStreaming}
                  className="inline-flex items-center gap-1 rounded-full border border-cyber-hairline px-4 py-1.5 font-bebas text-[11px] tracking-[0.24em] text-white/60 transition-colors hover:border-cyber-magenta/40 hover:text-cyber-magenta disabled:opacity-50"
                >
                  END · COMBAT
                </button>
              </div>
            )}

            <div ref={chatEndRef} />
          </main>

          <footer className="fixed bottom-0 left-0 right-0 z-20">
            <div className="mx-auto w-full max-w-5xl px-6 pb-6">
              <div className="cyber-glass-edge flex items-center gap-3 rounded-pill border border-cyber-hairline p-2 pl-5">
                <input
                  type="text"
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={state.isStreaming}
                  placeholder="说点什么…（Enter 发送）"
                  className="font-grotesk flex-1 bg-transparent text-sm text-white placeholder:text-white/30 focus:outline-none disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={handleSend}
                  disabled={state.isStreaming || !input.trim()}
                  aria-label="发送"
                  className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-br from-cyber-cyan via-vivid-purple to-cyber-magenta text-white shadow-[0_0_18px_rgba(0,240,255,0.5)] transition-transform hover:scale-105 disabled:opacity-50 disabled:hover:scale-100"
                >
                  <Send className="h-4 w-4" />
                </button>
              </div>
            </div>
          </footer>
        </>
      )}

      {/* ── Modals ───────────────────────────────────────────────── */}
      {showExitConfirm && (
        <Modal>
          <HudFrame
            label="EXIT · CONFIRM"
            tag="!"
            className="cyber-glass-edge mx-4 w-full max-w-sm rounded-3xl p-8 text-center"
          >
            <MascotReaction expression="caring" size="md" />
            <p className="mt-4 font-display text-xl italic text-white">确定要退出对练吗？</p>
            <p className="mt-2 font-mono text-xs text-white/50">CURRENT PROGRESS · NOT SAVED</p>
            <div className="mt-6 flex justify-center gap-3">
              <button
                type="button"
                onClick={() => setShowExitConfirm(false)}
                className="rounded-full border border-cyber-hairline px-5 py-2 font-bebas text-xs tracking-[0.22em] text-white/80 hover:border-cyber-cyan/40 hover:text-cyber-cyan"
              >
                CONTINUE
              </button>
              <MagneticButton type="button" variant="magenta" onClick={handleExitConfirm}>
                CONFIRM EXIT
              </MagneticButton>
            </div>
          </HudFrame>
        </Modal>
      )}

      {state.isQuietHours && (
        <Modal>
          <HudFrame
            label="QUIET · HOURS"
            tag="22:00 - 08:00"
            className="cyber-glass-edge mx-4 w-full max-w-sm rounded-3xl p-8 text-center"
          >
            <MascotReaction expression="caring" size="md" />
            <p className="mt-4 font-display text-xl italic text-white">现在是静默时段</p>
            <p className="mt-2 text-sm text-white/60">
              为保护未成年人，22:00-08:00 期间无法使用对练功能
            </p>
            <div className="mt-6 flex justify-center">
              <MagneticButton
                type="button"
                onClick={() => {
                  dismissQuietHours()
                  onExit()
                }}
              >
                <Lock className="h-4 w-4" />
                我知道了
              </MagneticButton>
            </div>
          </HudFrame>
        </Modal>
      )}

      {state.redirectResource && (
        <Modal>
          <HudFrame
            label="SAFETY · REDIRECT"
            tag="§3.0.5"
            className="cyber-glass-edge mx-4 w-full max-w-sm rounded-3xl p-8 text-center"
          >
            <MascotReaction expression="caring" size="md" />
            <p className="mt-4 font-display text-xl italic text-white">
              {state.redirectResource.title}
            </p>
            <div className="mt-6 flex justify-center">
              <MagneticButton type="button" variant="magenta">
                <a
                  href={state.redirectResource.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2"
                >
                  <ShieldAlert className="h-4 w-4" />
                  查看求助资源
                </a>
              </MagneticButton>
            </div>
            <button
              type="button"
              onClick={dismissModeration}
              className="mt-4 font-bebas text-[11px] tracking-[0.24em] text-white/50 hover:text-white/80"
            >
              CLOSE
            </button>
          </HudFrame>
        </Modal>
      )}
    </div>
  )
}

/* ── Local presentational helpers ─────────────────────────────────── */

function OpponentBubble({ text, isStreaming }: { text: string; isStreaming?: boolean }) {
  return (
    <div className="flex max-w-[80%] animate-slide-in-left items-start gap-2">
      <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full border border-cyber-hairline bg-cyber-deep/80 text-base shadow-[0_0_12px_rgba(255,45,170,0.25)]">
        👔
      </div>
      <div className="cyber-glass rounded-3xl rounded-tl-md border border-cyber-magenta/25 px-5 py-3 shadow-[0_0_18px_rgba(255,45,170,0.15)]">
        <p className="font-grotesk text-sm leading-relaxed text-white">
          {text}
          {isStreaming && (
            <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-cyber-magenta align-text-bottom" />
          )}
        </p>
      </div>
    </div>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="ml-auto flex max-w-[80%] animate-slide-in-right flex-row-reverse items-start gap-2">
      <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-cyber-cyan via-vivid-purple to-cyber-magenta text-xs font-bold text-white shadow-[0_0_12px_rgba(0,240,255,0.45)]">
        我
      </div>
      <div className="rounded-3xl rounded-tr-md bg-gradient-to-br from-cyber-cyan/80 via-vivid-purple to-cyber-magenta/90 px-5 py-3 shadow-[0_0_20px_rgba(108,77,255,0.45)]">
        <p className="font-grotesk text-sm leading-relaxed text-white">{text}</p>
      </div>
    </div>
  )
}

function OpponentTyping() {
  return (
    <div className="flex max-w-[80%] items-start gap-2">
      <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full border border-cyber-hairline bg-cyber-deep/80 text-base">
        👔
      </div>
      <div className="cyber-glass rounded-3xl rounded-tl-md border border-cyber-magenta/25 px-5 py-4">
        <div className="flex gap-1">
          <span
            className="h-2 w-2 animate-bounce rounded-full bg-cyber-magenta/80"
            style={{ animationDelay: '0ms' }}
          />
          <span
            className="h-2 w-2 animate-bounce rounded-full bg-cyber-magenta/80"
            style={{ animationDelay: '150ms' }}
          />
          <span
            className="h-2 w-2 animate-bounce rounded-full bg-cyber-magenta/80"
            style={{ animationDelay: '300ms' }}
          />
        </div>
      </div>
    </div>
  )
}

function Modal({ children }: { children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-cyber-void/80 backdrop-blur-md">
      {children}
    </div>
  )
}
