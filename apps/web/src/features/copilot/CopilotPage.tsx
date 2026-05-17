import { useState, useCallback } from 'react'
import {
  GlassCard,
  BlobBackground,
  MascotReaction,
  HintCardV2,
} from '../../components'
import { useCopilotSession } from './useCopilotSession'
import type { CreateCopilotSessionRequest } from '../../api/v1/types'
import type { ToneLevel } from '../../components'

// --- Scenario picker ---

interface ScenarioOption {
  id: string
  label: string
  emoji: string
  scenarioHint: string
}

const SCENARIOS: ScenarioOption[] = [
  {
    id: 'salary',
    label: '面试谈薪',
    emoji: '💼',
    scenarioHint: '面试时HR给薪资低于预期30%',
  },
  {
    id: 'overtime',
    label: '拒绝加班',
    emoji: '🙅',
    scenarioHint: '老板临时要求周末加班赶项目',
  },
  {
    id: 'complaint',
    label: '投诉维权',
    emoji: '🛡️',
    scenarioHint: '买到问题商品需要跟客服维权',
  },
  {
    id: 'review',
    label: '年终述职',
    emoji: '📊',
    scenarioHint: '年终汇报时被领导质疑贡献度',
  },
]

// --- CopilotScenarioPicker ---

function CopilotScenarioPicker({
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
    if (!scenario) return
    onStart({
      scenario_hint: customHint.trim() || scenario.scenarioHint,
      privacy_level: 'standard',
    })
  }

  return (
    <div className="relative min-h-screen flex flex-col items-center px-4 py-12 overflow-hidden">
      <BlobBackground />

      <div className="relative z-10 w-full max-w-lg space-y-6">
        {/* Back button */}
        <button
          type="button"
          onClick={onBack}
          className="text-ink-text-2 text-sm hover:text-ink-text transition-colors"
        >
          &larr; 返回
        </button>

        <div className="text-center">
          <MascotReaction expression="fired-up" size="md" showLabel />
          <h1 className="mt-4 text-2xl font-display text-ink-text">
            实战副驾
          </h1>
          <p className="mt-1 text-sm text-ink-text-2 font-body">
            关键时刻，耳机里挺你
          </p>
        </div>

        <GlassCard className="space-y-4">
          <p className="text-sm text-ink-text-2 font-body">选择场景</p>
          <div className="grid grid-cols-2 gap-3">
            {SCENARIOS.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setSelected(s.id)}
                className={`p-3 rounded-radius-md text-left text-sm font-body border transition-all ${
                  selected === s.id
                    ? 'border-vivid-purple bg-vivid-purple/10 text-ink-text'
                    : 'border-ink-line bg-ink-card/60 text-ink-text-2 hover:border-ink-text-3'
                }`}
              >
                <span className="text-lg" role="img" aria-label={s.label}>
                  {s.emoji}
                </span>
                <span className="ml-2 font-medium">{s.label}</span>
              </button>
            ))}
          </div>

          {selected && (
            <div className="space-y-3">
              <p className="text-xs text-ink-text-3 font-body">
                当前场景提示：{SCENARIOS.find((s) => s.id === selected)?.scenarioHint}
              </p>
              <textarea
                value={customHint}
                onChange={(e) => setCustomHint(e.target.value)}
                placeholder="自定义场景描述（可选）..."
                rows={2}
                className="w-full rounded-radius-md bg-ink-card px-3 py-2 text-sm text-ink-text font-body border border-ink-line focus:border-vivid-purple focus:outline-none transition-colors resize-none"
              />
              <button
                type="button"
                onClick={handleStart}
                className="w-full py-3 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform"
              >
                启动副驾
              </button>
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  )
}

// --- Status indicator ---

function StatusIndicator({ status }: { status: string }) {
  const config: Record<string, { label: string; color: string; pulse: boolean }> = {
    connecting: { label: '连接中', color: 'bg-vivid-yellow', pulse: true },
    recording: { label: '录音中', color: 'bg-vivid-red', pulse: true },
    thinking: { label: '思考中', color: 'bg-vivid-purple', pulse: true },
    hinting: { label: '提示中', color: 'bg-vivid-green', pulse: false },
    error: { label: '异常', color: 'bg-vivid-orange', pulse: false },
    idle: { label: '就绪', color: 'bg-ink-text-3', pulse: false },
  }
  const cfg = config[status] ?? config.idle ?? { label: '就绪', color: 'bg-ink-text-3', pulse: false }

  return (
    <div className="flex items-center gap-2">
      <span
        className={`inline-block w-2 h-2 rounded-full ${cfg.color} ${cfg.pulse ? 'animate-pulse' : ''}`}
      />
      <span className="text-xs text-ink-text-2 font-body">{cfg.label}</span>
    </div>
  )
}

// --- Duration display ---

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

// --- CopilotHUD ---

function CopilotHUD({
  onExit,
}: {
  onExit: () => void
}) {
  const {
    state,
    endSession,
    setTone,
    dismissError,
  } = useCopilotSession()
  const [showExitConfirm, setShowExitConfirm] = useState(false)

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

  /** Map activeTone to the hint text for current tone */
  const currentHint =
    state.streamingHint || state.hint?.text || ''

  /** Tone label mapping for the main hint card border */
  const toneColorMap: Record<ToneLevel, string> = {
    safe: 'border-tone-safe',
    aggro: 'border-tone-aggro',
    fun: 'border-tone-fun',
  }

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-ink-bg">
      {/* Darker HUD background — design-spec §9.5: "HUD 默认黑底高对比" */}
      <div className="absolute inset-0 bg-gradient-to-b from-ink-bg via-ink-bg/95 to-ink-bg" />

      {/* Header */}
      <header className="relative z-10 flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3">
          <MascotReaction
            expression={state.mascotExpression}
            size="sm"
          />
          <StatusIndicator status={state.status} />
        </div>
        <div className="flex items-center gap-4">
          <span className="text-sm text-ink-text-2 font-body font-mono">
            {formatDuration(state.durationSec)}
          </span>
          <span className="text-xs text-ink-text-3 font-body">
            {state.scenarioHint}
          </span>
        </div>
      </header>

      {/* Error banner */}
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
            &times;
          </button>
        </div>
      )}

      {/* Main HUD area — design-spec §9.5 */}
      <main className="relative z-10 flex-1 flex flex-col items-center justify-center px-4 py-6 space-y-5">
        {/* Subtitle card — opponent's transcript */}
        {state.transcript && (
          <div className="w-full max-w-md animate-fade-in">
            <p className="text-xs text-ink-text-3 font-body mb-1.5">
              对方刚说：
            </p>
            <GlassCard className="border-ink-line/50">
              <p className="text-base text-ink-text font-body leading-relaxed">
                &ldquo;{state.transcript.opponentText}&rdquo;
                {!state.transcript.isFinal && (
                  <span className="inline-block w-0.5 h-4 ml-1 bg-vivid-purple animate-pulse align-text-bottom" />
                )}
              </p>
            </GlassCard>
          </div>
        )}

        {/* Main hint card — Coach K suggestion */}
        {(currentHint || state.status === 'thinking') && (
          <div className="w-full max-w-md animate-fade-in">
            <GlassCard
              className={`border-2 ${toneColorMap[state.activeTone] ?? ''}`}
              glow
            >
              {/* Hint header */}
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-ink-text-2 font-body">
                  试试这句
                  <span className="ml-1.5 text-base" role="img" aria-label={
                    state.activeTone === 'safe' ? '稳' : state.activeTone === 'aggro' ? '刚' : '活'
                  }>
                    {state.activeTone === 'safe' ? '🐶' : state.activeTone === 'aggro' ? '🔥' : '🤡'}
                  </span>
                </span>
                {state.hint && state.hint.confidence < 0.6 && (
                  <span className="text-xs text-vivid-orange font-body">
                    低置信度
                  </span>
                )}
              </div>

              {/* Hint content — 24pt bold per design-spec */}
              {state.status === 'thinking' && !currentHint ? (
                <div className="flex items-center gap-2">
                  <div className="flex gap-1">
                    <span className="w-2 h-2 rounded-full bg-vivid-purple animate-bounce" style={{ animationDelay: '0ms' }} />
                    <span className="w-2 h-2 rounded-full bg-vivid-purple animate-bounce" style={{ animationDelay: '150ms' }} />
                    <span className="w-2 h-2 rounded-full bg-vivid-purple animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                  <span className="text-sm text-ink-text-3 font-body">教练在想...</span>
                </div>
              ) : (
                <p className="text-xl font-display font-bold text-ink-text leading-snug">
                  &ldquo;{currentHint}&rdquo;
                  {state.streamingHint && (
                    <span className="inline-block w-0.5 h-5 ml-1 bg-vivid-purple animate-pulse align-text-bottom" />
                  )}
                </p>
              )}
            </GlassCard>
          </div>
        )}

        {/* Tone switcher — design-spec §9.5: 滑动切档 */}
        <div className="w-full max-w-md">
          <HintCardV2
            hint="" // We render the actual hint above; this is just the tone switcher
            activeTone={state.activeTone}
            onToneChange={setTone}
            className="!p-0 border-0 bg-transparent shadow-none"
          />
        </div>

        {/* Idle state — waiting for opponent */}
        {!state.transcript && state.status === 'recording' && (
          <div className="text-center space-y-2 animate-fade-in">
            <p className="text-sm text-ink-text-3 font-body">
              正在听你的对手...
            </p>
          </div>
        )}

        {/* Redirect resource — when moderation blocks */}
        {state.redirectResource && (
          <div className="w-full max-w-md rounded-radius-md bg-vivid-orange/10 border border-vivid-orange/30 p-3">
            <p className="text-sm text-ink-text font-body font-medium">
              {state.redirectResource.title}
            </p>
            <a
              href={state.redirectResource.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-vivid-orange font-body underline"
            >
              获取帮助
            </a>
          </div>
        )}
      </main>

      {/* Footer — stop button */}
      <footer className="relative z-10 flex justify-center py-6">
        <button
          type="button"
          onClick={handleExit}
          className="px-6 py-3 rounded-radius-pill bg-vivid-red/20 border border-vivid-red/40 text-vivid-red text-sm font-body font-medium hover:bg-vivid-red/30 transition-colors"
        >
          停止副驾
        </button>
      </footer>

      {/* Exit confirmation modal */}
      {showExitConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-bg/70 backdrop-blur-sm">
          <GlassCard className="mx-4 max-w-sm w-full space-y-4 text-center">
            <MascotReaction expression="caring" size="md" showLabel />
            <p className="text-lg font-body text-ink-text">确定要结束副驾吗？</p>
            <p className="text-sm text-ink-text-2">当前会话不会被保存</p>
            <div className="flex gap-3 justify-center pt-2">
              <button
                type="button"
                onClick={() => setShowExitConfirm(false)}
                className="px-5 py-2 rounded-radius-pill bg-ink-card border border-ink-line text-ink-text text-sm font-body hover:bg-ink-card-2 transition-colors"
              >
                继续
              </button>
              <button
                type="button"
                onClick={confirmExit}
                className="px-5 py-2 rounded-radius-pill bg-vivid-red/20 border border-vivid-red/40 text-vivid-red text-sm font-body hover:bg-vivid-red/30 transition-colors"
              >
                确定结束
              </button>
            </div>
          </GlassCard>
        </div>
      )}
    </div>
  )
}

// --- Exported page component ---

export function CopilotPage({
  onBack,
}: {
  onBack: () => void
}) {
  const { state, startSession, reset } = useCopilotSession()

  // If not started, show scenario picker
  if (!state.started) {
    return (
      <CopilotScenarioPicker
        onStart={startSession}
        onBack={() => {
          reset()
          onBack()
        }}
      />
    )
  }

  // Otherwise show HUD
  return <CopilotHUD onExit={() => { reset(); onBack() }} />
}
