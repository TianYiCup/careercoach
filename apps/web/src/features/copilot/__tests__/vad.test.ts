/**
 * Unit tests for the adaptive VAD.
 *
 * @vitest-environment node
 */

import { describe, expect, it } from 'vitest'

import { createVad, DEFAULT_VAD_CONFIG } from '../vad'

const CFG = { ...DEFAULT_VAD_CONFIG }

/** Feed a run of identical levels at 100ms frame spacing from `startMs`. */
function feed(vad: ReturnType<typeof createVad>, level: number, frames: number, startMs: number) {
  let ended = false
  let t = startMs
  for (let i = 0; i < frames; i++) {
    if (vad.process(level, t)) ended = true
    t += 100
  }
  return { ended, nextMs: t }
}

describe('createVad', () => {
  it('does not fire before any speech', () => {
    const vad = createVad(CFG)
    const { ended } = feed(vad, 0.04, 30, 0) // pure noise floor, 3s
    expect(ended).toBe(false)
  })

  it('fires once after sustained speech followed by trailing silence', () => {
    const vad = createVad(CFG)
    // Settle floor near 0.04 (AGC-pumped noise), then a sustained voice
    // run (≥ speechFramesToLatch), then quiet.
    feed(vad, 0.04, 5, 0)
    feed(vad, 0.2, 4, 500) // sustained speech, latches
    // Back to noise floor; boundary after silenceHoldMs (500ms).
    const { ended } = feed(vad, 0.04, 12, 900)
    expect(ended).toBe(true)
  })

  it('adapts to a high AGC noise floor (0.04) and still detects silence', () => {
    const vad = createVad(CFG)
    // Floor ~0.04 — above the OLD fixed silence threshold (0.012), which
    // is exactly the case that used to wedge the detector permanently.
    feed(vad, 0.04, 10, 0)
    feed(vad, 0.16, 4, 1000) // sustained speech
    const { ended } = feed(vad, 0.04, 12, 1400)
    expect(ended).toBe(true)
  })

  it('does NOT latch on single-frame noise spikes (debounce)', () => {
    const vad = createVad(CFG)
    feed(vad, 0.04, 5, 0)
    // Isolated spikes separated by floor frames — never 3 in a row.
    let ended = false
    let t = 500
    for (let i = 0; i < 20; i++) {
      const level = i % 2 === 0 ? 0.2 : 0.04 // alternating spike/floor
      if (vad.process(level, t)) ended = true
      t += 100
    }
    expect(ended).toBe(false)
  })

  it('does not fire on silence that never had speech', () => {
    const vad = createVad(CFG)
    feed(vad, 0.04, 5, 0)
    const { ended } = feed(vad, 0.041, 20, 500) // tiny wobble, no speech
    expect(ended).toBe(false)
  })

  it('fires only once per utterance, not repeatedly', () => {
    const vad = createVad(CFG)
    feed(vad, 0.03, 5, 0)
    feed(vad, 0.2, 4, 500) // sustained speech
    let count = 0
    let t = 900
    for (let i = 0; i < 30; i++) {
      if (vad.process(0.03, t)) count++
      t += 100
    }
    expect(count).toBe(1)
  })

  it('handles a second utterance after the first ends', () => {
    const vad = createVad(CFG)
    feed(vad, 0.03, 5, 0)
    feed(vad, 0.2, 4, 500) // sustained speech
    const first = feed(vad, 0.03, 12, 900)
    expect(first.ended).toBe(true)

    const speak2 = feed(vad, 0.2, 4, first.nextMs) // sustained speech again
    const second = feed(vad, 0.03, 12, speak2.nextMs)
    expect(second.ended).toBe(true)
  })

  it('exposes the tracked floor near the ambient level', () => {
    const vad = createVad(CFG)
    feed(vad, 0.05, 20, 0)
    expect(vad.floor).toBeCloseTo(0.05, 2)
  })
})
