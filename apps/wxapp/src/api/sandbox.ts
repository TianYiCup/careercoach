/**
 * 沙盘会话 API — 小程序端
 *
 * 小程序没有 fetch/EventSource，SSE 通过 wx.request + enableChunked 实现。
 * 后端 POST /v1/sessions/:id/turns 返回 SSE 流，
 * 小程序端收到 chunked 响应后手动解析 event/data 帧。
 */

import { API_BASE } from './config'

// --- Types (aligned with packages/shared + web api/v1/types) ---

export type ToneLevel = 'safe' | 'aggro' | 'fun'
export type ScoreResult = 'shenfeng' | 'guolu' | 'fanche'

export interface CreateSessionResponse {
  session_id: string
  opening_line: string
}

export interface Score {
  aura: number
  logic: number
  emotion: number
  professionalism: number
  goal_achieve: number
  highlights: string
  failures: string
  result: ScoreResult
}

export interface EndSessionResponse {
  score: Score
  weakness_updates: { tag: string; delta: number }[]
}

export type SseEventFrame =
  | { event: 'opponent.delta'; data: { text: string } }
  | { event: 'opponent.done'; data: { turn_id: string; full_text: string } }
  | { event: 'coach.hint'; data: { safe: string; aggressive: string; humor: string } }
  | { event: 'meta'; data: { turns_used: number; turns_left: number } }

export interface ChatMessage {
  role: 'opponent' | 'user'
  text: string
}

// --- Auth token helper ---

function getAuthToken(): string {
  try {
    return wx.getStorageSync('auth_token') as string || ''
  } catch {
    return ''
  }
}

function authHeader(): Record<string, string> {
  const token = getAuthToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// --- Simple request wrapper ---

function request<T>(
  path: string,
  method: 'GET' | 'POST' = 'GET',
  data?: unknown,
): Promise<T> {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}${path}`,
      method,
      data,
      header: {
        'Content-Type': 'application/json',
        ...authHeader(),
      },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data as T)
        } else {
          reject(new Error(`API ${method} ${path} → ${res.statusCode}`))
        }
      },
      fail: (err) => {
        console.error(`[API] ${method} ${path} failed:`, err)
        reject(err)
      },
    })
  })
}

// --- Session API ---

/** Create a new sandbox session */
export function createSession(body: {
  mode: string
  scenario_id: string
  persona_id: string
  user_goal: string
}) {
  return request<CreateSessionResponse>('/v1/sessions', 'POST', body)
}

/** End a session and get score */
export function endSession(sessionId: string) {
  return request<EndSessionResponse>(
    `/v1/sessions/${sessionId}/end`,
    'POST',
  )
}

// --- SSE via wx.request + enableChunked ---

/**
 * Parse SSE text chunk into event frames.
 * SSE format: "event: <name>\ndata: <json>\n\n"
 * Chunks may contain partial or multiple events.
 */
export function parseSseChunk(chunk: string): SseEventFrame[] {
  const frames: SseEventFrame[] = []
  const blocks = chunk.split('\n\n')

  for (const block of blocks) {
    if (!block.trim()) continue
    let eventName = ''
    let dataStr = ''
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) {
        eventName = line.slice(7).trim()
      } else if (line.startsWith('data: ')) {
        dataStr = line.slice(6)
      }
    }
    if (!eventName || !dataStr) continue

    try {
      const data = JSON.parse(dataStr)
      if (eventName === 'opponent.delta') {
        frames.push({ event: 'opponent.delta', data: data as { text: string } })
      } else if (eventName === 'opponent.done') {
        frames.push({ event: 'opponent.done', data: data as { turn_id: string; full_text: string } })
      } else if (eventName === 'coach.hint') {
        frames.push({ event: 'coach.hint', data: data as { safe: string; aggressive: string; humor: string } })
      } else if (eventName === 'meta') {
        frames.push({ event: 'meta', data: data as { turns_used: number; turns_left: number } })
      }
    } catch {
      console.warn('[SSE] Failed to parse data for event:', eventName)
    }
  }

  return frames
}

/**
 * Send a user turn and consume the SSE response.
 *
 * WeChat Mini Program supports chunked transfer via `enableChunked: true`
 * and the `onChunkMessage` callback on the request task.
 */
export function sendTurnSSE(
  sessionId: string,
  content: string,
  onFrame: (frame: SseEventFrame) => void,
  onError: (err: Error) => void,
): WxRequestTask {
  const requestTask = wx.request({
    url: `${API_BASE}/v1/sessions/${sessionId}/turns`,
    method: 'POST',
    data: { content },
    enableChunked: true,
    header: {
      'Content-Type': 'application/json',
      ...authHeader(),
    },
    success: () => {
      // Stream complete
    },
    fail: (err) => {
      onError(new Error(err.errMsg))
    },
  })

  // onChunkMessage receives ArrayBuffer chunks
  requestTask.onChunkMessage?.((res: { data: ArrayBuffer | string }) => {
    try {
      const chunk = typeof res.data === 'string'
        ? res.data
        : new TextDecoder().decode(res.data)
      const frames = parseSseChunk(chunk)
      for (const frame of frames) {
        onFrame(frame)
      }
    } catch (e) {
      console.warn('[SSE] chunk parse error:', e)
    }
  })

  return requestTask
}
