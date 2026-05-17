/**
 * Auth token storage — wx.*Sync version of web's auth-token.ts
 *
 * Storage key: `careercoach.auth_token` (same as web for cross-tool debug).
 * Uses wx.getStorageSync / wx.setStorageSync (synchronous, small payloads).
 */

const TOKEN_KEY = 'careercoach.auth_token'

export function getAuthToken(): string | null {
  try {
    return (wx.getStorageSync(TOKEN_KEY) as string) || null
  } catch {
    return null
  }
}

export function setAuthToken(token: string): void {
  wx.setStorageSync(TOKEN_KEY, token)
}

export function clearAuthToken(): void {
  try {
    wx.removeStorageSync(TOKEN_KEY)
  } catch {
    // no-op — key may not exist
  }
}
