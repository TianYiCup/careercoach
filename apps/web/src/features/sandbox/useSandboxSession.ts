import { useState, useCallback, useRef } from 'react'
import { apiClient } from '../../api/v1/client'
import { postSSE } from '../../api/v1/sse'
import type {
  CreateSessionRequest,
  CreateSessionResponse,
  EndSessionResponse,
  SseEventFrame,
} from '../../api/v1/types'
import type { ToneLevel } from '../../components'
import type { MascotExpression } from '../../components/mascot/types'

export interface ChatMessage {
  role: 'opponent' | 'user'
  text: string
  /** Is this message still streaming? */
  streaming?: boolean
}

export interface SandboxState {
  sessionId: string | null
  messages: ChatMessage[]
  /** Current streaming opponent text (not yet in messages) */
  streamingText: string
  /** Is opponent currently speaking (SSE in flight)? */
  isStreaming: boolean
  /** Current turn hints from coach K */
  hints: { safe: string; aggressive: string; humor: string } | null
  /** Active tone level */
  activeTone: ToneLevel
  /** Turns used / left */
  turnsUsed: number
  turnsLeft: number
  /** Final score after ending session */
  score: EndSessionResponse | null
  /** Session started? */
  started: boolean
  /** Current mascot expression (auto-derived from conversation state) */
  mascotExpression: MascotExpression
}

const INITIAL_STATE: SandboxState = {
  sessionId: null,
  messages: [],
  streamingText: '',
  isStreaming: false,
  hints: null,
  activeTone: 'aggro',
  turnsUsed: 0,
  turnsLeft: 30,
  score: null,
  started: false,
  mascotExpression: 'confident',
}

/** Derive mascot expression from conversation state — design-spec §3.3 */
function deriveExpression(state: SandboxState): MascotExpression {
  // Score result — highest priority
  if (state.score) {
    if (state.score.score.result === 'shenfeng') return 'godlike'
    if (state.score.score.result === 'fanche') return 'crashed'
    return 'confident' // guolu
  }
  // Opponent thinking → thinking face
  if (state.isStreaming) return 'thinking'
  // Tone-driven expressions (when coach.hint arrives)
  if (state.hints) {
    if (state.activeTone === 'safe') return 'caring'
    if (state.activeTone === 'aggro') return 'fired-up'
    if (state.activeTone === 'fun') return 'clowning'
  }
  // Default idle
  return 'confident'
}

/** Apply state update + auto-derive mascot expression */
function withExpression(
  updater: SandboxState | ((prev: SandboxState) => SandboxState),
): (prev: SandboxState) => SandboxState {
  return (prev: SandboxState) => {
    const next = typeof updater === 'function' ? updater(prev) : updater
    const expression = deriveExpression(next)
    return expression === next.mascotExpression
      ? next
      : { ...next, mascotExpression: expression }
  }
}

export function useSandboxSession() {
  const [state, setRawState] = useState<SandboxState>(INITIAL_STATE)
  const abortRef = useRef<AbortController | null>(null)

  /** State setter that auto-derives mascot expression */
  const setState = useCallback(
    (updater: SandboxState | ((prev: SandboxState) => SandboxState)) => {
      setRawState(withExpression(updater))
    },
    [],
  )

  /** Start a new session */
  const startSession = useCallback(async (req: CreateSessionRequest) => {
    const res = await apiClient.post<CreateSessionResponse>(
      '/sessions',
      req,
    )
    setState((s) => ({
      ...s,
      sessionId: res.session_id,
      started: true,
      messages: [{ role: 'opponent', text: res.opening_line }],
    }))
  }, [setState])

  /** Send a user message and consume SSE stream */
  const sendTurn = useCallback(
    async (content: string) => {
      if (!state.sessionId || state.isStreaming) return

      // Add user message
      setState((s) => ({
        ...s,
        isStreaming: true,
        streamingText: '',
        hints: null,
        messages: [...s.messages, { role: 'user', text: content }],
      }))

      const abort = new AbortController()
      abortRef.current = abort

      try {
        await postSSE(
          `/sessions/${state.sessionId}/turns`,
          { content },
          (frame: SseEventFrame) => {
            setState((s) => {
              switch (frame.event) {
                case 'opponent.delta':
                  return {
                    ...s,
                    streamingText: s.streamingText + frame.data.text,
                  }
                case 'opponent.done':
                  return {
                    ...s,
                    streamingText: '',
                    isStreaming: false,
                    messages: [
                      ...s.messages,
                      {
                        role: 'opponent' as const,
                        text: frame.data.full_text,
                      },
                    ],
                  }
                case 'coach.hint':
                  return {
                    ...s,
                    hints: frame.data,
                  }
                case 'meta':
                  return {
                    ...s,
                    turnsUsed: frame.data.turns_used,
                    turnsLeft: frame.data.turns_left,
                  }
                default:
                  return s
              }
            })
          },
          abort.signal,
        )
      } catch (err) {
        // Abort is expected on unmount/cancel
        if (err instanceof DOMException && err.name === 'AbortError') return
        setState((s) => ({
          ...s,
          isStreaming: false,
          streamingText: '',
          messages: [
            ...s.messages,
            { role: 'opponent', text: `[连接中断: ${String(err)}]` },
          ],
        }))
      }
    },
    [state.sessionId, state.isStreaming, setState],
  )

  /** End the session and get score */
  const endSession = useCallback(async () => {
    if (!state.sessionId) return
    abortRef.current?.abort()
    const res = await apiClient.post<EndSessionResponse>(
      `/sessions/${state.sessionId}/end`,
    )
    setState((s) => ({ ...s, score: res, isStreaming: false }))
  }, [state.sessionId, setState])

  /** Set active tone */
  const setTone = useCallback(
    (tone: ToneLevel) => {
      setState((s) => ({ ...s, activeTone: tone }))
    },
    [setState],
  )

  /** Reset to initial state */
  const reset = useCallback(() => {
    abortRef.current?.abort()
    setState(INITIAL_STATE)
  }, [setState])

  return {
    state,
    startSession,
    sendTurn,
    endSession,
    setTone,
    reset,
  }
}
