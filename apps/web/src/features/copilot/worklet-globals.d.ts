/**
 * Minimal ambient declarations for the `AudioWorkletGlobalScope`.
 *
 * The worklet processor (`pcm-worklet.ts`) runs in a separate global
 * scope that the standard `dom` lib doesn't describe, and we don't want
 * to pull in the full `@types/audioworklet` package just for two
 * symbols. These cover exactly what `pcm-worklet.ts` references.
 */

declare const sampleRate: number

declare class AudioWorkletProcessor {
  readonly port: MessagePort
  constructor()
  process(
    inputs: Float32Array[][],
    outputs: Float32Array[][],
    parameters: Record<string, Float32Array>,
  ): boolean
}

declare function registerProcessor(
  name: string,
  processorCtor: new () => AudioWorkletProcessor,
): void
