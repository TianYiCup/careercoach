/**
 * PCM helpers — the wire format the copilot backend ASR expects.
 *
 * `apps/api/app/asr/aliyun.py` pins the realtime ASR to raw
 * **16 kHz mono signed-16-bit little-endian PCM**. The browser's Web
 * Audio graph hands us `Float32Array` samples in [-1, 1]; these two
 * pure functions are the only conversion between the two worlds, kept
 * out of the AudioWorklet so they can be unit-tested without a real
 * audio context.
 */

/** Below this RMS a frame is treated as silence (used for VAD). */
export const SILENCE_RMS_THRESHOLD = 0.012

/** Above this RMS a frame counts as speech (hysteresis vs silence). */
export const SPEECH_RMS_THRESHOLD = 0.02

/**
 * Convert Float32 samples in [-1, 1] to signed 16-bit PCM.
 *
 * Out-of-range inputs are clamped before scaling so a hot mic can't
 * wrap around into noise. Negative and positive halves use the
 * asymmetric Int16 range (-32768..32767), matching the convention
 * every ASR vendor decodes against.
 */
export function floatTo16BitPCM(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length)
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]!))
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return out
}

/**
 * Root-mean-square amplitude of a frame, in [0, 1].
 *
 * Drives the voice-activity heuristic that segments utterances: a run
 * of sub-threshold frames after speech marks the end of a sentence so
 * the client can send `audio_end` hands-free. Empty input is 0 (no
 * energy) rather than NaN.
 */
export function rms(input: Float32Array): number {
  if (input.length === 0) return 0
  let sum = 0
  for (let i = 0; i < input.length; i++) {
    const s = input[i]!
    sum += s * s
  }
  return Math.sqrt(sum / input.length)
}
