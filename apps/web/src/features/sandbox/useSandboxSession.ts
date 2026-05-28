import { useState, useCallback, useRef } from 'react'
import { apiClient, ApiError } from '../../api/v1/client'
import { postSSE } from '../../api/v1/sse'
import type {
  CharacterVector,
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
  /** Opponent's 6-dim character vector (L1). Set on session create from
   * the CreateSessionResponse and consumed by the SandboxRoom radar
   * (L9). Null until startSession resolves so the radar can hide on a
   * fresh mount. */
  characterVector: CharacterVector | null
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
  /** Minor is in quiet hours (22:00-08:00 Asia/Shanghai) — PRD §3.0.5 C */
  isQuietHours: boolean
  /** Last user-visible error from startSession / endSession (non-401).
   * Null when no error is showing. 401s are handled globally by
   * AuthProvider — we skip them here so we don't double-render the
   * "session expired" UI.
   */
  error: string | null
  /** Moderation redirect resource (crisis hotline, etc.).
   * Set when SSE delivers a moderation frame with verdict 'redirect'.
   * PRD §3.0.5 — self_harm / violence red lines.
   */
  redirectResource: { title: string; url: string } | null
}

const INITIAL_STATE: SandboxState = {
  sessionId: null,
  characterVector: null,
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
  isQuietHours: false,
  error: null,
  redirectResource: null,
}

/**
 * Was this rejection a 401 we already routed through the global auth
 * handler? If so the api client has already emitted `auth-invalid` and
 * AuthProvider is about to unmount us — skip the local banner.
 */
function _isAuthError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401
}

/** Was this a 403 MINOR_QUIET_HOURS rejection? PRD §3.0.5 C */
function _isQuietHoursError(err: unknown): boolean {
  return (
    err instanceof ApiError &&
    err.status === 403 &&
    (err.body as { code?: string })?.code === 'MINOR_QUIET_HOURS'
  )
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
  // No activity after many turns → slacking
  if (state.turnsUsed === 0 && state.started) return 'thinking'
  // Low turns left (≤3) → fired-up urgency
  if (state.turnsLeft <= 3 && state.turnsLeft > 0 && state.turnsUsed > 0) return 'fired-up'
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
  const startSession = useCallback(
    async (req: CreateSessionRequest) => {
      // Wipe any prior error so a retry doesn't show the stale banner.
      setState((s) => (s.error === null ? s : { ...s, error: null }))
      try {
        const res = await apiClient.post<CreateSessionResponse>('/sessions', req)
        setState((s) => ({
          ...s,
          sessionId: res.session_id,
          characterVector: res.character_vector,
          started: true,
          messages: [{ role: 'opponent', text: res.opening_line }],
        }))
      } catch (err) {
        if (_isAuthError(err)) return
        if (_isQuietHoursError(err)) {
          setState((s) => ({ ...s, isQuietHours: true }))
          return
        }
        setState((s) => ({
          ...s,
          error: '加载失败，请稍后重试',
        }))
      }
    },
    [setState],
  )

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
                case 'mood.update':
                  // L3: opponent's live mood after this turn. Swapping
                  // characterVector re-renders the L9 radar to the new
                  // shape (snap — animated frame-to-frame morph is the
                  // L9.2 follow-up). Lands before the deltas so the new
                  // shape is on screen as the reply streams in.
                  return {
                    ...s,
                    characterVector: frame.data,
                  }
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
                case 'moderation': {
                  // H-1: Consume moderation frame — show crisis hotline on redirect
                  if (frame.data.verdict === 'redirect' && frame.data.redirect_resource) {
                    return {
                      ...s,
                      isStreaming: false,
                      streamingText: '',
                      redirectResource: frame.data.redirect_resource,
                    }
                  }
                  // 'block' verdict: stop streaming, show generic message
                  if (frame.data.verdict === 'block') {
                    return {
                      ...s,
                      isStreaming: false,
                      streamingText: '',
                      messages: [
                        ...s.messages,
                        { role: 'opponent', text: '（对话内容未通过审核，请换一个话题）' },
                      ],
                    }
                  }
                  // 'allow' / other: no-op
                  return s
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
        if (_isQuietHoursError(err)) {
          setState((s) => ({
            ...s,
            isStreaming: false,
            streamingText: '',
            isQuietHours: true,
          }))
          return
        }
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
    try {
      const res = await apiClient.post<EndSessionResponse>(
        `/sessions/${state.sessionId}/end`,
      )
      setState((s) => ({ ...s, score: res, isStreaming: false, error: null }))
    } catch (err) {
      if (_isAuthError(err)) {
        // AuthProvider will unmount us; just clear the streaming flag
        // so we don't leave a phantom typing indicator behind.
        setState((s) => ({ ...s, isStreaming: false }))
        return
      }
      setState((s) => ({
        ...s,
        isStreaming: false,
        error: '结算失败，可重试或先退出',
      }))
    }
  }, [state.sessionId, setState])

  /** Dismiss the error banner — user clicked × on the banner. */
  const dismissError = useCallback(() => {
    setState((s) => (s.error === null ? s : { ...s, error: null }))
  }, [setState])

  /** Dismiss the quiet-hours banner — user acknowledged the notice. */
  const dismissQuietHours = useCallback(() => {
    setState((s) => (s.isQuietHours ? { ...s, isQuietHours: false } : s))
  }, [setState])

  /** Dismiss the moderation redirect banner — user acknowledged. */
  const dismissModeration = useCallback(() => {
    setState((s) => (s.redirectResource ? { ...s, redirectResource: null } : s))
  }, [setState])

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
    dismissError,
    dismissQuietHours,
    dismissModeration,
  }
}
