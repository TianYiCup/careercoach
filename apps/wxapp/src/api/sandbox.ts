/**
 * 沙盘会话 API — 小程序端
 *
 * SSE 通过 wx.request + enableChunked 实现。
 * B-6: 401 → clear + reLaunch 登录页
 * B-1: 403 AGE_REQUIRED → 跳年龄确认页
 * B-2: 403 MINOR_QUIET_HOURS → showModal → navigateBack
 * B-7: 删除所有 console.* 调用
 */

import Taro from '@tarojs/taro'
import { API_BASE } from './config'
import { getAuthToken, clearAuthToken } from '../utils/auth-token'
import { clearAuthUser } from '../utils/auth-user'

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

// --- Auth header helper ---

function authHeader(): Record<string, string> {
  const token = getAuthToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// --- 401/403 global handler ---

function handleAuthError(statusCode: number, body: unknown): void {
  if (statusCode === 401) {
    clearAuthToken()
    clearAuthUser()
    Taro.reLaunch({ url: '/pages/login/index' })
    return
  }
  if (statusCode === 403) {
    const code = (body as { code?: string })?.code
    if (code === 'AGE_REQUIRED') {
      Taro.reLaunch({ url: '/pages/age-gate/index' })
      return
    }
    if (code === 'MINOR_QUIET_HOURS') {
      Taro.showModal({
        title: '静默时段',
        content: '为保护未成年人，22:00-08:00 期间无法使用对练功能',
        showCancel: false,
        confirmText: '我知道了',
        success: () => Taro.navigateBack(),
      })
      return
    }
  }
}

// --- Session API ---

function request<T>(
  path: string,
  method: 'GET' | 'POST' = 'GET',
  data?: unknown,
): Promise<T> {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}/v1${path}`,
      method,
      data,
      header: {
        'Content-Type': 'application/json',
        ...authHeader(),
      },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data as T)
          return
        }
        handleAuthError(res.statusCode, res.data)
        reject(new Error(`API ${method} ${path} → ${res.statusCode}`))
      },
      fail: (err) => {
        reject(new Error(err.errMsg))
      },
    })
  })
}

/** Create a new sandbox session */
export function createSession(body: {
  mode: string
  scenario_id: string
  persona_id: string
  user_goal: string
}) {
  return request<CreateSessionResponse>('/sessions', 'POST', body)
}

/** End a session and get score */
export function endSession(sessionId: string) {
  return request<EndSessionResponse>(
    `/sessions/${sessionId}/end`,
    'POST',
  )
}

// --- SSE via wx.request + enableChunked ---

/**
 * Parse SSE text chunk into event frames.
 * SSE format: "event: <name>\ndata: <json>\n\n"
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
      // Skip unparseable chunks silently
    }
  }

  return frames
}

/**
 * Send a user turn and consume the SSE response.
 * Includes 401/403 handling on the initial HTTP response.
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
    success: (res) => {
      // If the HTTP status itself is an error, handle auth + reject
      if (res.statusCode >= 300) {
        handleAuthError(res.statusCode, res.data)
        onError(new Error(`SSE turn → ${res.statusCode}`))
      }
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
    } catch {
      // Skip unparseable chunks silently
    }
  })

  return requestTask
}
