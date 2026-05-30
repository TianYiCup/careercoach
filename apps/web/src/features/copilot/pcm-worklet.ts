/**
 * AudioWorklet processor — the real-time tap on the microphone graph.
 *
 * Runs on the audio render thread (not the main thread), so it must
 * stay allocation-light and never block. It accumulates ~100 ms of
 * audio at the context's NATIVE sample rate, downsamples that chunk to
 * 16 kHz, converts to 16-bit PCM, and `postMessage`s the raw bytes +
 * the frame's RMS back to the main thread, which forwards the bytes
 * over the copilot WebSocket and uses the RMS for voice-activity
 * detection.
 *
 * Why native rate + resample instead of forcing a 16 kHz context:
 * `new AudioContext({ sampleRate: 16000 })` makes Chromium's
 * MediaStreamSource emit *silence* on many machines (frames flow but
 * every sample is 0). Capturing at the hardware rate and resampling
 * here is the robust path. `sampleRate` is the AudioWorkletGlobalScope
 * global carrying the actual context rate.
 *
 * Loaded via `audioContext.audioWorklet.addModule(...?worker&url)`;
 * Vite bundles this entry and resolves the `./pcm` import into the
 * worklet chunk. Not unit-tested — AudioWorklet has no jsdom shim; the
 * pure helpers it delegates to (`./pcm`) carry the coverage instead.
 */

import { floatTo16BitPCM, resampleLinear, rms } from './pcm'

const TARGET_RATE = 16000

// ~100 ms of native audio per emitted frame keeps the WS message rate
// sane without adding meaningful latency to the hint loop.
const FRAME_MS = 0.1

class PcmEncoderProcessor extends AudioWorkletProcessor {
  private buffer: Float32Array
  private offset = 0

  constructor() {
    super()
    this.buffer = new Float32Array(Math.round(sampleRate * FRAME_MS))
  }

  process(inputs: Float32Array[][]): boolean {
    const channel = inputs[0]?.[0]
    // No input connected this quantum (e.g. track muted) — keep the
    // node alive and wait for audio to resume.
    if (!channel || channel.length === 0) return true

    for (let i = 0; i < channel.length; i++) {
      this.buffer[this.offset++] = channel[i]!
      if (this.offset === this.buffer.length) {
        this.flush()
      }
    }
    return true
  }

  private flush(): void {
    const native = this.buffer.subarray(0, this.offset)
    const down =
      sampleRate === TARGET_RATE ? native : resampleLinear(native, sampleRate, TARGET_RATE)
    const level = rms(down)
    const pcm = floatTo16BitPCM(down)
    // Transfer the ArrayBuffer so the main thread owns it without a
    // copy. `pcm` is freshly allocated each flush, so handing it off is
    // safe — nothing else references it.
    this.port.postMessage({ pcm: pcm.buffer, level }, [pcm.buffer])
    this.offset = 0
  }
}

registerProcessor('pcm-encoder', PcmEncoderProcessor)
