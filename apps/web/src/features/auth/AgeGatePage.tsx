/**
 * Age Gate page — PRD §1.5 / §3.0.5 C.
 *
 * New users (JWT without `age_set=true`) get 403 AGE_REQUIRED
 * from the backend. AuthProvider detects this and routes here
 * instead of the home page.
 *
 * Collects a 4-digit birth year, posts to
 * `POST /v1/users/me/birth-year`, then swaps the stale JWT
 * for the fresh one in the response (same shape as SmsVerifyResponse).
 */

import { useState } from 'react'

import { BlobBackground, GlassCard, MascotReaction } from '../../components'
import { apiClient, ApiError } from '../../api/v1'
import type { SmsVerifyResponse } from '../../api/v1'
import { useAuth } from './useAuth'

const MIN_YEAR = 1900
const MAX_YEAR = new Date().getFullYear()
const YEAR_PATTERN = /^\d{4}$/

export function AgeGatePage() {
  const { login } = useAuth()
  const [year, setYear] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const handleSubmit = async () => {
    const y = Number(year)
    if (!YEAR_PATTERN.test(year) || y < MIN_YEAR || y > MAX_YEAR) {
      setError(`请输入 ${MIN_YEAR}-${MAX_YEAR} 之间的年份`)
      return
    }
    setError(null)
    setPending(true)
    try {
      const res = await apiClient.post<SmsVerifyResponse>(
        '/users/me/birth-year',
        { birth_year: y },
      )
      // Replace the stale JWT with the fresh one that has age_set=true
      login(res.token, res.user)
    } catch (e) {
      setError(_humanizeError(e))
    } finally {
      setPending(false)
    }
  }

  const disabled = pending || !YEAR_PATTERN.test(year)

  return (
    <div className="relative min-h-screen flex flex-col items-center justify-center px-4 py-12 overflow-hidden">
      <BlobBackground />
      <div className="relative z-10 w-full max-w-sm space-y-6">
        <div className="text-center">
          <MascotReaction expression="caring" size="lg" showLabel />
          <h1 className="mt-4 text-2xl font-display text-ink-text">
            填写出生年份
          </h1>
          <p className="mt-2 text-sm text-ink-text-2 font-body">
            根据相关规定，使用前需确认你的年龄
          </p>
        </div>

        <GlassCard className="space-y-4">
          <input
            type="text"
            inputMode="numeric"
            maxLength={4}
            value={year}
            onChange={(e) => {
              setYear(e.target.value.replace(/\D/g, ''))
              setError(null)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !disabled) handleSubmit()
            }}
            placeholder="出生年份，如 2003"
            aria-label="出生年份"
            className="w-full rounded-radius-pill bg-ink-card px-5 py-3 text-base text-ink-text font-body border border-ink-line focus:border-vivid-purple focus:outline-none transition-colors text-center"
          />
          <button
            type="button"
            onClick={handleSubmit}
            disabled={disabled}
            className="w-full px-5 py-3 rounded-radius-pill gradient-vivid text-white text-base font-body font-medium hover:scale-[1.02] transition-transform disabled:opacity-50 disabled:hover:scale-100"
          >
            {pending ? '提交中…' : '确认'}
          </button>
          {error !== null && (
            <p className="text-sm text-vivid-orange text-center font-body" role="alert">
              {error}
            </p>
          )}
        </GlassCard>

        <p className="text-xs text-ink-text-3 text-center px-4">
          仅用于判断是否未成年，不会收集其他个人信息
        </p>
      </div>
    </div>
  )
}

function _humanizeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 400) return '年份格式不对，请输入4位数字'
    if (e.status === 404) return '账号不存在，请重新登录'
  }
  return '提交失败，请稍后再试'
}
