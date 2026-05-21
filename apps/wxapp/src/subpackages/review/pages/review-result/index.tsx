/**
 * 复盘结果页 — 小程序版
 * PRD §3.3 / design-spec §9.6
 *
 * 三栏布局简化为纵向堆叠：概览 → 逐句分析 → 报告
 * D12-B: VerdictBadge 三色 + BetterSuggestion 一键复制
 */

import { useState, useEffect } from 'react'
import { View, Text } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { authedRequest } from '../../../../api/client'
import type { ReviewUploadResponse } from '../../../../api/review'
import './index.scss'

const VERDICT_MAP: Record<string, { label: string; color: string }> = {
  win: { label: '得分', color: '#00D68F' },
  neutral: { label: '路过', color: '#FFB800' },
  lose: { label: '失分', color: '#FF4757' },
}

export default function ReviewResultPage() {
  const router = useRouter()
  const uploadId = router.params.uploadId ?? ''

  const [data, setData] = useState<ReviewUploadResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!uploadId) return
    _fetchResult()
  }, [uploadId]) // eslint-disable-line react-hooks/exhaustive-deps

  const _fetchResult = async () => {
    try {
      const res = await authedRequest<ReviewUploadResponse>(
        `/review/uploads/${uploadId}`,
        'GET',
      )
      if (res.status === 'processing') {
        // Poll every 2s
        setTimeout(_fetchResult, 2000)
      } else {
        setData(res)
      }
    } catch {
      setError('加载复盘结果失败')
    }
  }

  if (error) {
    return (
      <View className="review-result">
        <Text className="review-result-error">{error}</Text>
        <View className="review-result-btn" onClick={() => Taro.navigateBack()}>
          <Text>返回</Text>
        </View>
      </View>
    )
  }

  if (!data) {
    return (
      <View className="review-result">
        <Text className="review-result-loading">复盘分析中，请稍候…</Text>
      </View>
    )
  }

  const { turns, summary } = data
  const loseCount = turns.filter((t) => t.verdict === 'lose').length

  return (
    <View className="review-result">
      {/* Header */}
      <View className="review-result-header">
        <Text className="review-result-back" onClick={() => Taro.navigateBack()}>
          ← 返回
        </Text>
        <Text className="review-result-title">复盘报告</Text>
        <View className="review-result-spacer" />
      </View>

      {/* Score overview */}
      {summary && (
        <View className="review-result-card review-result-card--score">
          <Text className="review-result-score-value">{summary.score}/10</Text>
          <Text className="review-result-score-label">总评分</Text>
          {loseCount > 0 && (
            <Text className="review-result-score-detail">
              共 {turns.length} 句，{loseCount} 句失分
            </Text>
          )}
        </View>
      )}

      {/* Per-turn analysis */}
      <View className="review-result-section">
        <Text className="review-result-section-title">逐句分析</Text>
        {turns.map((turn, i) => {
          const v = VERDICT_MAP[turn.verdict] ?? VERDICT_MAP.neutral!
          return (
            <View key={i} className="review-result-turn">
              <View className="review-result-turn-header">
                <Text className={`review-result-turn-speaker ${turn.speaker === 'user' ? 'review-result-turn-speaker--user' : ''}`}>
                  {turn.speaker === 'user' ? '我' : '对方'}
                </Text>
                <View className="review-result-turn-badge" style={{ background: v.color }}>
                  <Text className="review-result-turn-badge-text">{v.label}</Text>
                </View>
              </View>
              <Text className="review-result-turn-content">{turn.content}</Text>
              {turn.verdict === 'lose' && turn.reason && (
                <Text className="review-result-turn-reason">原因：{turn.reason}</Text>
              )}
              {turn.better && (
                <View className="review-result-turn-better">
                  <Text className="review-result-turn-better-label">建议改写</Text>
                  <Text className="review-result-turn-better-text">{turn.better}</Text>
                  <Text
                    className="review-result-turn-better-copy"
                    onClick={() => {
                      Taro.setClipboardData({ data: turn.better! })
                    }}
                  >
                    复制
                  </Text>
                </View>
              )}
            </View>
          )
        })}
      </View>

      {/* Summary report */}
      {summary && (
        <View className="review-result-section">
          <Text className="review-result-section-title">K 的总结</Text>
          {summary.top_failures.length > 0 && (
            <View className="review-result-card review-result-card--failures">
              <Text className="review-result-card-label">主要失分点</Text>
              {summary.top_failures.map((f, i) => (
                <Text key={i} className="review-result-card-item">• {f}</Text>
              ))}
            </View>
          )}
          {summary.improvements.length > 0 && (
            <View className="review-result-card review-result-card--improvements">
              <Text className="review-result-card-label">改善建议</Text>
              {summary.improvements.map((imp, i) => (
                <Text key={i} className="review-result-card-item">• {imp}</Text>
              ))}
            </View>
          )}
        </View>
      )}

      <View className="review-result-btn" onClick={() => Taro.navigateBack()}>
        <Text className="review-result-btn-text">完成</Text>
      </View>
    </View>
  )
}
