/**
 * API 客户端封装 — wx.request 的 Promise 化
 *
 * 注意：所有后端域名必须在微信小程序后台「服务器域名」中添加白名单
 * （CLAUDE.md #14）。本地开发时在微信开发者工具 → 详情 → 本地设置 →
 * 勾选「不校验合法域名」。
 *
 * 实际域名 + 环境切换在 ./config.ts。
 */

import { API_BASE } from './config'

interface ApiResponse<T> {
  data: T
  statusCode: number
  header: Record<string, string>
}

/** 通用 wx.request 封装 */
function request<T>(
  path: string,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'GET',
  data?: unknown,
): Promise<ApiResponse<T>> {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}${path}`,
      method,
      data,
      header: {
        'Content-Type': 'application/json',
      },
      success: (res) => {
        resolve({
          data: res.data as T,
          statusCode: res.statusCode,
          header: res.header as Record<string, string>,
        })
      },
      fail: (err) => {
        console.error(`[API] ${method} ${path} failed:`, err)
        reject(err)
      },
    })
  })
}

/** Health check — D5-B 验证端点 */
export function getHealth() {
  return request<{ status: string }>('/health')
}

/** 获取场景列表 */
export function getScenarios(category?: string) {
  const query = category ? `?category=${category}` : ''
  return request<{ items: unknown[] }>(`/v1/scenarios${query}`)
}

/** 创建会话 */
export function createSession(body: { scenario_id: string }) {
  return request<{ session_id: string }>('/v1/sessions', 'POST', body)
}

export { API_BASE }

