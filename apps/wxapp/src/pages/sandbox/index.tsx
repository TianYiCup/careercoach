/**
 * 沙盘对练房 — 小程序文字版
 * design-spec §9.3 / PRD §5.2 mobile wireframe
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
  parseSseChunk,
  type ChatMessage,
  type ToneLevel,
  type SseEventFrame,
  type Score,
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
}

// --- Mock data fallback (for dev without backend) ---

const MOCK_OPENING = '小林啊，这次项目就靠你了，周末加个班吧。'
const MOCK_REPLY = '你是不是对工作有什么误解？加班不是奉献，是剥削。'
const MOCK_HINT = {
  safe: '可以试试委婉地说自己有安排，同时给出替代方案。',
  aggressive: '直接反问 deadline 是什么时候，把压力还给对方。',
  humor: '一本正经地说"赵总我周末得给猫过生日"。',
}

function isMockMode(): boolean {
  // In dev without backend, fall back to mock
  const base = process.env.TARO_APP_API_BASE ?? ''
  return base === '' || base.includes('localhost')
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
    if (isMockMode()) {
      setState((s) => ({
        ...s,
        started: true,
        sessionId: 'mock-session',
        messages: [{ role: 'opponent', text: MOCK_OPENING }],
        turnsUsed: 1,
        turnsLeft: 29,
      }))
      return
    }

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
    } catch (err) {
      console.error('[Sandbox] startSession failed:', err)
      // Fallback to mock
      setState((s) => ({
        ...s,
        started: true,
        sessionId: 'mock-session',
        messages: [{ role: 'opponent', text: MOCK_OPENING }],
        turnsUsed: 1,
        turnsLeft: 29,
      }))
    }
  }

  const handleSend = async () => {
    const text = input.trim()
    if (!text || state.isStreaming) return
    setInput('')

    const userMsg: ChatMessage = { role: 'user', text }
    setState((s) => ({
      ...s,
      messages: [...s.messages, userMsg],
      isStreaming: true,
      streamingText: '',
      hints: null,
    }))

    if (isMockMode() || state.sessionId === 'mock-session') {
      // Mock reply with short delay
      setTimeout(() => {
        setState((s) => ({
          ...s,
          isStreaming: false,
          messages: [...s.messages, { role: 'opponent', text: MOCK_REPLY }],
          hints: MOCK_HINT,
          turnsUsed: s.turnsUsed + 1,
          turnsLeft: s.turnsLeft - 1,
        }))
      }, 1200)
      return
    }

    // Real SSE flow
    const task = sendTurnSSE(
      state.sessionId!,
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
            default:
              return s
          }
        })
      },
      (err: Error) => {
        console.error('[SSE] error:', err)
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

    if (!state.sessionId || state.sessionId === 'mock-session') {
      // Mock score
      setState((s) => ({
        ...s,
        score: {
          score: {
            aura: 7,
            logic: 8,
            emotion: 6,
            professionalism: 7,
            goal_achieve: 5,
            highlights: '能坚持立场，没有退让',
            failures: '情绪管理可以更好',
            result: 'guolu',
          },
          weakness_updates: [],
        },
        isStreaming: false,
      }))
      return
    }

    try {
      const res = await endSessionApi(state.sessionId)
      setState((s) => ({ ...s, score: res, isStreaming: false }))
    } catch (err) {
      console.error('[Sandbox] endSession failed:', err)
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

        {/* Score result */}
        {state.score && (
          <View className="sandbox-score-card">
            <Text className="sandbox-score-result">
              {state.score.score.result === 'shenfeng' ? '✨ 封神' : state.score.score.result === 'fanche' ? '💥 翻车' : '🌀 路过'}
            </Text>
            <Text className="sandbox-score-highlight">
              {state.score.score.highlights}
            </Text>
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
            <Text className="sandbox-send-btn-text">🎤</Text>
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
