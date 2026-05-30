/**
 * CopilotPage — cyberpunk redesign (design-spec §9.5).
 *
 * Data layer untouched: `useCopilotSession` hook, ToneLevel, scenario
 * picker shape, HintCardV2 reuse, MascotReaction tied to state.
 *
 * Visual rebuild only — split into ScenarioPicker (entry) and HUD (live):
 *   · Both phases sit over NeuralParticles + tech-grid + scanline.
 *   · ScenarioPicker: 4 cyber TiltCard mission tiles + custom HudFrame.
 *   · HUD: dark transcript card cyan-edged + main hint HudFrame coloured
 *     by current tone (safe→cyan / aggro→magenta / fun→amber) + tone
 *     switcher (HintCardV2) + 停止 MagneticButton magenta.
 */

import { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import {
  ArrowLeft,
  BookOpen,
  Briefcase,
  Headphones,
  MicOff,
  ShieldAlert,
  ShoppingBag,
  UserX,
  Volume2,
  VolumeX,
  Zap,
} from 'lucide-react'

import { HintCardV2, MascotReaction } from '../../components'
import type { ToneLevel } from '../../components'
import {
  GlowText,
  HudFrame,
  MagneticButton,
  NeuralParticles,
  TiltCard,
} from '../../components/cyber'
import type { CreateCopilotSessionRequest } from '../../api/v1/types'
import { useCopilotSession } from './useCopilotSession'
import { useHintTts } from './useHintTts'

interface ScenarioOption {
  id: string
  label: string
  en: string
  icon: typeof Briefcase
  color: string
  scenarioHint: string
}

const SCENARIOS: ScenarioOption[] = [
  {
    id: 'salary',
    label: '面试谈薪',
    en: 'SALARY · NEG',
    icon: Briefcase,
    color: '#00F0FF',
    scenarioHint: '面试时 HR 给薪资低于预期 30%',
  },
  {
    id: 'overtime',
    label: '拒绝加班',
    en: 'OVERTIME · NO',
    icon: UserX,
    color: '#FF2DAA',
    scenarioHint: '老板临时要求周末加班赶项目',
  },
  {
    id: 'complaint',
    label: '投诉维权',
    en: 'COMPLAINT · WIN',
    icon: ShoppingBag,
    color: '#B7FF00',
    scenarioHint: '买到问题商品需要跟客服维权',
  },
  {
    id: 'review',
    label: '年终述职',
    en: 'REVIEW · DEFEND',
    icon: BookOpen,
    color: '#FFA600',
    scenarioHint: '年终汇报时被领导质疑贡献度',
  },
]

const TONE_COLOR_HEX: Record<ToneLevel, string> = {
  safe: '#00F0FF',
  aggro: '#FF2DAA',
  fun: '#FFE94D',
}

const TONE_EMOJI: Record<ToneLevel, string> = {
  safe: '🐶',
  aggro: '🔥',
  fun: '🤡',
}

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

const STATUS_LABEL: Record<string, { label: string; color: string; pulse: boolean }> = {
  connecting: { label: 'CONNECTING', color: '#FFE94D', pulse: true },
  recording: { label: 'RECORDING', color: '#FF2DAA', pulse: true },
  thinking: { label: 'THINKING', color: '#A892FF', pulse: true },
  hinting: { label: 'HINTING', color: '#B7FF00', pulse: false },
  error: { label: 'ERROR', color: '#FF6B35', pulse: false },
  idle: { label: 'READY', color: '#FFFFFF66', pulse: false },
}

function StatusIndicator({ status }: { status: string }) {
  const cfg = STATUS_LABEL[status] ?? STATUS_LABEL.idle!
  return (
    <div className="inline-flex items-center gap-2">
      <span
        className={`inline-block h-2 w-2 rounded-full ${cfg.pulse ? 'animate-pulse' : ''}`}
        style={{ backgroundColor: cfg.color, boxShadow: `0 0 8px ${cfg.color}` }}
      />
      <span className="font-bebas text-[11px] tracking-[0.28em]" style={{ color: cfg.color }}>
        {cfg.label}
      </span>
    </div>
  )
}

/* ── Page orchestrator ────────────────────────────────────────────── */

export function CopilotPage({ onBack }: { onBack: () => void }) {
  // Single session instance for the whole page. CopilotHUD MUST receive
  // this same instance via props — calling useCopilotSession() again
  // inside the HUD would spin up a second, disconnected hook (its own
  // WS + state), so the HUD would render an empty session while the
  // real one (here) silently receives all the asr/hint events.
  const session = useCopilotSession()
  const { state, startSession, reset } = session

  if (!state.started) {
    return (
      <ScenarioPicker
        onStart={startSession}
        onBack={() => {
          reset()
          onBack()
        }}
      />
    )
  }

  return (
    <CopilotHUD
      session={session}
      onExit={() => {
        reset()
        onBack()
      }}
    />
  )
}

/* ── ScenarioPicker ───────────────────────────────────────────────── */

function ScenarioPicker({
  onStart,
  onBack,
}: {
  onStart: (req: CreateCopilotSessionRequest) => void
  onBack: () => void
}) {
  const [selected, setSelected] = useState<string | null>(null)
  const [customHint, setCustomHint] = useState('')

  const handleStart = () => {
    const scenario = SCENARIOS.find((s) => s.id === selected)
    if (!scenario) {
      return
    }
    onStart({
      scenario_hint: customHint.trim() || scenario.scenarioHint,
      privacy_level: 'standard',
    })
  }

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

      <div className="relative z-10 mx-auto flex w-full max-w-4xl flex-1 flex-col px-6 py-8">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1 font-bebas text-xs tracking-[0.24em] text-white/60 transition-colors hover:text-cyber-magenta"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            BACK
          </button>
          <MascotReaction expression="fired-up" size="sm" />
        </div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mt-6 text-center"
        >
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-cyber-magenta/30 bg-cyber-magenta/5 px-3 py-1">
            <Headphones className="h-3 w-3 text-cyber-magenta" />
            <span className="font-bebas text-[11px] tracking-[0.28em] text-cyber-magenta">
              COPILOT · LIVE MODE
            </span>
          </div>
          <h1 className="font-orbitron text-6xl font-black uppercase leading-none tracking-tight">
            <GlowText variant="stroke" color="#FF2DAA">
              COPILOT
            </GlowText>
          </h1>
          <p className="mt-3 font-display text-xl italic text-white/80">关键时刻，耳机里挺你。</p>
        </motion.div>

        <p className="mt-8 font-bebas text-[11px] tracking-[0.28em] text-white/50">
          MISSION · SELECT
        </p>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {SCENARIOS.map((s, i) => {
            const Icon = s.icon
            const isActive = selected === s.id
            return (
              <motion.div
                key={s.id}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: i * 0.06 }}
              >
                <TiltCard className="rounded-3xl">
                  <HudFrame
                    label={`MISSION · 0${i + 1}`}
                    tag=""
                    color={isActive ? s.color : 'rgba(255, 255, 255, 0.4)'}
                    className={`cyber-glass-edge rounded-3xl p-5 transition-all ${
                      isActive ? 'border-cyber-cyan/40' : ''
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => setSelected(s.id)}
                      className="flex w-full flex-col items-start gap-3 text-left"
                    >
                      <div
                        className="flex h-10 w-10 items-center justify-center rounded-2xl border"
                        style={{
                          borderColor: `${s.color}55`,
                          background: `radial-gradient(circle at center, ${s.color}22 0%, transparent 70%)`,
                          color: s.color,
                          boxShadow: isActive ? `0 0 18px ${s.color}66` : undefined,
                        }}
                      >
                        <Icon className="h-5 w-5" />
                      </div>
                      <div>
                        <p
                          className="font-bebas text-[11px] tracking-[0.24em]"
                          style={{ color: s.color }}
                        >
                          {s.en}
                        </p>
                        <p className="font-display text-xl italic text-white">{s.label}</p>
                      </div>
                      <p className="text-xs text-white/55">{s.scenarioHint}</p>
                    </button>
                  </HudFrame>
                </TiltCard>
              </motion.div>
            )
          })}
        </div>

        {selected && (
          <HudFrame
            label="CUSTOM · OVERRIDE"
            tag="optional"
            className="cyber-glass-edge mt-6 rounded-3xl p-6"
          >
            <div className="space-y-3">
              <p className="font-mono text-[11px] text-white/50">
                CURRENT · {SCENARIOS.find((s) => s.id === selected)?.scenarioHint}
              </p>
              <textarea
                value={customHint}
                onChange={(e) => setCustomHint(e.target.value)}
                placeholder="自定义场景描述（可选）…"
                rows={2}
                className="font-grotesk w-full resize-none rounded-2xl border border-cyber-hairline bg-cyber-deep/60 px-4 py-3 text-sm text-white placeholder:text-white/30 focus:border-cyber-magenta focus:bg-cyber-deep/80 focus:outline-none focus:ring-2 focus:ring-cyber-magenta/30"
              />
              <div className="flex justify-end">
                <MagneticButton type="button" variant="magenta" onClick={handleStart}>
                  <Zap className="h-4 w-4" />
                  启动副驾
                </MagneticButton>
              </div>
            </div>
          </HudFrame>
        )}
      </div>
    </div>
  )
}

/* ── CopilotHUD ───────────────────────────────────────────────────── */

const TTS_MUTE_KEY = 'careercoach.copilot_tts_muted'

function readMutePref(): boolean {
  if (typeof localStorage === 'undefined') return false
  return localStorage.getItem(TTS_MUTE_KEY) === '1'
}

function CopilotHUD({
  session,
  onExit,
}: {
  session: ReturnType<typeof useCopilotSession>
  onExit: () => void
}) {
  const { state, endSession, setTone, dismissError } = session
  const [showExitConfirm, setShowExitConfirm] = useState(false)
  const [muted, setMuted] = useState<boolean>(readMutePref)

  const ttsState = useHintTts({ hintText: state.hint?.text ?? null, muted })

  useEffect(() => {
    if (typeof localStorage === 'undefined') return
    localStorage.setItem(TTS_MUTE_KEY, muted ? '1' : '0')
  }, [muted])

  const handleExit = useCallback(() => {
    if (state.started) {
      setShowExitConfirm(true)
    } else {
      onExit()
    }
  }, [state.started, onExit])

  const confirmExit = useCallback(() => {
    endSession()
    setShowExitConfirm(false)
    onExit()
  }, [endSession, onExit])

  const currentHint = state.streamingHint || state.hint?.text || ''
  const toneColor = TONE_COLOR_HEX[state.activeTone]

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-cyber-void text-white">
      <NeuralParticles count={900} />
      <div className="pointer-events-none fixed inset-0 -z-10 tech-grid opacity-25" />
      <div className="pointer-events-none fixed inset-0 -z-10 scanline" />
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 -z-10"
        style={{
          background:
            'radial-gradient(ellipse at center, transparent 0%, rgba(5,5,5,0.5) 60%, rgba(5,5,5,0.95) 100%)',
        }}
      />

      <header className="relative z-10 mx-auto flex w-full max-w-3xl items-center justify-between gap-4 px-6 pt-6">
        <div className="flex items-center gap-3">
          <MascotReaction expression={state.mascotExpression} size="sm" />
          <StatusIndicator status={state.status} />
        </div>
        <div className="flex items-center gap-4">
          <button
            type="button"
            onClick={() => setMuted((m) => !m)}
            aria-label={muted ? '开启耳机提示' : '静音耳机提示'}
            aria-pressed={muted}
            className={`inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 font-bebas text-[10px] tracking-[0.22em] transition-colors ${
              muted
                ? 'border-white/20 bg-white/5 text-white/50 hover:text-white/80'
                : 'border-cyber-cyan/40 bg-cyber-cyan/10 text-cyber-cyan hover:bg-cyber-cyan/15'
            }`}
          >
            {muted ? <VolumeX className="h-3.5 w-3.5" /> : <Volume2 className="h-3.5 w-3.5" />}
            {muted ? 'MUTED' : ttsState.isSpeaking ? 'SPEAK' : 'AUDIO'}
          </button>
          <span className="font-orbitron text-sm font-semibold text-white">
            {formatDuration(state.durationSec)}
          </span>
          <span className="font-mono text-[10px] text-white/40 truncate max-w-[160px]">
            {state.scenarioHint}
          </span>
        </div>
      </header>

      {state.error && (
        <div
          role="alert"
          className="relative z-10 mx-auto mt-4 flex w-full max-w-3xl items-center justify-between rounded-2xl border border-cyber-magenta/40 bg-cyber-magenta/10 px-4 py-2"
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

      {!muted && ttsState.error === 'autoplay_blocked' && (
        <div
          role="status"
          className="relative z-10 mx-auto mt-3 flex w-full max-w-3xl items-center justify-center gap-2 rounded-2xl border border-cyber-amber/40 bg-cyber-amber/10 px-4 py-2"
        >
          <Volume2 className="h-3.5 w-3.5 text-cyber-amber" />
          <span className="font-mono text-[11px] text-cyber-amber">
            浏览器拦了自动播放,点一下上方的 AUDIO 按钮再开一次
          </span>
        </div>
      )}

      <main className="relative z-10 mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center gap-5 px-6 py-6">
        {/* Subtitle card */}
        {state.transcript && (
          <div className="w-full animate-fade-in">
            <p className="mb-2 font-bebas text-[11px] tracking-[0.28em] text-white/50">
              OPPONENT · TRANSCRIPT
            </p>
            <div className="cyber-glass rounded-3xl border border-cyber-hairline px-5 py-4">
              <p className="font-grotesk text-base leading-relaxed text-white">
                &ldquo;{state.transcript.opponentText}&rdquo;
                {!state.transcript.isFinal && (
                  <span className="ml-1 inline-block h-4 w-0.5 animate-pulse bg-cyber-cyan align-text-bottom" />
                )}
              </p>
            </div>
          </div>
        )}

        {/* Main hint card */}
        {(currentHint || state.status === 'thinking') && (
          <div className="w-full animate-fade-in">
            <HudFrame
              label={`K · ${state.activeTone.toUpperCase()} · ${TONE_EMOJI[state.activeTone]}`}
              tag={
                state.hint && state.hint.confidence < 0.6
                  ? `LOW ${Math.round(state.hint.confidence * 100)}%`
                  : 'LIVE'
              }
              color={`${toneColor}E6`}
              className="cyber-glass-edge rounded-3xl border p-6"
            >
              {state.status === 'thinking' && !currentHint ? (
                <div className="flex items-center gap-3">
                  <div className="flex gap-1">
                    <span
                      className="h-2 w-2 animate-bounce rounded-full"
                      style={{ backgroundColor: toneColor, animationDelay: '0ms' }}
                    />
                    <span
                      className="h-2 w-2 animate-bounce rounded-full"
                      style={{ backgroundColor: toneColor, animationDelay: '150ms' }}
                    />
                    <span
                      className="h-2 w-2 animate-bounce rounded-full"
                      style={{ backgroundColor: toneColor, animationDelay: '300ms' }}
                    />
                  </div>
                  <span className="font-mono text-xs text-white/50">K · THINKING…</span>
                </div>
              ) : (
                <p
                  className="font-display text-2xl font-bold italic leading-snug text-white"
                  style={{ textShadow: `0 0 18px ${toneColor}55` }}
                >
                  &ldquo;{currentHint}&rdquo;
                  {state.streamingHint && (
                    <span
                      className="ml-1 inline-block h-5 w-0.5 animate-pulse align-text-bottom"
                      style={{ backgroundColor: toneColor }}
                    />
                  )}
                </p>
              )}
            </HudFrame>
          </div>
        )}

        {/* Tone switcher — HintCardV2 in switcher-only mode */}
        <div className="w-full">
          <HintCardV2
            hint=""
            activeTone={state.activeTone}
            onToneChange={setTone}
            className="!p-0 border-0 bg-transparent shadow-none"
          />
        </div>

        {!state.transcript && state.status === 'recording' && (
          <p className="font-mono text-[11px] text-white/40 animate-pulse">
            LISTENING · 正在听你的对手…
          </p>
        )}

        {state.redirectResource && (
          <HudFrame
            label="SAFETY · RESOURCE"
            tag="§3.0.5"
            color="rgba(255, 107, 53, 0.85)"
            className="cyber-glass-edge w-full rounded-3xl border-cyber-amber/30 p-5"
          >
            <p className="font-display text-base italic text-white">
              {state.redirectResource.title}
            </p>
            <a
              href={state.redirectResource.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-flex items-center gap-1 font-bebas text-xs tracking-[0.22em] text-cyber-amber underline-offset-4 hover:underline"
            >
              <ShieldAlert className="h-3.5 w-3.5" />
              获取帮助
            </a>
          </HudFrame>
        )}
      </main>

      <footer className="relative z-10 flex justify-center pb-8">
        <MagneticButton type="button" variant="magenta" onClick={handleExit}>
          <MicOff className="h-4 w-4" />
          停止副驾
        </MagneticButton>
      </footer>

      {showExitConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-cyber-void/80 backdrop-blur-md">
          <HudFrame
            label="EXIT · CONFIRM"
            tag="!"
            className="cyber-glass-edge mx-4 w-full max-w-sm rounded-3xl p-8 text-center"
          >
            <MascotReaction expression="caring" size="md" />
            <p className="mt-4 font-display text-xl italic text-white">确定要结束副驾吗？</p>
            <p className="mt-2 font-mono text-xs text-white/50">SESSION · NOT SAVED</p>
            <div className="mt-6 flex justify-center gap-3">
              <button
                type="button"
                onClick={() => setShowExitConfirm(false)}
                className="rounded-full border border-cyber-hairline px-5 py-2 font-bebas text-xs tracking-[0.22em] text-white/80 hover:border-cyber-cyan/40 hover:text-cyber-cyan"
              >
                CONTINUE
              </button>
              <MagneticButton type="button" variant="magenta" onClick={confirmExit}>
                CONFIRM EXIT
              </MagneticButton>
            </div>
          </HudFrame>
        </div>
      )}
    </div>
  )
}
