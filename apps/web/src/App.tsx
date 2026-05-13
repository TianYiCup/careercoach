import { useState } from 'react'
import {
  BlobBackground,
  MascotReaction,
  HintCardV2,
} from './components'
import type { ToneLevel } from './components'

/** 沙盘对练房 — design-spec §9.3
 *  静态页 + MSW mock 数据驱动
 *  顶栏 K 表情 + 对手气泡(渐变) + 用户气泡(vivid 渐变) + HintCardV2 + 输入框
 */

interface ChatBubble {
  role: 'opponent' | 'user'
  text: string
}

const MOCK_CHAT: ChatBubble[] = [
  { role: 'opponent', text: '周末加个班吧。' },
  { role: 'user', text: '我有事。' },
  { role: 'opponent', text: '什么事比工作重要？' },
]

function SandboxRoom() {
  const [activeTone, setActiveTone] = useState<ToneLevel>('aggro')
  const [input, setInput] = useState('')

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden">
      <BlobBackground />

      {/* ── 顶栏 ────────────────────────────── */}
      <header className="relative z-10 flex items-center justify-between px-4 py-3 glass">
        <button type="button" className="text-ink-text-2 text-sm">← 退出</button>
        <div className="flex items-center gap-2">
          <MascotReaction expression="confident" size="sm" />
          <span className="text-sm font-body text-ink-text">赵总（刚）</span>
        </div>
        <span className="text-xs text-ink-text-3">4/30</span>
      </header>

      {/* ── 对话区 ──────────────────────────── */}
      <main className="relative z-10 flex-1 overflow-y-auto px-4 py-6 space-y-4">
        {MOCK_CHAT.map((msg, i) =>
          msg.role === 'opponent' ? (
            <div key={i} className="flex items-start gap-2 max-w-[80%]">
              <div className="w-8 h-8 rounded-full bg-ink-card-2 flex items-center justify-center text-xs flex-shrink-0">
                👔
              </div>
              <div className="rounded-radius-md rounded-tl-none px-4 py-3 bg-ink-card-2 border border-ink-line">
                <p className="text-sm text-ink-text font-body">{msg.text}</p>
              </div>
            </div>
          ) : (
            <div key={i} className="flex items-start gap-2 max-w-[80%] ml-auto flex-row-reverse">
              <div className="w-8 h-8 rounded-full gradient-vivid flex items-center justify-center text-xs flex-shrink-0">
                我
              </div>
              <div className="rounded-radius-md rounded-tr-none px-4 py-3 gradient-vivid">
                <p className="text-sm text-white font-body">{msg.text}</p>
              </div>
            </div>
          ),
        )}

        {/* 等待对手回复时三点跳动 */}
        <div className="flex items-start gap-2 max-w-[80%]">
          <div className="w-8 h-8 rounded-full bg-ink-card-2 flex items-center justify-center text-xs flex-shrink-0">
            👔
          </div>
          <div className="rounded-radius-md px-4 py-3 bg-ink-card-2 border border-ink-line">
            <div className="flex gap-1">
              <span className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          </div>
        </div>

        {/* ── 教练提示卡 ──────────────────────── */}
        <div className="mt-4">
          <HintCardV2
            hint="试试反问 deadline ⚡"
            activeTone={activeTone}
            onToneChange={setActiveTone}
          />
        </div>
      </main>

      {/* ── 输入框 ──────────────────────────── */}
      <footer className="relative z-10 glass px-4 py-3">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="说点什么..."
            className="flex-1 rounded-radius-pill bg-ink-card px-4 py-2 text-sm text-ink-text font-body border border-ink-line focus:border-vivid-purple focus:outline-none transition-colors"
          />
          <button
            type="button"
            className="w-10 h-10 rounded-full gradient-vivid flex items-center justify-center text-white text-lg hover:scale-105 transition-transform"
            aria-label="发送"
          >
            🎤
          </button>
        </div>
      </footer>
    </div>
  )
}

function App() {
  return <SandboxRoom />
}

export default App
