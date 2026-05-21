/**
 * 复盘上传页 — 小程序版
 * PRD §3.3 US-C1 / design-spec §9.6
 *
 * 流程：粘贴对话文本 → POST /v1/review/uploads → 跳转结果页
 */

import { useState } from 'react'
import { View, Text, Textarea } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { authedRequest, ApiError } from '../../../../api/client'
import type { CreateReviewUploadResponse } from '../../../../api/review'
import './index.scss'

const MAX_CHARS = 5000

export default function ReviewUploadPage() {
  const [text, setText] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const charCount = text.length
  const overLimit = charCount > MAX_CHARS
  const canSubmit = charCount > 0 && !overLimit && !pending

  const handleSubmit = async () => {
    if (!canSubmit) return
    setError(null)
    setPending(true)
    try {
      const res = await authedRequest<CreateReviewUploadResponse>(
        '/review/uploads',
        'POST',
        { text },
      )
      Taro.navigateTo({ url: `/subpackages/review/pages/review-result/index?uploadId=${res.upload_id}` })
    } catch (e) {
      setError(_humanizeError(e))
    } finally {
      setPending(false)
    }
  }

  return (
    <View className="review-upload">
      <View className="review-upload-header">
        <Text className="review-upload-back" onClick={() => Taro.navigateBack()}>
          ← 返回
        </Text>
        <Text className="review-upload-title">复盘师</Text>
        <View className="review-upload-spacer" />
      </View>

      <View className="review-upload-body">
        <Text className="review-upload-heading">粘贴对话内容</Text>
        <Text className="review-upload-desc">
          把你想复盘的对话粘贴下来，教练 K 帮你逐句分析
        </Text>
        <Text className="review-upload-hint">
          格式：以「对方：」或「我：」开头分行，每行一句话
        </Text>

        <View className="review-upload-card">
          <Textarea
            className="review-upload-textarea"
            value={text}
            onInput={(e) => setText(e.detail.value)}
            placeholder={'对方：你周末有空吗？\n我：有安排了，下次约。'}
            maxlength={MAX_CHARS + 100}
            autoFocus={false}
          />
          <View className="review-upload-footer">
            <Text className={`review-upload-count ${overLimit ? 'review-upload-count--over' : ''}`}>
              {charCount} / {MAX_CHARS}
            </Text>
            <View
              className={`review-upload-btn ${!canSubmit ? 'review-upload-btn--disabled' : ''}`}
              onClick={handleSubmit}
            >
              <Text className="review-upload-btn-text">
                {pending ? '分析中…' : '开始复盘'}
              </Text>
            </View>
          </View>
        </View>

        {error && (
          <Text className="review-upload-error">{error}</Text>
        )}
      </View>
    </View>
  )
}

function _humanizeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 400) return '内容包含敏感信息，请修改后重试'
    if (e.status === 422) return '文本格式不对，请检查后重试'
  }
  return '提交失败，请稍后再试'
}
