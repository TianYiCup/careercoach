/**
 * 沙盘对练房 — 小程序文字版
 * design-spec §9.3 / PRD §5.2 mobile wireframe
 *
 * B-7: 删除 isMockMode() 默认 + 所有 mock fallback
 * B-6: 401 由 API 层全局处理（client.ts/sandbox.ts）
 * B-1/B-2: AGE_REQUIRED/MINOR_QUIET_HOURS 由 API 层全局处理
 *
 * 小程序适配限制：
 * - GlassCard: backdrop-filter 降级为 rgba + 1px border
 * - Mascot: Lottie 兜底（v1 先用 emoji）
 * - 无 framer-motion: Taro Animation API
 * - SSE: wx.request + enableChunked + onChunkMessage
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { View, Text, Input, ScrollView } from '@tarojs/components'
import Taro from '@tarojs/taro'
import {
  createSession,
  endSession as endSessionApi,
  sendTurnSSE,
  type ChatMessage,
  type ToneLevel,
  type SseEventFrame,
  type EndSessionResponse,
} from '../../api/sandbox'
import './index.scss'

// --- Session State ---

interface SessionState {
  sessionId: string | null
  messages: ChatMessage[]
  streamingText: string
  isStreaming: boolean
  hints: { safe: string; aggressive: string; humor: string } | null
  activeTone: ToneLevel
  turnsUsed: number
  turnsLeft: number
  score: EndSessionResponse | null
  started: boolean
  error: string | null
  /** Moderation redirect resource (crisis hotline) — PRD §3.0.5 */
  redirectResource: { title: string; url: string } | null
}

const INITIAL_STATE: SessionState = {
  sessionId: null,
  messages: [],
  streamingText: '',
  isStreaming: false,
  hints: null,
  activeTone: 'aggro',
  turnsUsed: 0,
  turnsLeft: 30,
  score: null,
  started: false,
  error: null,
  redirectResource: null,
}

// --- Component ---

export default function SandboxPage() {
  const [state, setState] = useState<SessionState>(INITIAL_STATE)
  const [input, setInput] = useState('')
  const [showExitConfirm, setShowExitConfirm] = useState(false)
  const requestTaskRef = useRef<WxRequestTask | null>(null)
  const scrollViewRef = useRef<string>('scroll-bottom')

  // Auto-start session
  useEffect(() => {
    startSession()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-scroll on new messages
  useEffect(() => {
    scrollViewRef.current = `bottom-${Date.now()}`
  }, [state.messages, state.streamingText])

  // 30-round auto-end
  useEffect(() => {
    if (state.started && state.turnsLeft === 0 && !state.isStreaming && !state.score) {
      handleEndSession()
    }
  }, [state.turnsLeft]) // eslint-disable-line react-hooks/exhaustive-deps

  const startSession = async () => {
    setState((s) => (s.error ? { ...s, error: null } : s))
    try {
      const res = await createSession({
        mode: 'sandbox',
        scenario_id: 'scenario_campus_overtime',
        persona_id: 'boss_strict',
        user_goal: '拒绝加班且不撕破脸',
      })
      setState((s) => ({
        ...s,
        sessionId: res.session_id,
        started: true,
        messages: [{ role: 'opponent', text: res.opening_line }],
      }))
    } catch {
      setState((s) => ({
        ...s,
        error: '加载失败，请稍后重试',
      }))
    }
  }

  const handleSend = async () => {
    const text = input.trim()
    if (!text || state.isStreaming || !state.sessionId) return
    setInput('')

    setState((s) => ({
      ...s,
      messages: [...s.messages, { role: 'user', text }],
      isStreaming: true,
      streamingText: '',
      hints: null,
    }))

    const task = sendTurnSSE(
      state.sessionId,
      text,
      (frame: SseEventFrame) => {
        setState((s) => {
          switch (frame.event) {
            case 'opponent.delta':
              return { ...s, streamingText: s.streamingText + frame.data.text }
            case 'opponent.done':
              return {
                ...s,
                streamingText: '',
                isStreaming: false,
                messages: [...s.messages, { role: 'opponent', text: frame.data.full_text }],
              }
            case 'coach.hint':
              return { ...s, hints: frame.data }
            case 'meta':
              return {
                ...s,
                turnsUsed: frame.data.turns_used,
                turnsLeft: frame.data.turns_left,
              }
            case 'moderation': {
              if (frame.data.verdict === 'redirect' && frame.data.redirect_resource) {
                return {
                  ...s,
                  isStreaming: false,
                  streamingText: '',
                  redirectResource: frame.data.redirect_resource,
                }
              }
              if (frame.data.verdict === 'block') {
                return {
                  ...s,
                  isStreaming: false,
                  streamingText: '',
                  messages: [
                    ...s.messages,
                    { role: 'opponent' as const, text: '（对话内容未通过审核，请换一个话题）' },
                  ],
                }
              }
              return s
            }
            default:
              return s
          }
        })
      },
      () => {
        setState((s) => ({
          ...s,
          isStreaming: false,
          streamingText: '',
          messages: [...s.messages, { role: 'opponent', text: '[连接中断]' }],
        }))
      },
    )
    requestTaskRef.current = task
  }

  const handleEndSession = async () => {
    requestTaskRef.current?.abort()
    requestTaskRef.current = null

    if (!state.sessionId) return

    try {
      const res = await endSessionApi(state.sessionId)
      setState((s) => ({ ...s, score: res, isStreaming: false }))
    } catch {
      setState((s) => ({
        ...s,
        isStreaming: false,
        error: '结算失败，可重试或先退出',
      }))
    }
  }

  const handleExitClick = () => {
    if (state.started && !state.score) {
      setShowExitConfirm(true)
    } else {
      Taro.navigateBack()
    }
  }

  const handleExitConfirm = async () => {
    setShowExitConfirm(false)
    await handleEndSession()
    Taro.navigateBack()
  }

  const handleToneChange = (tone: ToneLevel) => {
    setState((s) => ({ ...s, activeTone: tone }))
  }

  const getHintText = (): string => {
    if (!state.hints) return ''
    if (state.activeTone === 'safe') return state.hints.safe
    if (state.activeTone === 'fun') return state.hints.humor
    return state.hints.aggressive
  }

  const totalTurns = state.turnsUsed + state.turnsLeft
  const turnProgress = totalTurns > 0 ? (state.turnsUsed / totalTurns) * 100 : 0

  return (
    <View className="sandbox">
      {/* Header */}
      <View className="sandbox-header">
        <View className="sandbox-header-left" onClick={handleExitClick}>
          <Text className="sandbox-header-back">← 退出</Text>
        </View>
        <View className="sandbox-header-center">
          <Text className="sandbox-header-name">赵总（刚）</Text>
          <Text className="sandbox-header-scene">拒绝加班</Text>
        </View>
        <View className="sandbox-header-right">
          <Text className="sandbox-header-turns">
            {state.turnsUsed}/{totalTurns}
          </Text>
        </View>
      </View>

      {/* Progress bar */}
      <View className="sandbox-progress">
        <View
          className="sandbox-progress-fill"
          style={{
            width: `${turnProgress}%`,
            background: state.turnsLeft <= 3 ? '#ff6b35' : '#6c4dff',
          }}
        />
      </View>

      {/* Error banner */}
      {state.error && (
        <View className="sandbox-warning">
          <Text className="sandbox-warning-text">{state.error}</Text>
          <Text className="sandbox-warning-dismiss" onClick={() => setState((s) => ({ ...s, error: null }))}>×</Text>
        </View>
      )}

      {/* Turn limit warning */}
      {state.turnsLeft > 0 && state.turnsLeft <= 3 && !state.score && (
        <View className="sandbox-warning">
          <Text className="sandbox-warning-text">
            还剩 {state.turnsLeft} 回合，抓紧表现！
          </Text>
        </View>
      )}

      {/* Chat area */}
      <ScrollView
        className="sandbox-chat"
        scrollY
        scrollIntoView={scrollViewRef.current}
        scrollWithAnimation
      >
        {state.messages.map((msg, i) =>
          msg.role === 'opponent' ? (
            <View key={i} id={`msg-${i}`} className="sandbox-bubble-wrap sandbox-bubble-wrap--left">
              <View className="sandbox-avatar sandbox-avatar--opponent">
                <Text>👔</Text>
              </View>
              <View className="sandbox-bubble sandbox-bubble--opponent">
                <Text className="sandbox-bubble-text">{msg.text}</Text>
              </View>
            </View>
          ) : (
            <View key={i} id={`msg-${i}`} className="sandbox-bubble-wrap sandbox-bubble-wrap--right">
              <View className="sandbox-bubble sandbox-bubble--user">
                <Text className="sandbox-bubble-text sandbox-bubble-text--user">{msg.text}</Text>
              </View>
              <View className="sandbox-avatar sandbox-avatar--user">
                <Text>我</Text>
              </View>
            </View>
          ),
        )}

        {/* Streaming text */}
        {state.isStreaming && state.streamingText && (
          <View className="sandbox-bubble-wrap sandbox-bubble-wrap--left">
            <View className="sandbox-avatar sandbox-avatar--opponent">
              <Text>👔</Text>
            </View>
            <View className="sandbox-bubble sandbox-bubble--opponent">
              <Text className="sandbox-bubble-text">
                {state.streamingText}
                <Text className="sandbox-cursor">|</Text>
              </Text>
            </View>
          </View>
        )}

        {/* Typing indicator */}
        {state.isStreaming && !state.streamingText && (
          <View className="sandbox-bubble-wrap sandbox-bubble-wrap--left">
            <View className="sandbox-avatar sandbox-avatar--opponent">
              <Text>👔</Text>
            </View>
            <View className="sandbox-bubble sandbox-bubble--opponent">
              <View className="sandbox-typing">
                <View className="sandbox-typing-dot" />
                <View className="sandbox-typing-dot sandbox-typing-dot--2" />
                <View className="sandbox-typing-dot sandbox-typing-dot--3" />
              </View>
            </View>
          </View>
        )}

        {/* Coach hint card */}
        {state.hints && !state.score && (
          <View className="sandbox-hint-card">
            <Text className="sandbox-hint-label">💡 教练 K 来了</Text>
            <Text className="sandbox-hint-text">{getHintText()}</Text>
            <View className="sandbox-hint-tones">
              <View
                className={`sandbox-tone-btn ${state.activeTone === 'safe' ? 'sandbox-tone-btn--active-safe' : ''}`}
                onClick={() => handleToneChange('safe')}
              >
                <Text>🐶 稳</Text>
              </View>
              <View
                className={`sandbox-tone-btn ${state.activeTone === 'aggro' ? 'sandbox-tone-btn--active-aggro' : ''}`}
                onClick={() => handleToneChange('aggro')}
              >
                <Text>🔥 刚</Text>
              </View>
              <View
                className={`sandbox-tone-btn ${state.activeTone === 'fun' ? 'sandbox-tone-btn--active-fun' : ''}`}
                onClick={() => handleToneChange('fun')}
              >
                <Text>🤡 活</Text>
              </View>
            </View>
          </View>
        )}

        {/* Score result — B-9 aligned with web ScorePage */}
        {state.score && (
          <View className="sandbox-score">
            {/* Result emoji + verdict */}
            <View className="sandbox-score-header">
              <Text className="sandbox-score-emoji">
                {state.score.score.result === 'shenfeng' ? '✨' : state.score.score.result === 'fanche' ? '💥' : '🌀'}
              </Text>
              <Text className="sandbox-score-verdict">
                {state.score.score.result === 'shenfeng' ? '封神' : state.score.score.result === 'fanche' ? '翻车' : '路过'}
              </Text>
            </View>

            {/* Five-dimension score bars — replacement for radar chart */}
            <View className="sandbox-score-dims">
              {[
                { label: '气场', value: state.score.score.aura },
                { label: '逻辑', value: state.score.score.logic },
                { label: '共情', value: state.score.score.emotion },
                { label: '专业', value: state.score.score.professionalism },
                { label: '目标', value: state.score.score.goal_achieve },
              ].map((dim) => (
                <View key={dim.label} className="sandbox-score-dim">
                  <Text className="sandbox-score-dim-label">{dim.label}</Text>
                  <View className="sandbox-score-dim-bar">
                    <View
                      className="sandbox-score-dim-fill"
                      style={{ width: `${dim.value * 10}%` }}
                    />
                  </View>
                  <Text className="sandbox-score-dim-value">{dim.value}</Text>
                </View>
              ))}
            </View>

            {/* Highlights & failures */}
            {state.score.score.highlights && (
              <View className="sandbox-score-card sandbox-score-card--green">
                <Text className="sandbox-score-card-label">K 说你棒的地方</Text>
                <Text className="sandbox-score-card-text">{state.score.score.highlights}</Text>
              </View>
            )}
            {state.score.score.failures && (
              <View className="sandbox-score-card sandbox-score-card--orange">
                <Text className="sandbox-score-card-label">可以更好的地方</Text>
                <Text className="sandbox-score-card-text">{state.score.score.failures}</Text>
              </View>
            )}

            <View className="sandbox-score-btn" onClick={() => Taro.navigateBack()}>
              <Text className="sandbox-score-btn-text">返回首页</Text>
            </View>
          </View>
        )}

        {/* End session button */}
        {state.started && !state.score && (
          <View className="sandbox-end-btn" onClick={handleEndSession}>
            <Text className="sandbox-end-btn-text">结束对练</Text>
          </View>
        )}

        {/* Scroll anchor */}
        <View id="scroll-bottom" />
      </ScrollView>

      {/* Input bar */}
      {state.started && !state.score && (
        <View className="sandbox-input-bar">
          <Input
            className="sandbox-input"
            value={input}
            onInput={(e) => setInput(e.detail.value)}
            confirmType="send"
            onConfirm={handleSend}
            disabled={state.isStreaming}
            placeholder="说点什么..."
            placeholderClass="sandbox-input-placeholder"
          />
          <View
            className={`sandbox-send-btn ${(!input.trim() || state.isStreaming) ? 'sandbox-send-btn--disabled' : ''}`}
            onClick={handleSend}
          >
            <Text className="sandbox-send-btn-text">➤</Text>
          </View>
        </View>
      )}

      {/* Moderation redirect — crisis hotline. PRD §3.0.5 */}
      {state.redirectResource && (
        <View className="sandbox-modal-overlay">
          <View className="sandbox-modal">
            <Text className="sandbox-modal-title">{state.redirectResource.title}</Text>
            <Text className="sandbox-modal-desc" onClick={() => {
              Taro.setClipboardData({ data: state.redirectResource!.url })
            }}>
              复制求助链接
            </Text>
            <View className="sandbox-modal-actions">
              <View
                className="sandbox-modal-confirm"
                onClick={() => setState((s) => ({ ...s, redirectResource: null }))}
              >
                <Text>关闭</Text>
              </View>
            </View>
          </View>
        </View>
      )}

      {/* Exit confirm modal */}
      {showExitConfirm && (
        <View className="sandbox-modal-overlay">
          <View className="sandbox-modal">
            <Text className="sandbox-modal-title">确定要退出对练吗？</Text>
            <Text className="sandbox-modal-desc">当前进度不会被保存</Text>
            <View className="sandbox-modal-actions">
              <View
                className="sandbox-modal-cancel"
                onClick={() => setShowExitConfirm(false)}
              >
                <Text>继续练</Text>
              </View>
              <View className="sandbox-modal-confirm" onClick={handleExitConfirm}>
                <Text>确定退出</Text>
              </View>
            </View>
          </View>
        </View>
      )}
    </View>
  )
}
