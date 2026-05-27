/**
 * LoginPage — cyberpunk redesign (PRD §F1 / §7.2).
 *
 * Same two-step SMS flow + data layer as before:
 *   step 1 (phone): 11-digit input → POST /v1/auth/sms/send
 *   step 2 (code):  6-digit input  → POST /v1/auth/sms/verify → useAuth.login
 *
 * Visual chrome only — error messages, regexes, 60s cooldown,
 * Retry-After handling, and the inline error humanizers are preserved
 * verbatim from the original. The page now matches the HomePage AI-OS
 * shell: NeuralParticles background, HUD frame around the form card,
 * MagneticButton primary, Orbitron massive eyebrow, K Mascot in a corner.
 */

import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { ChevronLeft, Send, Smartphone, Sparkles } from 'lucide-react'

import { MascotReaction } from '../../components'
import {
  GlowText,
  HudFrame,
  MagneticButton,
  NeuralParticles,
} from '../../components/cyber'
import { apiClient, ApiError } from '../../api/v1'
import type { SmsSendResponse, SmsVerifyResponse } from '../../api/v1'
import { useAuth } from './useAuth'

const PHONE_PATTERN = /^1[3-9]\d{9}$/
const CODE_PATTERN = /^\d{6}$/

type Step = 'phone' | 'code'

export function LoginPage() {
  const { login } = useAuth()
  const [step, setStep] = useState<Step>('phone')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    if (cooldown <= 0) {
      return
    }
    const t = setInterval(() => setCooldown(c => Math.max(0, c - 1)), 1000)
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
      const res = await apiClient.post<SmsSendResponse>('/auth/sms/send', { phone })
      setStep('code')
      setCode('')
      setCooldown(res.ttl)
    } catch (e) {
      setError(_humanizeSendError(e))
      if (e instanceof ApiError && e.status === 429) {
        const retryAfter = e.headers.get('Retry-After')
        if (retryAfter) {
          const secs = Number(retryAfter)
          if (secs > 0) {
            setCooldown(secs)
          }
        }
      }
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
      const res = await apiClient.post<SmsVerifyResponse>('/auth/sms/verify', {
        phone,
        code,
      })
      login(res.token, res.user)
    } catch (e) {
      setError(_humanizeVerifyError(e))
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-cyber-void px-4 py-12 text-white">
      <NeuralParticles count={1200} />
      <div className="pointer-events-none fixed inset-0 -z-10 tech-grid opacity-25" />
      <div className="pointer-events-none fixed inset-0 -z-10 scanline" />
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 -z-10"
        style={{
          background:
            'radial-gradient(ellipse at center, transparent 0%, rgba(5,5,5,0.5) 50%, rgba(5,5,5,0.92) 100%)',
        }}
      />

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
        className="relative z-10 w-full max-w-md"
      >
        {/* Eyebrow + giant headline */}
        <div className="mb-6 text-center">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-cyber-cyan/30 bg-cyber-cyan/5 px-3 py-1">
            <Sparkles className="h-3 w-3 text-cyber-cyan" />
            <span className="font-bebas text-[11px] tracking-[0.28em] text-cyber-cyan">
              ACCESS · TERMINAL
            </span>
          </div>
          <h1 className="font-orbitron text-6xl font-black uppercase leading-none tracking-tight">
            <GlowText variant="gradient">ENTER</GlowText>
          </h1>
          <p className="mt-3 font-display text-xl italic text-white/80">
            {step === 'phone' ? '把手机号丢进来' : '验证码已发出'}
          </p>
        </div>

        {/* K mascot floats above the HUD card */}
        <div className="-mb-12 flex justify-center">
          <motion.div
            initial={{ scale: 0.3, rotate: -10, opacity: 0 }}
            animate={{ scale: 1, rotate: 0, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 280, damping: 18, delay: 0.3 }}
            className="relative z-10"
          >
            <MascotReaction expression="confident" size="md" />
          </motion.div>
        </div>

        <HudFrame
          label={step === 'phone' ? 'AUTH · STEP 01' : 'AUTH · STEP 02'}
          tag={step === 'phone' ? 'PHONE' : 'OTP-6'}
          className="cyber-glass-edge rounded-3xl px-6 pt-16 pb-6"
        >
          {step === 'phone' ? (
            <PhoneStep
              phone={phone}
              pending={pending}
              onChange={setPhone}
              onSubmit={handleSend}
            />
          ) : (
            <CodeStep
              phone={phone}
              code={code}
              pending={pending}
              cooldown={cooldown}
              onCodeChange={setCode}
              onSubmit={handleVerify}
              onResend={handleSend}
              onBack={() => {
                setStep('phone')
                setCode('')
                setError(null)
              }}
            />
          )}
          {error !== null && (
            <p
              className="mt-4 text-center font-mono text-xs text-cyber-magenta animate-hud-flicker"
              role="alert"
            >
              ⚠ {error}
            </p>
          )}
        </HudFrame>

        <p className="mt-6 text-center font-mono text-[11px] text-white/40">
          登录即同意《用户协议》和《隐私政策》
        </p>
        {/* PR-D1 demo bypass hint — flag-driven so it disappears in prod. */}
        {import.meta.env.VITE_DEMO_MODE === 'true' && (
          <div className="mt-3 rounded-lg border border-cyber-cyan/20 bg-cyber-cyan/5 px-4 py-2 text-center font-mono text-[10px] text-cyber-cyan/70">
            <span className="text-cyber-cyan">DEMO · </span>
            手机号填自己的（不会真发短信），验证码任意 6 位即可（如 000000）
          </div>
        )}
      </motion.div>
    </div>
  )
}

interface PhoneStepProps {
  phone: string
  pending: boolean
  onChange: (v: string) => void
  onSubmit: () => void
}

function PhoneStep({ phone, pending, onChange, onSubmit }: PhoneStepProps) {
  const disabled = pending || !PHONE_PATTERN.test(phone)
  return (
    <div className="space-y-4">
      <CyberInput
        type="tel"
        inputMode="numeric"
        autoComplete="tel"
        maxLength={11}
        value={phone}
        onChange={e => onChange(e.target.value.replace(/\D/g, ''))}
        onKeyDown={e => {
          if (e.key === 'Enter' && !disabled) {
            onSubmit()
          }
        }}
        placeholder="11 位手机号"
        ariaLabel="手机号"
        icon={<Smartphone className="h-4 w-4" />}
      />
      <div className="flex justify-center pt-1">
        <MagneticButton type="button" onClick={onSubmit} disabled={disabled}>
          {pending ? (
            <>
              <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-cyber-cyan" />
              发送中…
            </>
          ) : (
            <>
              <Send className="h-4 w-4" />
              发送验证码
            </>
          )}
        </MagneticButton>
      </div>
    </div>
  )
}

interface CodeStepProps {
  phone: string
  code: string
  pending: boolean
  cooldown: number
  onCodeChange: (v: string) => void
  onSubmit: () => void
  onResend: () => void
  onBack: () => void
}

function CodeStep({
  phone,
  code,
  pending,
  cooldown,
  onCodeChange,
  onSubmit,
  onResend,
  onBack,
}: CodeStepProps) {
  const disabled = pending || !CODE_PATTERN.test(code)
  return (
    <div className="space-y-4">
      <p className="text-center font-mono text-[11px] text-white/50">
        SMS → <span className="text-cyber-cyan">{_maskPhone(phone)}</span>
      </p>
      <CyberInput
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        value={code}
        onChange={e => onCodeChange(e.target.value.replace(/\D/g, ''))}
        onKeyDown={e => {
          if (e.key === 'Enter' && !disabled) {
            onSubmit()
          }
        }}
        placeholder="6 位验证码"
        ariaLabel="验证码"
        centered
        mono
      />
      <div className="flex justify-center pt-1">
        <MagneticButton
          type="button"
          onClick={onSubmit}
          disabled={disabled}
          variant="magenta"
        >
          {pending ? '解锁中…' : '解锁 ENTER →'}
        </MagneticButton>
      </div>
      <div className="flex items-center justify-between pt-1 font-mono text-[11px]">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1 text-white/50 transition-colors hover:text-white/80"
        >
          <ChevronLeft className="h-3 w-3" />
          换手机号
        </button>
        <button
          type="button"
          onClick={onResend}
          disabled={pending || cooldown > 0}
          className="text-cyber-cyan transition-colors hover:text-cyber-magenta disabled:text-white/30"
        >
          {cooldown > 0 ? `RESEND · ${cooldown}s` : 'RESEND'}
        </button>
      </div>
    </div>
  )
}

/* ── CyberInput ────────────────────────────────────────────────────
 * Local-only input wrapper. Cyan focus ring, mono optional, optional
 * leading icon. Inlined here rather than exported because both pages
 * in this feature module are the only consumers — pulling it out into
 * cyber/* would mean exposing a "form-input" abstraction that we don't
 * have a clear contract for yet.
 * ─────────────────────────────────────────────────────────────── */
interface CyberInputProps {
  type?: string
  inputMode?: 'numeric' | 'text' | 'tel'
  autoComplete?: string
  maxLength?: number
  value: string
  placeholder?: string
  ariaLabel: string
  icon?: React.ReactNode
  centered?: boolean
  mono?: boolean
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void
}

function CyberInput({
  type = 'text',
  inputMode,
  autoComplete,
  maxLength,
  value,
  placeholder,
  ariaLabel,
  icon,
  centered = false,
  mono = false,
  onChange,
  onKeyDown,
}: CyberInputProps) {
  return (
    <div className="group relative">
      {icon && (
        <span className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-cyber-cyan/70">
          {icon}
        </span>
      )}
      <input
        type={type}
        inputMode={inputMode}
        autoComplete={autoComplete}
        maxLength={maxLength}
        value={value}
        onChange={onChange}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        aria-label={ariaLabel}
        className={`w-full rounded-2xl border border-cyber-hairline bg-cyber-deep/60 py-3 ${
          icon ? 'pl-11 pr-4' : 'px-4'
        } text-base text-white transition-all placeholder:text-white/30 focus:border-cyber-cyan focus:bg-cyber-deep/80 focus:outline-none focus:ring-2 focus:ring-cyber-cyan/30 ${
          centered ? 'text-center tracking-[0.5em]' : ''
        } ${mono ? 'font-orbitron text-xl' : 'font-grotesk'}`}
      />
    </div>
  )
}

function _maskPhone(phone: string): string {
  if (phone.length < 7) {
    return phone
  }
  return `${phone.slice(0, 3)}****${phone.slice(-4)}`
}

function _humanizeSendError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 400) {
      return '手机号格式不对'
    }
    if (e.status === 429) {
      const code = (e.body as { code?: string })?.code
      if (code === 'SMS_SEND_COOLDOWN') {
        const retryAfter = e.headers.get('Retry-After')
        const secs = retryAfter ? Number(retryAfter) : 0
        if (secs > 0) {
          return `发送太频繁，${secs} 秒后再试`
        }
        return '发送太频繁，请稍后再试'
      }
      return '请求太频繁，稍后再试'
    }
  }
  return '发送失败，请稍后再试'
}

function _humanizeVerifyError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 400) {
      return '验证码错了，再试一次'
    }
    if (e.status === 429) {
      const code = (e.body as { code?: string })?.code
      if (code === 'SMS_VERIFY_LOCKED') {
        return '验证次数过多，请稍后再试'
      }
      return '请求太频繁，稍后再试'
    }
  }
  return '登录失败，请稍后再试'
}
