import { getAuthToken } from './auth-token';
import type { SseEventFrame } from './types';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/v1';

/**
 * POST SSE — sends a POST request and consumes the text/event-stream response.
 *
 * EventSource only supports GET, so we use fetch + ReadableStream manual parsing.
 * Frame format: `event: <name>\ndata: <json>\n\n`
 *
 * Authorization header is read fresh from storage on every call so a
 * logout in another tab is picked up before the next /turns request.
 */
export async function postSSE(
  path: string,
  body: unknown,
  onFrame: (frame: SseEventFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${BASE_URL}${path}`;
  const token = getAuthToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: res.statusText }));
    throw new Error(
      typeof error === 'object' && error !== null && 'message' in error
        ? String((error as { message: unknown }).message)
        : `API Error ${res.status}`,
    );
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error('No response body');

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Split on double-newline (frame boundary)
    const parts = buffer.split('\n\n');
    // Last part may be incomplete; keep it in buffer
    buffer = parts.pop() ?? '';

    for (const part of parts) {
      const frame = parseSseFrame(part);
      if (frame) onFrame(frame);
    }
  }

  // Process any remaining buffer
  if (buffer.trim()) {
    const frame = parseSseFrame(buffer);
    if (frame) onFrame(frame);
  }
}

/** Parse a single SSE frame string into a typed SseEventFrame */
function parseSseFrame(raw: string): SseEventFrame | null {
  let event = '';
  let data = '';

  for (const line of raw.split('\n')) {
    if (line.startsWith('event: ')) {
      event = line.slice(7).trim();
    } else if (line.startsWith('data: ')) {
      data = line.slice(6);
    }
  }

  if (!event || !data) return null;

  try {
    const parsed = JSON.parse(data);
    return { event, data: parsed } as SseEventFrame;
  } catch {
    return null;
  }
}
