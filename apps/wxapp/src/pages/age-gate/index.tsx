/**
 * 年龄确认页 — 小程序版
 *
 * PRD §1.5 / §3.0.5 C：新用户（JWT 无 age_set）需填写出生年份。
 * 后端返回 403 AGE_REQUIRED 后跳此页。
 *
 * 登录成功后用新 JWT 替换旧 token，跳首页。
 */

import { useState } from 'react'
import { View, Text, Input } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { getAuthToken, setAuthToken } from '../../utils/auth-token'
import { setAuthUser, clearAuthUser } from '../../utils/auth-user'
import { authedRequest } from '../../api/client'
import type { SmsVerifyResponse } from '../../api/types'
import './index.scss'

const MIN_YEAR = 1900
const MAX_YEAR = new Date().getFullYear()
const YEAR_PATTERN = /^\d{4}$/

export default function AgeGatePage() {
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
      const res = await authedRequest<SmsVerifyResponse>(
        '/users/me/birth-year', 'POST', { birth_year: y },
      )
      // Replace JWT with fresh one (has age_set=true)
      setAuthToken(res.token)
      setAuthUser(res.user)
      Taro.reLaunch({ url: '/pages/index/index' })
    } catch (e) {
      setError(_humanizeError(e))
    } finally {
      setPending(false)
    }
  }

  const disabled = pending || !YEAR_PATTERN.test(year)

  return (
    <View className="age-gate">
      <View className="age-gate-header">
        <Text className="age-gate-emoji">💜</Text>
        <Text className="age-gate-title">填写出生年份</Text>
        <Text className="age-gate-desc">
          根据相关规定，使用前需确认你的年龄
        </Text>
      </View>

      <View className="age-gate-card">
        <Input
          className="age-gate-input"
          type="number"
          maxLength={4}
          value={year}
          onInput={(e) => {
            setYear(e.detail.value.replace(/\D/g, ''))
            setError(null)
          }}
          placeholder="出生年份，如 2003"
        />
        <View
          className={`age-gate-btn ${disabled ? 'age-gate-btn--disabled' : ''}`}
          onClick={handleSubmit}
        >
          <Text className="age-gate-btn-text">
            {pending ? '提交中…' : '确认'}
          </Text>
        </View>
        {error !== null && (
          <Text className="age-gate-error">{error}</Text>
        )}
      </View>

      <Text className="age-gate-privacy">
        仅用于判断是否未成年，不会收集其他个人信息
      </Text>
    </View>
  )
}

function _humanizeError(e: unknown): string {
  if (e instanceof Error && 'status' in e) {
    const apiErr = e as { status: number }
    if (apiErr.status === 400) return '年份格式不对，请输入4位数字'
    if (apiErr.status === 404) return '账号不存在，请重新登录'
  }
  return '提交失败，请稍后再试'
}
