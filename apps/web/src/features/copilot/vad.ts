/**
 * Adaptive voice-activity detection for the copilot mic stream.
 *
 * Segmenting utterances by an *absolute* RMS threshold is fragile: a
 * mic with `autoGainControl` pumps the noise floor up (we measured
 * ~0.04 RMS on a "silent" room), which sits above any fixed silence
 * cutoff — so end-of-speech is never detected and `audio_end` never
 * fires. This detector instead tracks the noise floor continuously and
 * defines speech/silence *relative* to it, so it adapts to any mic gain
 * or room.
 *
 * Model:
 *   - `floor` follows the quietest recent level: it drops instantly to
 *     any new minimum and rises slowly (EMA) when levels climb, so it
 *     settles near the ambient noise during pauses.
 *   - speech  := level > floor + speechMargin
 *   - silence := level < floor + silenceMargin   (silenceMargin <
 *     speechMargin gives hysteresis so jitter doesn't flap)
 *   - an utterance ends after `silenceHoldMs` of continuous silence
 *     *following* detected speech.
 */

export interface VadConfig {
  /** Level above the noise floor that counts as speech. */
  speechMargin: number
  /** Level above the noise floor below which counts as silence. */
  silenceMargin: number
  /** Trailing silence (ms) after speech that ends an utterance. */
  silenceHoldMs: number
  /** EMA weight for the floor rising toward louder levels (0..1). */
  floorRise: number
  /**
   * Consecutive above-threshold frames required to *latch* speech. A
   * single noisy frame shouldn't open an utterance — that's what makes
   * the detector fire spuriously every few seconds on an AGC mic. At
   * ~100 ms/frame, 3 frames ≈ 300 ms of sustained voice.
   */
  speechFramesToLatch: number
}

export const DEFAULT_VAD_CONFIG: VadConfig = {
  speechMargin: 0.04,
  silenceMargin: 0.015,
  silenceHoldMs: 800,
  floorRise: 0.01,
  speechFramesToLatch: 3,
}

export interface Vad {
  /**
   * Feed one frame's RMS level at time `nowMs`. Returns true exactly
   * once when an utterance boundary is crossed (speech → sustained
   * silence), so the caller can send `audio_end` then.
   */
  process(level: number, nowMs: number): boolean
  /** Current noise-floor estimate — useful for a VU meter / debugging. */
  readonly floor: number
}

export function createVad(config: VadConfig = DEFAULT_VAD_CONFIG): Vad {
  let floor: number | null = null
  let hasSpeech = false
  let silenceStartedAt: number | null = null
  let speechRun = 0 // consecutive above-threshold frames, pre-latch

  return {
    get floor() {
      return floor ?? 0
    },
    process(level: number, nowMs: number): boolean {
      // Seed the floor on the first frame so the initial margin is
      // measured against a real baseline, not 0.
      if (floor === null) {
        floor = level
        return false
      }
      // Drop instantly to a new minimum; rise slowly otherwise.
      floor = level < floor ? level : floor * (1 - config.floorRise) + level * config.floorRise

      if (level > floor + config.speechMargin) {
        // Require a sustained run before latching speech — kills the
        // single-frame noise spikes that caused spurious utterances.
        speechRun += 1
        if (speechRun >= config.speechFramesToLatch) {
          hasSpeech = true
          silenceStartedAt = null
        }
        return false
      }

      // Dropped below the speech threshold — reset the pre-latch run so
      // scattered spikes don't accumulate toward a latch.
      speechRun = 0

      if (hasSpeech && level < floor + config.silenceMargin) {
        if (silenceStartedAt === null) {
          silenceStartedAt = nowMs
        } else if (nowMs - silenceStartedAt >= config.silenceHoldMs) {
          hasSpeech = false
          silenceStartedAt = null
          return true
        }
      }
      return false
    },
  }
}
