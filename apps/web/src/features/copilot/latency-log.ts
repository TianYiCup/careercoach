/**
 * Dev-only latency marks for the copilot pipeline (perf P0).
 *
 * The user-perceived delay — opponent stops talking → K's voice in the
 * headphones — is the sum of several stages that live in different
 * places (VAD in the worklet, ASR/hint over the WS, TTS over a separate
 * fetch). The backend logs its half as structured `*_latency` lines; this
 * is the client half: one mark per stage boundary so a developer can read
 * the waterfall straight out of the console and see which stage dominates.
 *
 *   audio_end (VAD fired) → asr_final → hint_done → hint_audible
 *
 * Each mark prints the delta from the previous mark, so consecutive lines
 * read as stage durations. Marks are wall-clock via `performance.now()`
 * (monotonic, sub-ms). The running `lastMarkMs` is module-global, so
 * overlapping utterances (a hint for turn N still synthesizing when turn
 * N+1's `audio_end` fires) interleave — fine for eyeballing a single turn
 * in dev, which is all this is for.
 *
 * Gated on `import.meta.env.DEV`: in a production build the calls compile
 * to a guard that Vite tree-shakes, so no `console` noise ships and the
 * "no console.log in production" rule holds.
 */

/** Pipeline stage boundaries, in the order they fire for one turn. */
export type CopilotLatencyEvent = 'audio_end' | 'asr_final' | 'hint_done' | 'hint_audible'

const LOG_PREFIX = '[copilot-latency]'

let lastMarkMs: number | null = null

/**
 * Record one pipeline stage boundary. No-op outside dev. `audio_end`
 * resets the delta baseline so each turn's waterfall starts clean.
 */
export function markCopilotLatency(event: CopilotLatencyEvent): void {
  if (!import.meta.env.DEV) return

  const now = performance.now()
  if (event === 'audio_end') {
    lastMarkMs = now
    // eslint-disable-next-line no-console
    console.debug(`${LOG_PREFIX} ${event} (turn start)`)
    return
  }

  const delta = lastMarkMs === null ? null : now - lastMarkMs
  lastMarkMs = now
  // eslint-disable-next-line no-console
  console.debug(
    `${LOG_PREFIX} ${event} ${delta === null ? '(no baseline)' : `+${delta.toFixed(0)}ms`}`,
  )
}
