/**
 * SMS 登录页 — 小程序版
 *
 * 参考 web LoginPage.tsx 的两步流程：
 *   step 1 (phone): 11 位手机号 → POST /v1/auth/sms/send
 *   step 2 (code):  6 位验证码 → POST /v1/auth/sms/verify
 *
 * 登录成功后存 token + user 到 wx storage，跳转首页。
 */

import { useEffect, useState } from 'react'
import { View, Text, Input } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { setAuthToken } from '../../utils/auth-token'
import { setAuthUser } from '../../utils/auth-user'
import { authedRequest } from '../../api/client'
import type { SmsSendResponse, SmsVerifyResponse } from '../../api/types'
import './index.scss'

const PHONE_PATTERN = /^1[3-9]\d{9}$/
const CODE_PATTERN = /^\d{6}$/

type Step = 'phone' | 'code'

export default function LoginPage() {
  const [step, setStep] = useState<Step>('phone')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [cooldown, setCooldown] = useState(0)

  // Cooldown ticker
  useEffect(() => {
    if (cooldown <= 0) return
    const t = setInterval(() => setCooldown((c) => Math.max(0, c - 1)), 1000)
    return () => clearInterval(t)
  }, [cooldown])

  const handleSend = async () => {
    if (!PHONE_PATTERN.test(phone)) {
      setError('请输入正确的手机号')
      return
    }
    setError(null)
    setPending(true)
    try {
      const res = await authedRequest<SmsSendResponse>(
        '/auth/sms/send', 'POST', { phone },
      )
      setStep('code')
      setCode('')
      setCooldown(res.ttl)
    } catch (e) {
      setError(_humanizeSendError(e))
    } finally {
      setPending(false)
    }
  }

  const handleVerify = async () => {
    if (!CODE_PATTERN.test(code)) {
      setError('验证码是 6 位数字')
      return
    }
    setError(null)
    setPending(true)
    try {
      const res = await authedRequest<SmsVerifyResponse>(
        '/auth/sms/verify', 'POST', { phone, code },
      )
      setAuthToken(res.token)
      setAuthUser(res.user)
      Taro.reLaunch({ url: '/pages/index/index' })
    } catch (e) {
      setError(_humanizeVerifyError(e))
    } finally {
      setPending(false)
    }
  }

  return (
    <View className="login">
      <View className="login-header">
        <Text className="login-title">CareerCoach AI</Text>
        <Text className="login-subtitle">
          {step === 'phone' ? '输入手机号开始对练' : '验证码已发送'}
        </Text>
      </View>

      <View className="login-card">
        {step === 'phone' ? (
          <View className="login-form">
            <Input
              className="login-input"
              type="number"
              maxLength={11}
              value={phone}
              onInput={(e) => {
                setPhone(e.detail.value.replace(/\D/g, ''))
                setError(null)
              }}
              placeholder="11 位手机号"
            />
            <View
              className={`login-btn ${(!PHONE_PATTERN.test(phone) || pending) ? 'login-btn--disabled' : ''}`}
              onClick={handleSend}
            >
              <Text className="login-btn-text">
                {pending ? '发送中…' : '发送验证码'}
              </Text>
            </View>
          </View>
        ) : (
          <View className="login-form">
            <Text className="login-hint">
              验证码已发送至 {_maskPhone(phone)}
            </Text>
            <Input
              className="login-input login-input--code"
              type="number"
              maxLength={6}
              value={code}
              onInput={(e) => {
                setCode(e.detail.value.replace(/\D/g, ''))
                setError(null)
              }}
              placeholder="6 位验证码"
            />
            <View
              className={`login-btn ${(!CODE_PATTERN.test(code) || pending) ? 'login-btn--disabled' : ''}`}
              onClick={handleVerify}
            >
              <Text className="login-btn-text">
                {pending ? '登录中…' : '登录'}
              </Text>
            </View>
            <View className="login-actions">
              <Text
                className="login-link"
                onClick={() => { setStep('phone'); setCode(''); setError(null) }}
              >
                ← 换手机号
              </Text>
              <Text
                className={`login-link ${pending || cooldown > 0 ? 'login-link--disabled' : ''}`}
                onClick={() => { if (cooldown <= 0 && !pending) handleSend() }}
              >
                {cooldown > 0 ? `重发 (${cooldown}s)` : '重新发送'}
              </Text>
            </View>
          </View>
        )}

        {error !== null && (
          <Text className="login-error">{error}</Text>
        )}
      </View>

      <Text className="login-agreement">
        登录即同意《用户协议》和《隐私政策》
      </Text>
    </View>
  )
}

function _maskPhone(phone: string): string {
  if (phone.length < 7) return phone
  return `${phone.slice(0, 3)}****${phone.slice(-4)}`
}

function _humanizeSendError(e: unknown): string {
  if (e instanceof Error && 'status' in e) {
    const apiErr = e as { status: number; body?: { code?: string } }
    if (apiErr.status === 400) return '手机号格式不对'
    if (apiErr.status === 429) {
      const code = apiErr.body?.code
      if (code === 'SMS_SEND_COOLDOWN') return '发送太频繁，请稍后再试'
      return '请求太频繁，稍后再试'
    }
  }
  return '发送失败，请稍后再试'
}

function _humanizeVerifyError(e: unknown): string {
  if (e instanceof Error && 'status' in e) {
    const apiErr = e as { status: number; body?: { code?: string } }
    if (apiErr.status === 400) return '验证码错了，再试一次'
    if (apiErr.status === 429) {
      const code = apiErr.body?.code
      if (code === 'SMS_VERIFY_LOCKED') return '验证次数过多，请稍后再试'
      return '请求太频繁，稍后再试'
    }
  }
  return '登录失败，请稍后再试'
}
