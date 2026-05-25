/**
 * ReviewUploadPage — cyberpunk redesign (PRD §3.3 US-C1 / design-spec §9.6).
 *
 * Data layer untouched: same POST `/v1/review/uploads` + 5000-char limit
 * + 422/400 error humanizer.
 *
 * Visual rebuild only:
 *   · NeuralParticles background.
 *   · "PASTE · CONVO" HudFrame around textarea + char count + submit CTA.
 *   · MagneticButton as primary action; K Mascot reflects pending state.
 */

import { useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowLeft, ClipboardPaste, ScanSearch } from 'lucide-react'

import { MascotReaction } from '../../components'
import {
  GlowText,
  HudFrame,
  MagneticButton,
  NeuralParticles,
} from '../../components/cyber'
import { apiClient, ApiError } from '../../api/v1'
import type { CreateReviewUploadResponse } from '../../api/v1'

const MAX_CHARS = 5000

export function ReviewUploadPage({
  onResult,
  onBack,
}: {
  onResult: (uploadId: string) => void
  onBack: () => void
}) {
  const [text, setText] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mascotExpression = error ? 'crashed' : pending ? 'thinking' : 'confident'
  const charCount = text.length
  const overLimit = charCount > MAX_CHARS
  const canSubmit = charCount > 0 && !overLimit && !pending

  const handleSubmit = async () => {
    if (!canSubmit) {
      return
    }
    setError(null)
    setPending(true)
    try {
      const res = await apiClient.post<CreateReviewUploadResponse>('/review/uploads', { text })
      onResult(res.upload_id)
    } catch (e) {
      setError(_humanizeError(e))
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-cyber-void text-white">
      <NeuralParticles count={1000} />
      <div className="pointer-events-none fixed inset-0 -z-10 tech-grid opacity-20" />
      <div className="pointer-events-none fixed inset-0 -z-10 scanline" />
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 -z-10"
        style={{
          background:
            'radial-gradient(ellipse at center, transparent 0%, rgba(5,5,5,0.4) 50%, rgba(5,5,5,0.92) 100%)',
        }}
      />

      <div className="relative z-10 mx-auto flex w-full max-w-3xl flex-1 flex-col px-6 py-8">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1 font-bebas text-xs tracking-[0.24em] text-white/60 transition-colors hover:text-cyber-cyan"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            BACK
          </button>
          <MascotReaction expression={mascotExpression} size="sm" />
        </div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mt-6 text-center"
        >
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-cyber-cyan/30 bg-cyber-cyan/5 px-3 py-1">
            <ScanSearch className="h-3 w-3 text-cyber-cyan" />
            <span className="font-bebas text-[11px] tracking-[0.28em] text-cyber-cyan">
              REVIEW · INGEST
            </span>
          </div>
          <h1 className="font-orbitron text-6xl font-black uppercase leading-none tracking-tight">
            <GlowText variant="gradient">DISSECT</GlowText>
          </h1>
          <p className="mt-3 font-display text-xl italic text-white/80">
            把对话粘下来，K 帮你逐句解剖。
          </p>
          <p className="mt-1 font-mono text-[11px] text-white/40">
            FORMAT · 「对方：…」 OR 「我：…」 PER LINE
          </p>
        </motion.div>

        <HudFrame
          label="PASTE · CONVO"
          tag={`${charCount}/${MAX_CHARS}`}
          className="cyber-glass-edge mt-8 rounded-3xl p-6"
        >
          <div className="flex items-center gap-2 text-white/50">
            <ClipboardPaste className="h-4 w-4" />
            <span className="font-bebas text-[11px] tracking-[0.24em]">CHAT · TRANSCRIPT</span>
          </div>
          <textarea
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder={
              '对方：你周末有空吗？\n我：有安排了，下次约。\n对方：什么事比工作还重要？\n我：emmm…好吧我加班'
            }
            rows={12}
            maxLength={MAX_CHARS + 100}
            className="font-grotesk mt-3 w-full resize-none rounded-2xl border border-cyber-hairline bg-cyber-deep/60 px-4 py-3 text-sm text-white placeholder:text-white/30 focus:border-cyber-cyan focus:bg-cyber-deep/80 focus:outline-none focus:ring-2 focus:ring-cyber-cyan/30"
          />
          <div className="mt-4 flex items-center justify-between">
            <span
              className={`font-mono text-xs ${
                overLimit ? 'text-cyber-magenta' : 'text-white/40'
              }`}
            >
              {overLimit ? '⚠ ' : ''}
              {charCount} / {MAX_CHARS}
            </span>
            <MagneticButton type="button" onClick={handleSubmit} disabled={!canSubmit}>
              <ScanSearch className="h-4 w-4" />
              {pending ? '分析中…' : '开始复盘'}
            </MagneticButton>
          </div>
        </HudFrame>

        {error && (
          <p
            role="alert"
            className="mt-4 text-center font-mono text-xs text-cyber-magenta animate-hud-flicker"
          >
            ⚠ {error}
          </p>
        )}
      </div>
    </div>
  )
}

function _humanizeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 400) {
      return '内容包含敏感信息，请修改后重试'
    }
    if (e.status === 422) {
      return '文本格式不对，请检查后重试'
    }
  }
  return '提交失败，请稍后再试'
}
