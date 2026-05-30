/**
 * AudioWorklet processor — the real-time tap on the microphone graph.
 *
 * Runs on the audio render thread (not the main thread), so it must
 * stay allocation-light and never block. Per 128-sample render quantum
 * it accumulates Float32 samples; once a full ~100 ms frame is buffered
 * it converts to 16-bit PCM and `postMessage`s the raw bytes + the
 * frame's RMS back to the main thread, which forwards the bytes over
 * the copilot WebSocket and uses the RMS for voice-activity detection.
 *
 * Batching to ~100 ms (instead of shipping every 8 ms quantum) keeps
 * the WS message rate sane without adding meaningful latency to the
 * "耳机里挺你" hint loop.
 *
 * Loaded via `audioContext.audioWorklet.addModule(new URL(...))`; Vite
 * bundles this entry and resolves the `./pcm` import into the worklet
 * chunk. Not unit-tested — AudioWorklet has no jsdom shim; the pure
 * conversion it delegates to (`./pcm`) carries the coverage instead.
 */

import { floatTo16BitPCM, rms } from './pcm'

// At the context's fixed 16 kHz, 1600 samples ≈ 100 ms per emitted frame.
const FRAME_SAMPLES = 1600

class PcmEncoderProcessor extends AudioWorkletProcessor {
  private buffer = new Float32Array(FRAME_SAMPLES)
  private offset = 0

  process(inputs: Float32Array[][]): boolean {
    const channel = inputs[0]?.[0]
    // No input connected this quantum (e.g. track muted) — keep the
    // node alive and wait for audio to resume.
    if (!channel || channel.length === 0) return true

    for (let i = 0; i < channel.length; i++) {
      this.buffer[this.offset++] = channel[i]!
      if (this.offset === FRAME_SAMPLES) {
        this.flush()
      }
    }
    return true
  }

  private flush(): void {
    const frame = this.buffer.subarray(0, this.offset)
    const level = rms(frame)
    const pcm = floatTo16BitPCM(frame)
    // Transfer the ArrayBuffer so the main thread owns it without a
    // copy. `pcm` is freshly allocated each flush, so handing it off is
    // safe — nothing else references it.
    this.port.postMessage({ pcm: pcm.buffer, level }, [pcm.buffer])
    this.offset = 0
  }
}

registerProcessor('pcm-encoder', PcmEncoderProcessor)
