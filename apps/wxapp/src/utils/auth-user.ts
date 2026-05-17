/**
 * Auth user storage — wx.*Sync version of web's auth-user.ts
 *
 * Storage key: `careercoach.auth_user` (same key as web).
 * Stores JSON-serialized UserPublic object.
 */

import type { UserPublic } from '../api/types'

const USER_KEY = 'careercoach.auth_user'

export function getAuthUser(): UserPublic | null {
  try {
    const raw = wx.getStorageSync(USER_KEY) as string
    if (!raw) return null
    return JSON.parse(raw) as UserPublic
  } catch {
    return null
  }
}

export function setAuthUser(user: UserPublic): void {
  wx.setStorageSync(USER_KEY, JSON.stringify(user))
}

export function clearAuthUser(): void {
  try {
    wx.removeStorageSync(USER_KEY)
  } catch {
    // no-op
  }
}
