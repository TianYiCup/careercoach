/**
 * Unit tests for the dev-only copilot latency logger.
 *
 * @vitest-environment node
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { markCopilotLatency } from '../latency-log'

describe('markCopilotLatency', () => {
  let debugSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
  })

  afterEach(() => {
    debugSpy.mockRestore()
    vi.restoreAllMocks()
  })

  it('logs every stage with the copilot-latency prefix', () => {
    markCopilotLatency('audio_end')
    markCopilotLatency('asr_final')
    markCopilotLatency('hint_done')
    markCopilotLatency('hint_audible')

    expect(debugSpy).toHaveBeenCalledTimes(4)
    for (const call of debugSpy.mock.calls) {
      expect(String(call[0])).toContain('[copilot-latency]')
    }
  })

  it('reports a delta in ms for stages after audio_end', () => {
    // Pin the clock so the delta is deterministic, not a flaky timing read.
    const nowSpy = vi.spyOn(performance, 'now')
    nowSpy.mockReturnValueOnce(1000) // audio_end (baseline)
    nowSpy.mockReturnValueOnce(1420) // asr_final

    markCopilotLatency('audio_end')
    markCopilotLatency('asr_final')

    expect(String(debugSpy.mock.calls[0]?.[0])).toContain('turn start')
    expect(String(debugSpy.mock.calls[1]?.[0])).toContain('+420ms')
  })

  it('anchors the delta baseline to the most recent audio_end', () => {
    const nowSpy = vi.spyOn(performance, 'now')
    nowSpy.mockReturnValueOnce(5000) // audio_end for a fresh turn
    nowSpy.mockReturnValueOnce(5100) // asr_final

    markCopilotLatency('audio_end')
    markCopilotLatency('asr_final')

    expect(String(debugSpy.mock.calls[1]?.[0])).toContain('+100ms')
  })
})
