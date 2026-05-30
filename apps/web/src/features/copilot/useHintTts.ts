/**
 * useHintTts — speaks a finalized coach hint via `POST /v1/tts/synthesize`.
 *
 * PRD US-B2 calls the copilot 耳机里挺你 — the whole point is hearing
 * K's line in the headphones, not reading it. Backend is fully wired
 * (Edge → Aliyun failover, moderation, adult-only gate) but the React
 * page only renders the hint as text. This hook closes the loop:
 *
 *   1. Watch the finalized hint (`hintText`). Streaming deltas are
 *      intentionally ignored — re-synthesising every keystroke would
 *      hammer the vendor and produce glitchy audio.
 *   2. On change, POST the text to /v1/tts/synthesize, read the audio
 *      blob, and play it through a single shared `<audio>` element.
 *   3. When the next hint arrives mid-playback, abort the in-flight
 *      fetch *and* stop the current playback so two K lines never
 *      overlap. Browser autoplay rejection is logged as a soft error
 *      (text is still on screen), not surfaced as a user-facing crash.
 *
 * The hook deliberately doesn't depend on `apiClient` because the
 * client hardcodes `.json()`; TTS responds with binary audio and we
 * need the raw `Response.blob()`. We reach into `auth-token` directly
 * for the bearer the same way the client does internally.
 */

import { useEffect, useRef, useState } from 'react'
import { getAuthToken } from '../../api/v1/auth-token'
import { markCopilotLatency } from './latency-log'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/v1'
const TTS_PATH = '/tts/synthesize'

export interface UseHintTtsOptions {
  /** The finalized hint text, or null if no hint is current. */
  hintText: string | null
  /** When true, skip TTS entirely — used for the user-facing mute toggle. */
  muted?: boolean
}

export type HintTtsErrorKind =
  | 'autoplay_blocked'
  | 'tts_unavailable'
  | 'tts_blocked'
  | 'auth_required'
  | 'network'

export interface HintTtsState {
  /** True while audio is fetching or playing. */
  isSpeaking: boolean
  /** Last non-fatal failure. Text is still visible regardless. */
  error: HintTtsErrorKind | null
}

const INITIAL: HintTtsState = { isSpeaking: false, error: null }

function classifyHttpError(status: number): HintTtsErrorKind {
  if (status === 401 || status === 403) return 'auth_required'
  if (status === 422) return 'tts_blocked'
  if (status >= 500) return 'tts_unavailable'
  return 'tts_unavailable'
}

export function useHintTts({ hintText, muted = false }: UseHintTtsOptions): HintTtsState {
  const [asyncState, setAsyncState] = useState<HintTtsState>(INITIAL)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const skip = !hintText || muted

  useEffect(() => {
    if (skip) return

    const ac = new AbortController()
    let objectUrl: string | null = null
    let cancelled = false

    // Mark the loading state so the HUD can show "SPEAK" while we wait
    // for the audio. Derivation isn't an option here because the signal
    // we'd derive from (an in-flight fetch) only exists inside this
    // effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAsyncState({ isSpeaking: true, error: null })

    const run = async () => {
      const token = getAuthToken()
      let res: Response
      try {
        res = await fetch(`${BASE_URL}${TTS_PATH}`, {
          method: 'POST',
          signal: ac.signal,
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ text: hintText, audio_format: 'mp3' }),
        })
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === 'AbortError')) return
        setAsyncState({ isSpeaking: false, error: 'network' })
        return
      }

      if (!res.ok) {
        if (cancelled) return
        setAsyncState({ isSpeaking: false, error: classifyHttpError(res.status) })
        return
      }

      let blob: Blob
      try {
        blob = await res.blob()
      } catch {
        if (cancelled) return
        setAsyncState({ isSpeaking: false, error: 'network' })
        return
      }
      if (cancelled) return

      objectUrl = URL.createObjectURL(blob)
      const audio = audioRef.current ?? (audioRef.current = new Audio())
      audio.src = objectUrl
      audio.onended = () => {
        if (!cancelled) setAsyncState({ isSpeaking: false, error: null })
      }

      try {
        await audio.play()
        // End of the latency waterfall: K is now audible. The gap from
        // the `hint_done` mark to here is the TTS fetch + synth + decode
        // the user waits through after the text is already on screen.
        markCopilotLatency('hint_audible')
      } catch {
        if (cancelled) return
        // Browser blocked autoplay — the hint text is still on screen,
        // so we mark this as non-fatal and let the parent decide
        // whether to nudge the user (e.g. show an "enable audio" tip).
        setAsyncState({ isSpeaking: false, error: 'autoplay_blocked' })
      }
    }

    void run()

    return () => {
      cancelled = true
      ac.abort()
      const audio = audioRef.current
      if (audio) {
        audio.pause()
        audio.removeAttribute('src')
      }
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl)
      }
    }
  }, [hintText, muted, skip])

  return skip ? INITIAL : asyncState
}
