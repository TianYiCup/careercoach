/**
 * 复盘上传页 — PRD §3.3 US-C1 / design-spec §9.6
 *
 * v0.1 仅支持文字粘贴（≤5000字），图片/音频留给 v2。
 * 流程：用户粘贴对话文本 → POST /v1/review/uploads → 跳转结果页。
 */

import { useState } from 'react'

import { BlobBackground, GlassCard, MascotReaction } from '../../components'
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

  // Dynamic mascot: thinking while pending, crashed on error, confident idle
  const mascotExpression = error ? 'crashed' : pending ? 'thinking' : 'confident'

  const charCount = text.length
  const overLimit = charCount > MAX_CHARS
  const canSubmit = charCount > 0 && !overLimit && !pending

  const handleSubmit = async () => {
    if (!canSubmit) return
    setError(null)
    setPending(true)
    try {
      const res = await apiClient.post<CreateReviewUploadResponse>(
        '/review/uploads',
        { text },
      )
      onResult(res.upload_id)
    } catch (e) {
      setError(_humanizeError(e))
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="relative min-h-screen flex flex-col items-center px-4 py-12 overflow-hidden">
      <BlobBackground />
      <div className="relative z-10 w-full max-w-2xl space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={onBack}
            className="text-ink-text-2 text-sm hover:text-ink-text transition-colors"
          >
            ← 返回
          </button>
          <div className="flex items-center gap-2">
            <MascotReaction expression={mascotExpression} size="sm" />
            <span className="text-lg font-display text-ink-text">复盘师</span>
          </div>
          <div className="w-14" /> {/* spacer */}
        </div>

        {/* Instruction */}
        <div className="text-center">
          <h1 className="text-2xl font-display text-ink-text">
            粘贴对话内容
          </h1>
          <p className="mt-2 text-sm text-ink-text-2 font-body">
            把你想复盘的对话粘贴下来，教练 K 帮你逐句分析
          </p>
          <p className="mt-1 text-xs text-ink-text-3 font-body">
            格式：以「对方：」或「我：」开头分行，每行一句话
          </p>
        </div>

        {/* Text area */}
        <GlassCard className="space-y-3">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={'对方：你周末有空吗？\n我：有安排了，下次约。\n对方：什么事比工作还重要？\n我：emmm...好吧我加班'}
            rows={10}
            maxLength={MAX_CHARS + 100}
            className="w-full rounded-radius-md bg-ink-card px-4 py-3 text-sm text-ink-text font-body border border-ink-line focus:border-vivid-purple focus:outline-none transition-colors resize-none"
          />
          <div className="flex items-center justify-between">
            <span className={`text-xs font-body ${overLimit ? 'text-vivid-orange' : 'text-ink-text-3'}`}>
              {charCount} / {MAX_CHARS}
            </span>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="px-6 py-2 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform disabled:opacity-50 disabled:hover:scale-100"
            >
              {pending ? '分析中…' : '开始复盘'}
            </button>
          </div>
        </GlassCard>

        {error && (
          <p className="text-sm text-vivid-orange text-center font-body" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  )
}

function _humanizeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 400) return '内容包含敏感信息，请修改后重试'
    if (e.status === 422) return '文本格式不对，请检查后重试'
  }
  return '提交失败，请稍后再试'
}
