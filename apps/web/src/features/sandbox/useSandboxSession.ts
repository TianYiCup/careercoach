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
}

export function useSandboxSession() {
  const [state, setState] = useState<SandboxState>(INITIAL_STATE)
  const abortRef = useRef<AbortController | null>(null)

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
  }, [])

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
    [state.sessionId, state.isStreaming],
  )

  /** End the session and get score */
  const endSession = useCallback(async () => {
    if (!state.sessionId) return
    abortRef.current?.abort()
    const res = await apiClient.post<EndSessionResponse>(
      `/sessions/${state.sessionId}/end`,
    )
    setState((s) => ({ ...s, score: res, isStreaming: false }))
  }, [state.sessionId])

  /** Set active tone */
  const setTone = useCallback((tone: ToneLevel) => {
    setState((s) => ({ ...s, activeTone: tone }))
  }, [])

  /** Reset to initial state */
  const reset = useCallback(() => {
    abortRef.current?.abort()
    setState(INITIAL_STATE)
  }, [])

  return {
    state,
    startSession,
    sendTurn,
    endSession,
    setTone,
    reset,
  }
}
