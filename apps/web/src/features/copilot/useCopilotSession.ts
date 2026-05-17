import { useState, useCallback, useRef, useEffect } from 'react'
import { apiClient, ApiError } from '../../api/v1/client'
import type {
  CreateCopilotSessionRequest,
  CreateCopilotSessionResponse,
  CopilotWsEvent,
  ModerationVerdict,
} from '../../api/v1/types'
import type { ToneLevel } from '../../components'
import type { MascotExpression } from '../../components/mascot/types'

// --- State shape ---

export type CopilotStatus =
  | 'idle'
  | 'connecting'
  | 'recording'
  | 'thinking'
  | 'hinting'
  | 'error'

export interface CopilotHint {
  text: string
  /** Confidence 0-1 from LLM logprob; <0.6 means low confidence */
  confidence: number
}

export interface CopilotTranscript {
  /** Latest ASR partial/final text from the opponent */
  opponentText: string
  /** Is the current transcript final (asr_final)? */
  isFinal: boolean
}

export interface CopilotState {
  /** Current copilot session id */
  copilotId: string | null
  /** Whether the session has been started */
  started: boolean
  /** Lifecycle status */
  status: CopilotStatus
  /** Scenario hint provided at start */
  scenarioHint: string
  /** Opponent transcript (subtitle card) */
  transcript: CopilotTranscript | null
  /** Current hint from Coach K */
  hint: CopilotHint | null
  /** Streaming hint text (from hint_delta) */
  streamingHint: string
  /** Active tone level */
  activeTone: ToneLevel
  /** Last moderation verdict for the opponent's utterance */
  lastVerdict: ModerationVerdict | null
  /** Redirection resource (crisis line) when verdict is redirect */
  redirectResource: { title: string; url: string } | null
  /** Duration in seconds since session started */
  durationSec: number
  /** Mascot expression */
  mascotExpression: MascotExpression
  /** User-visible error */
  error: string | null
}

const INITIAL_STATE: CopilotState = {
  copilotId: null,
  started: false,
  status: 'idle',
  scenarioHint: '',
  transcript: null,
  hint: null,
  streamingHint: '',
  activeTone: 'safe',
  lastVerdict: null,
  redirectResource: null,
  durationSec: 0,
  mascotExpression: 'confident',
  error: null,
}

// --- Helpers ---

function _isAuthError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401
}

function _isMinorForbidden(err: unknown): boolean {
  return (
    err instanceof ApiError &&
    err.status === 403 &&
    (err.body as { code?: string })?.code === 'MINOR_FORBIDDEN'
  )
}

function deriveExpression(state: CopilotState): MascotExpression {
  if (state.status === 'error') return 'crashed'
  if (state.status === 'thinking') return 'thinking'
  if (state.hint) {
    if (state.hint.confidence < 0.6) return 'crashed'
    if (state.activeTone === 'safe') return 'caring'
    if (state.activeTone === 'aggro') return 'fired-up'
    if (state.activeTone === 'fun') return 'clowning'
  }
  if (state.status === 'recording') return 'slacking'
  return 'confident'
}

function withExpression(
  updater: CopilotState | ((prev: CopilotState) => CopilotState),
): (prev: CopilotState) => CopilotState {
  return (prev: CopilotState) => {
    const next = typeof updater === 'function' ? updater(prev) : updater
    const expression = deriveExpression(next)
    return expression === next.mascotExpression
      ? next
      : { ...next, mascotExpression: expression }
  }
}

// --- Mock WS event simulation ---
// When MSW is active, the POST returns a fake ws_url. Rather than
// trying to open a real WebSocket (which would fail), we simulate
// the event stream with setTimeout. This lets the HUD UI be fully
// developed and tested without a running backend.

const MOCK_OPPONENT_LINES = [
  '我们公司的预算就这么多',
  '你看其他同事都没意见',
  '这个方案太冒险了，还是保守一点吧',
  '你能不能这周末把报告赶出来',
  '大家都在加班，就你特殊？',
]

const MOCK_HINTS: Record<ToneLevel, string[]> = {
  safe: [
    '可以反问具体预算上限，让对方先亮底牌',
    '先认可对方关切，再提出替代方案',
    '用数据说话，不急着否定',
  ],
  aggro: [
    '直接质疑合理性，预算少不代表就该妥协',
    '问对方"你是觉得我不值得更好的吗"',
    '正面刚，要求对等交换条件',
  ],
  fun: [
    '说"预算不够那我只能用爱发电了"',
    '回"大家都加班？那公司得请我们吃夜宵"',
    '说"我可以加班，但得加猫粮补贴"',
  ],
}

let mockLineIndex = 0

function startMockWsStream(
  onEvent: (event: CopilotWsEvent) => void,
): () => void {
  let cancelled = false
  const timers: ReturnType<typeof setTimeout>[] = []

  const schedule = (fn: () => void, ms: number) => {
    const t = setTimeout(() => {
      if (!cancelled) fn()
    }, ms)
    timers.push(t)
  }

  const runUtterance = () => {
    if (cancelled) return
    const line = MOCK_OPPONENT_LINES[mockLineIndex % MOCK_OPPONENT_LINES.length]!
    mockLineIndex++

    // 1. ASR partial → final (simulating speech)
    schedule(() => onEvent({ type: 'asr_partial', text: line.slice(0, 4) }), 800)
    schedule(() => onEvent({ type: 'asr_final', text: line }), 1500)

    // 2. Moderation (always allow in mock)
    schedule(
      () => onEvent({ type: 'moderation', verdict: 'allow', categories: [], score: 0.1 }),
      1800,
    )

    // 3. Hint stream
    const hintIdx = (mockLineIndex - 1) % 3
    const hint = MOCK_HINTS.safe[hintIdx] ?? '先听听对方怎么说的'
    schedule(() => onEvent({ type: 'hint_delta', text: hint.slice(0, 4) }), 2500)
    schedule(() => onEvent({ type: 'hint_done', text: hint }), 3000)

    // 4. Next utterance after a pause
    schedule(() => runUtterance(), 7000)
  }

  // Start first utterance after a brief "listening" period
  schedule(() => runUtterance(), 1500)

  return () => {
    cancelled = true
    timers.forEach(clearTimeout)
  }
}

// --- Hook ---

export function useCopilotSession() {
  const [state, setRawState] = useState<CopilotState>(INITIAL_STATE)
  const wsRef = useRef<WebSocket | null>(null)
  const mockCancelRef = useRef<(() => void) | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const startTimeRef = useRef<number>(0)

  const setState = useCallback(
    (updater: CopilotState | ((prev: CopilotState) => CopilotState)) => {
      setRawState(withExpression(updater))
    },
    [],
  )

  // Duration timer
  const startTimer = useCallback(() => {
    startTimeRef.current = Date.now()
    timerRef.current = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startTimeRef.current) / 1000)
      setState((s) => (s.durationSec === elapsed ? s : { ...s, durationSec: elapsed }))
    }, 1000)
  }, [setState])

  const stopTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  /** Handle a WS event from either real WS or mock stream */
  const handleWsEvent = useCallback(
    (event: CopilotWsEvent) => {
      switch (event.type) {
        case 'asr_partial':
          setState((s) => ({
            ...s,
            status: 'recording',
            transcript: { opponentText: event.text, isFinal: false },
          }))
          break
        case 'asr_final':
          setState((s) => ({
            ...s,
            status: 'thinking',
            transcript: { opponentText: event.text, isFinal: true },
          }))
          break
        case 'moderation':
          setState((s) => ({
            ...s,
            lastVerdict: event.verdict,
            redirectResource: event.redirect_resource ?? null,
          }))
          break
        case 'hint_delta':
          setState((s) => ({
            ...s,
            status: 'hinting',
            streamingHint: s.streamingHint + event.text,
          }))
          break
        case 'hint_done':
          setState((s) => ({
            ...s,
            status: 'hinting',
            streamingHint: '',
            hint: {
              text: event.text,
              confidence: 0.85, // mock: high confidence
            },
          }))
          break
        case 'hint_error':
          setState((s) => ({
            ...s,
            status: 'error',
            error: event.message,
            hint: null,
          }))
          break
        case 'asr_error':
          setState((s) => ({
            ...s,
            status: 'error',
            error: event.message,
          }))
          break
      }
    },
    [setState],
  )

  /** Start a copilot session — POST /copilot/sessions then connect WS */
  const startSession = useCallback(
    async (req: CreateCopilotSessionRequest) => {
      setState((s) => (s.error === null ? { ...s, status: 'connecting' } : { ...s, status: 'connecting', error: null }))
      try {
        const res = await apiClient.post<CreateCopilotSessionResponse>(
          '/copilot/sessions',
          req,
        )
        setState((s) => ({
          ...s,
          copilotId: res.copilot_id,
          started: true,
          scenarioHint: req.scenario_hint,
        }))

        // Connect to WS or mock stream
        if (res.ws_url.includes('mock.careercoach.ai')) {
          // MSW mock mode — simulate events with setTimeout
          const cancel = startMockWsStream(handleWsEvent)
          mockCancelRef.current = cancel
        } else {
          // Real WebSocket — RFC 6455
          const ws = new WebSocket(res.ws_url)
          ws.onopen = () => {
            setState((s) => ({ ...s, status: 'recording' }))
          }
          ws.onmessage = (ev) => {
            try {
              const data = JSON.parse(ev.data as string) as CopilotWsEvent
              handleWsEvent(data)
            } catch {
              // Ignore malformed frames
            }
          }
          ws.onerror = () => {
            setState((s) => ({
              ...s,
              status: 'error',
              error: '连接中断，请重启副驾',
            }))
          }
          ws.onclose = (ev) => {
            // Custom close codes from backend: 4404 not found, 4409 already used
            if (ev.code === 4404 || ev.code === 4409) {
              setState((s) => ({
                ...s,
                status: 'error',
                error: '会话已失效，请重新启动',
              }))
            }
          }
          wsRef.current = ws
        }

        startTimer()
      } catch (err) {
        if (_isAuthError(err)) return
        if (_isMinorForbidden(err)) {
          setState((s) => ({
            ...s,
            status: 'error',
            error: '未成年人无法使用副驾功能',
          }))
          return
        }
        setState((s) => ({
          ...s,
          status: 'error',
          error: '启动失败，请稍后重试',
        }))
      }
    },
    [handleWsEvent, startTimer, setState],
  )

  /** End the copilot session */
  const endSession = useCallback(() => {
    // Close WS connection
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    // Cancel mock stream
    if (mockCancelRef.current) {
      mockCancelRef.current()
      mockCancelRef.current = null
    }
    stopTimer()
    setState((s) => ({
      ...s,
      status: 'idle',
      started: false,
    }))
  }, [stopTimer, setState])

  /** Send audio_end control frame to finalize current utterance */
  const sendAudioEnd = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'audio_end' }))
    }
    // In mock mode, utterances auto-finalize — no-op
  }, [])

  /** Set active tone */
  const setTone = useCallback(
    (tone: ToneLevel) => {
      setState((s) => ({ ...s, activeTone: tone }))
    },
    [setState],
  )

  /** Dismiss error */
  const dismissError = useCallback(() => {
    setState((s) => (s.error === null ? s : { ...s, error: null, status: s.started ? 'recording' : 'idle' }))
  }, [setState])

  /** Reset to initial state */
  const reset = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    if (mockCancelRef.current) {
      mockCancelRef.current()
      mockCancelRef.current = null
    }
    stopTimer()
    setState(INITIAL_STATE)
  }, [stopTimer, setState])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) wsRef.current.close()
      if (mockCancelRef.current) mockCancelRef.current()
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  return {
    state,
    startSession,
    endSession,
    sendAudioEnd,
    setTone,
    dismissError,
    reset,
  }
}
