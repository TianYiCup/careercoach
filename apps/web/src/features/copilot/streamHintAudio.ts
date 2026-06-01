/**
 * streamHintAudio — incremental TTS playback via MediaSource.
 *
 * `useHintTts` historically `await res.blob()`'d the whole synthesized
 * clip before playing, so K stayed silent until the *full* synthesis
 * finished. But the backend `POST /v1/tts/synthesize` is a
 * `StreamingResponse` that emits mp3 frames as it synthesizes them —
 * the blob-buffer threw that away. This feeds the response body into a
 * `MediaSource` and starts playback at the *first* chunk, so K begins
 * talking one synthesis-chunk in, not one full clip in. For a hint
 * whose synthesis dominates the wait, that's the `total_ms` → first
 * `chunk_ms` gap the backend latency log already measures.
 *
 * mp3 is a frame stream with no container, so raw `appendBuffer` of each
 * network chunk decodes incrementally; `audio/mpeg` MSE support covers
 * the copilot's Chrome / Edge / Tauri targets. Where MediaSource or the
 * codec is unavailable (Firefox, Safari), `canStreamMp3` returns false
 * and the caller keeps the buffered blob path.
 */

const MP3_MIME = 'audio/mpeg'

/** True when this runtime can stream mp3 through MediaSource. */
export function canStreamMp3(): boolean {
  return (
    typeof MediaSource !== 'undefined' &&
    typeof MediaSource.isTypeSupported === 'function' &&
    MediaSource.isTypeSupported(MP3_MIME)
  )
}

export interface StreamMp3Options {
  /** Aborts the read loop when the hint is superseded / the hook unmounts. */
  signal: AbortSignal
  /** Fired once, when playback actually starts (K becomes audible). */
  onAudible: () => void
}

/**
 * Stream an mp3 `Response` body into `audio` via MediaSource, starting
 * playback at the first chunk. Resolves once the whole body has been
 * appended and playback has begun; rejects on abort (`AbortError`), a
 * browser autoplay block (`NotAllowedError`), or a fatal
 * MediaSource / decode error.
 *
 * The promise settling does NOT mean playback *ended* — for a streamed
 * clip the audio is usually still playing. The caller tracks the real
 * end via the element's `ended` event so `isSpeaking` stays true until
 * the audio stops.
 */
export async function streamMp3ToAudio(
  res: Response,
  audio: HTMLAudioElement,
  { signal, onAudible }: StreamMp3Options,
): Promise<void> {
  const body = res.body
  if (!body) throw new Error('response has no readable body')

  const mediaSource = new MediaSource()
  const objectUrl = URL.createObjectURL(mediaSource)
  audio.src = objectUrl

  // Kicked off after the first chunk lands; awaited at the end so an
  // autoplay block surfaces to the caller. Stored (not awaited in the
  // loop) so reading the next chunk isn't gated on playback starting.
  let playStarted: Promise<void> | null = null

  try {
    await waitForEvent(mediaSource, 'sourceopen', signal)
    const sourceBuffer = mediaSource.addSourceBuffer(MP3_MIME)
    const reader = body.getReader()

    for (;;) {
      const { done, value } = await reader.read()
      throwIfAborted(signal)
      if (done) break
      if (value && value.byteLength > 0) {
        await appendChunk(sourceBuffer, value, signal)
        if (playStarted === null) {
          playStarted = audio.play().then(onAudible)
        }
      }
    }

    if (mediaSource.readyState === 'open') {
      mediaSource.endOfStream()
    }
    if (playStarted !== null) {
      await playStarted
    }
  } finally {
    // A late play() rejection (e.g. an abort raced ahead of the await
    // above) must not surface as an unhandled rejection — the caller
    // already has the abort/error it acted on.
    if (playStarted !== null) {
      playStarted.catch(() => {})
    }
    URL.revokeObjectURL(objectUrl)
  }
}

function throwIfAborted(signal: AbortSignal): void {
  if (signal.aborted) throw new DOMException('aborted', 'AbortError')
}

/** Resolve on the next `event`, or reject if `signal` aborts first. */
function waitForEvent(target: EventTarget, event: string, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('aborted', 'AbortError'))
      return
    }
    const cleanup = () => {
      target.removeEventListener(event, onEvent)
      signal.removeEventListener('abort', onAbort)
    }
    const onEvent = () => {
      cleanup()
      resolve()
    }
    const onAbort = () => {
      cleanup()
      reject(new DOMException('aborted', 'AbortError'))
    }
    target.addEventListener(event, onEvent, { once: true })
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

/**
 * Append one chunk and resolve on `updateend`. A `SourceBuffer` can't
 * accept a new chunk while it's still processing the previous one, so
 * the caller must await this before the next append.
 */
function appendChunk(
  sourceBuffer: SourceBuffer,
  chunk: Uint8Array,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      sourceBuffer.removeEventListener('updateend', onUpdateEnd)
      sourceBuffer.removeEventListener('error', onError)
      signal.removeEventListener('abort', onAbort)
    }
    const onUpdateEnd = () => {
      cleanup()
      resolve()
    }
    const onError = () => {
      cleanup()
      reject(new Error('SourceBuffer append failed'))
    }
    const onAbort = () => {
      cleanup()
      reject(new DOMException('aborted', 'AbortError'))
    }
    sourceBuffer.addEventListener('updateend', onUpdateEnd, { once: true })
    sourceBuffer.addEventListener('error', onError, { once: true })
    signal.addEventListener('abort', onAbort, { once: true })
    try {
      // MSE chunks come from a fetch ReadableStream — always ArrayBuffer-backed,
      // never SharedArrayBuffer — so the BufferSource cast is safe. TS 5.7 made
      // Uint8Array generic over its backing buffer (Uint8Array<ArrayBufferLike>),
      // which no longer structurally matches appendBuffer's ArrayBuffer-only type.
      sourceBuffer.appendBuffer(chunk as BufferSource)
    } catch (err) {
      cleanup()
      reject(err instanceof Error ? err : new Error('appendBuffer threw'))
    }
  })
}
