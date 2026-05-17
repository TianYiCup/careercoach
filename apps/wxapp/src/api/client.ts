/**
 * API 客户端封装 — wx.request 的 Promise 化
 *
 * B-6: 401 → clear storage → reLaunch 登录页
 * B-1: 403 AGE_REQUIRED → reLaunch 年龄确认页
 * B-2: 403 MINOR_QUIET_HOURS → showModal 提示
 *
 * 注意：所有后端域名必须在微信小程序后台「服务器域名」中添加白名单
 * （CLAUDE.md #14）。
 */

import Taro from '@tarojs/taro'
import { API_BASE } from './config'
import { getAuthToken, clearAuthToken } from '../utils/auth-token'
import { clearAuthUser } from '../utils/auth-user'

// --- Error class ---

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, body: unknown) {
    const message =
      typeof body === 'object' && body !== null && 'message' in body
        ? String((body as { message: unknown }).message)
        : `API Error ${status}`
    super(message)
    this.status = status
    this.body = body
  }
}

// --- Auth-gated request ---

/**
 * Authenticated request — injects Bearer token, handles 401/403 globally.
 *
 * 401 → clear token + user → reLaunch /pages/login/index
 * 403 AGE_REQUIRED → reLaunch /pages/age-gate/index
 * 403 MINOR_QUIET_HOURS → showModal → navigateBack
 */
export function authedRequest<T>(
  path: string,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'GET',
  data?: unknown,
): Promise<T> {
  const token = getAuthToken()
  const header: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  if (token) {
    header.Authorization = `Bearer ${token}`
  }

  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}/v1${path}`,
      method,
      data,
      header,
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data as T)
          return
        }

        // 401 — token invalid/expired
        if (res.statusCode === 401) {
          clearAuthToken()
          clearAuthUser()
          Taro.reLaunch({ url: '/pages/login/index' })
          reject(new ApiError(res.statusCode, res.data))
          return
        }

        // 403 — check error code
        if (res.statusCode === 403) {
          const code = (res.data as { code?: string })?.code
          if (code === 'AGE_REQUIRED') {
            Taro.reLaunch({ url: '/pages/age-gate/index' })
            reject(new ApiError(res.statusCode, res.data))
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
            reject(new ApiError(res.statusCode, res.data))
            return
          }
        }

        reject(new ApiError(res.statusCode, res.data))
      },
      fail: (err) => {
        reject(new Error(err.errMsg))
      },
    })
  })
}

// --- Health check (no auth needed) ---

export function getHealth() {
  return new Promise<{ status: string }>((resolve, reject) => {
    wx.request({
      url: `${API_BASE}/health`,
      success: (res) => resolve(res.data as { status: string }),
      fail: (err) => reject(new Error(err.errMsg)),
    })
  })
}

export { API_BASE }
