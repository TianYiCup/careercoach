/**
 * AgeGatePage — cyberpunk redesign (PRD §1.5 / §3.0.5 C).
 *
 * Same 4-digit birth-year input + `POST /v1/users/me/birth-year` flow.
 * Only the chrome changes — NeuralParticles background, HUD frame card,
 * K Mascot floating, MagneticButton, cyan focus ring on the input.
 *
 * K's expression switches to `caring` here — matches design-spec §3.3
 * 🥹 "心疼" tone for the under-18 protection moment.
 */

import { useState } from 'react'
import { motion } from 'framer-motion'
import { CalendarDays, ShieldCheck } from 'lucide-react'

import { MascotReaction } from '../../components'
import {
  GlowText,
  HudFrame,
  MagneticButton,
  NeuralParticles,
} from '../../components/cyber'
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
      const res = await apiClient.post<SmsVerifyResponse>('/users/me/birth-year', {
        birth_year: y,
      })
      login(res.token, res.user)
    } catch (e) {
      setError(_humanizeError(e))
    } finally {
      setPending(false)
    }
  }

  const disabled = pending || !YEAR_PATTERN.test(year)

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
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-cyber-magenta/30 bg-cyber-magenta/5 px-3 py-1">
            <ShieldCheck className="h-3 w-3 text-cyber-magenta" />
            <span className="font-bebas text-[11px] tracking-[0.28em] text-cyber-magenta">
              AGE · GATE · § 1.5
            </span>
          </div>
          <h1 className="font-orbitron text-6xl font-black uppercase leading-none tracking-tight">
            <GlowText variant="stroke" color="#FF2DAA">
              VERIFY
            </GlowText>
          </h1>
          <p className="mt-3 font-display text-xl italic text-white/80">
            填一下出生年份，给你匹配合适的练习场。
          </p>
        </div>

        {/* K mascot, caring expression for under-18 protection moment */}
        <div className="-mb-12 flex justify-center">
          <motion.div
            initial={{ scale: 0.3, rotate: -10, opacity: 0 }}
            animate={{ scale: 1, rotate: 0, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 280, damping: 18, delay: 0.3 }}
            className="relative z-10"
          >
            <MascotReaction expression="caring" size="md" />
          </motion.div>
        </div>

        <HudFrame
          label="GATE · BIRTH-YEAR"
          tag={`Y${MIN_YEAR}-${MAX_YEAR}`}
          className="cyber-glass-edge rounded-3xl px-6 pt-16 pb-6"
        >
          <div className="space-y-4">
            <div className="group relative">
              <span className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-cyber-magenta/70">
                <CalendarDays className="h-4 w-4" />
              </span>
              <input
                type="text"
                inputMode="numeric"
                maxLength={4}
                value={year}
                onChange={e => {
                  setYear(e.target.value.replace(/\D/g, ''))
                  setError(null)
                }}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !disabled) {
                    handleSubmit()
                  }
                }}
                placeholder="出生年份，如 2003"
                aria-label="出生年份"
                className="font-orbitron w-full rounded-2xl border border-cyber-hairline bg-cyber-deep/60 py-3 pr-4 pl-11 text-center text-xl tracking-[0.4em] text-white transition-all placeholder:text-white/30 placeholder:tracking-normal focus:border-cyber-magenta focus:bg-cyber-deep/80 focus:outline-none focus:ring-2 focus:ring-cyber-magenta/30"
              />
            </div>

            <div className="flex justify-center pt-1">
              <MagneticButton
                type="button"
                onClick={handleSubmit}
                disabled={disabled}
                variant="magenta"
              >
                {pending ? '验证中…' : 'CONFIRM →'}
              </MagneticButton>
            </div>

            {error !== null && (
              <p
                className="text-center font-mono text-xs text-cyber-magenta animate-hud-flicker"
                role="alert"
              >
                ⚠ {error}
              </p>
            )}
          </div>
        </HudFrame>

        <p className="mt-6 px-4 text-center font-mono text-[11px] text-white/40">
          仅用于判断是否未成年 · 不收集其他个人信息 · 数据加密存储
        </p>
      </motion.div>
    </div>
  )
}

function _humanizeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 400) {
      return '年份格式不对，请输入 4 位数字'
    }
    if (e.status === 404) {
      return '账号不存在，请重新登录'
    }
  }
  return '提交失败，请稍后再试'
}
