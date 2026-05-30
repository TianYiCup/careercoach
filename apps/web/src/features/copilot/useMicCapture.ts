/**
 * useMicCapture — the missing upper half of the copilot loop.
 *
 * PRD §7.5 / US-B2: the copilot listens to the *opponent* through the
 * mic, transcribes it, and whispers K's hint into the headphones. The
 * backend WS bridge (`apps/api/app/routes/v1/copilot.py`) has waited
 * for audio bytes since A-18; nothing ever fed it because the web app
 * had no microphone capture at all. This hook closes that gap:
 *
 *   getUserMedia → AudioContext (native rate) → AudioWorklet(pcm-encoder)
 *     → per ~100 ms frame: onFrame(rawPcmBytes)  // → ws.send(...)
 *     → trailing-silence VAD: onUtteranceEnd()    // → send audio_end
 *
 * The backend ASR (`app/asr/aliyun.py`) is pinned to **16 kHz mono
 * signed-16-bit PCM**. We do NOT force the AudioContext to 16 kHz —
 * that makes Chromium's MediaStreamSource emit silence on many
 * machines — so the context runs at the hardware rate and the worklet
 * downsamples to 16 kHz before emitting that wire format. No
 * MediaRecorder either (its webm/opus container is wrong for streaming
 * ASR).
 *
 * Voice-activity detection segments utterances hands-free: once a frame
 * crosses the speech threshold, a following run of sub-silence frames
 * (`SILENCE_HOLD_MS`) fires `onUtteranceEnd` so the caller can finalize
 * the current sentence without the user tapping a button mid-conversation.
 *
 * Adult-only / minor protection is enforced upstream at session
 * creation (`require_adult`, R-15); this hook only runs after a session
 * exists, so it carries no age logic of its own.
 */

import { useEffect, useRef, useState } from 'react'

import { createVad } from './vad'
// `?worker&url` makes Vite bundle the worklet (resolving its `./pcm`
// import) into a standalone ES module chunk and hand back its URL.
// A bare `new URL('./pcm-worklet.ts', import.meta.url)` is NOT bundled
// for .ts entries and 404s in a production build.
import pcmWorkletUrl from './pcm-worklet.ts?worker&url'

const WORKLET_NAME = 'pcm-encoder'

export type MicErrorKind =
  | 'permission_denied'
  | 'no_device'
  | 'insecure_context'
  | 'unsupported'
  | 'unknown'

export interface UseMicCaptureOptions {
  /** Capture runs while true; flipping to false tears everything down. */
  active: boolean
  /** Raw 16-bit PCM bytes for one ~100 ms frame, ready for `ws.send`. */
  onFrame: (pcm: ArrayBuffer) => void
  /** Fired after a run of silence following speech (auto audio_end). */
  onUtteranceEnd?: () => void
  /** Non-fatal capture failure (permission, device, unsupported). */
  onError?: (kind: MicErrorKind) => void
}

export interface MicCaptureState {
  /** True once the worklet is live and frames are flowing. */
  isCapturing: boolean
  /** Latest frame RMS in [0, 1] — drive a VU meter / "listening" pulse. */
  level: number
  /** Last capture error, or null. Cleared on a fresh start. */
  error: MicErrorKind | null
}

interface FrameMessage {
  pcm: ArrayBuffer
  level: number
}

const INITIAL: MicCaptureState = { isCapturing: false, level: 0, error: null }

function classifyGetUserMediaError(err: unknown): MicErrorKind {
  if (err instanceof DOMException) {
    if (err.name === 'NotAllowedError' || err.name === 'SecurityError') {
      return 'permission_denied'
    }
    if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') {
      return 'no_device'
    }
  }
  return 'unknown'
}

/** Everything a running capture session holds, torn down together. */
interface CaptureResources {
  stream: MediaStream
  context: AudioContext
  source: MediaStreamAudioSourceNode
  node: AudioWorkletNode
}

export function useMicCapture({
  active,
  onFrame,
  onUtteranceEnd,
  onError,
}: UseMicCaptureOptions): MicCaptureState {
  const [state, setState] = useState<MicCaptureState>(INITIAL)

  // Callbacks are stashed in refs so the start/stop effect depends only
  // on `active` — a parent re-render that produces new callback
  // identities must not tear down and re-acquire the microphone.
  const onFrameRef = useRef(onFrame)
  const onUtteranceEndRef = useRef(onUtteranceEnd)
  const onErrorRef = useRef(onError)
  // Keep the latest callbacks without retriggering the capture effect.
  // Synced in an effect (not during render) per react-hooks/refs.
  useEffect(() => {
    onFrameRef.current = onFrame
    onUtteranceEndRef.current = onUtteranceEnd
    onErrorRef.current = onError
  })

  useEffect(() => {
    if (!active) return

    let resources: CaptureResources | null = null
    let cancelled = false
    // Adaptive VAD — tracks the noise floor so end-of-speech is detected
    // regardless of mic gain / AGC (a fixed threshold wedges on an
    // AGC-pumped floor and never fires audio_end).
    const vad = createVad()

    const handleFrame = ({ pcm, level }: FrameMessage) => {
      if (cancelled) return
      onFrameRef.current(pcm)
      setState((s) => (s.level === level ? s : { ...s, level }))
      if (vad.process(level, Date.now())) {
        onUtteranceEndRef.current?.()
      }
    }

    const fail = (kind: MicErrorKind) => {
      if (cancelled) return
      setState({ isCapturing: false, level: 0, error: kind })
      onErrorRef.current?.(kind)
    }

    const start = async () => {
      const mediaDevices = navigator.mediaDevices
      if (!mediaDevices?.getUserMedia || typeof AudioContext === 'undefined') {
        fail('unsupported')
        return
      }
      // getUserMedia is only available in secure contexts; surfacing
      // this distinctly lets the UI tell the user "needs https" rather
      // than a generic failure.
      if (typeof isSecureContext !== 'undefined' && !isSecureContext) {
        fail('insecure_context')
        return
      }

      let stream: MediaStream
      try {
        stream = await mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        })
      } catch (err) {
        fail(classifyGetUserMediaError(err))
        return
      }
      if (cancelled) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }

      try {
        // Native rate — NOT forced to 16kHz: a forced rate makes
        // Chromium's MediaStreamSource emit silence on many machines.
        // The worklet downsamples to 16kHz instead.
        const context = new AudioContext()
        await context.audioWorklet.addModule(pcmWorkletUrl)
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          await context.close()
          return
        }
        const source = context.createMediaStreamSource(stream)
        const node = new AudioWorkletNode(context, WORKLET_NAME)
        node.port.onmessage = (e: MessageEvent<FrameMessage>) => handleFrame(e.data)
        source.connect(node)
        // The render graph only schedules nodes that reach a sink — a
        // worklet connected to nothing never has process() called, so
        // no frames flow. Route it to the destination to keep it pulled.
        // This does NOT echo: process() leaves its output buffers
        // untouched, so what reaches the speakers is pure silence.
        node.connect(context.destination)
        // Created after `await getUserMedia`, so we're off the user-
        // gesture stack and the context can start suspended — resume so
        // the render thread actually runs the worklet.
        if (context.state === 'suspended') {
          await context.resume()
        }
        resources = { stream, context, source, node }
        setState({ isCapturing: true, level: 0, error: null })
      } catch {
        stream.getTracks().forEach((t) => t.stop())
        fail('unknown')
      }
    }

    void start()

    return () => {
      cancelled = true
      if (resources) {
        resources.node.port.onmessage = null
        resources.node.disconnect()
        resources.source.disconnect()
        resources.stream.getTracks().forEach((t) => t.stop())
        void resources.context.close()
        resources = null
      }
      setState(INITIAL)
    }
  }, [active])

  return state
}
