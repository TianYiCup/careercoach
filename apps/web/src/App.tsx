import { useState } from 'react'
import {
  BlobBackground,
  GlassCard,
  MascotReaction,
  HintCardV2,
  StickerBadge,
  VibePill,
  StreakFire,
  WrappedCard,
} from './components'
import type { ToneLevel } from './components'

type Page = 'home' | 'sandbox' | 'wrapped'

/** 沙盘对练房 — design-spec §9.3 */
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
      <header className="relative z-10 flex items-center justify-between px-4 py-3 glass">
        <button type="button" className="text-ink-text-2 text-sm">← 退出</button>
        <div className="flex items-center gap-2">
          <MascotReaction expression="confident" size="sm" />
          <span className="text-sm font-body text-ink-text">赵总（刚）</span>
        </div>
        <span className="text-xs text-ink-text-3">4/30</span>
      </header>
      <main className="relative z-10 flex-1 overflow-y-auto px-4 py-6 space-y-4">
        {MOCK_CHAT.map((msg, i) =>
          msg.role === 'opponent' ? (
            <div key={i} className="flex items-start gap-2 max-w-[80%]">
              <div className="w-8 h-8 rounded-full bg-ink-card-2 flex items-center justify-center text-xs flex-shrink-0">👔</div>
              <div className="rounded-radius-md rounded-tl-none px-4 py-3 bg-ink-card-2 border border-ink-line">
                <p className="text-sm text-ink-text font-body">{msg.text}</p>
              </div>
            </div>
          ) : (
            <div key={i} className="flex items-start gap-2 max-w-[80%] ml-auto flex-row-reverse">
              <div className="w-8 h-8 rounded-full gradient-vivid flex items-center justify-center text-xs flex-shrink-0">我</div>
              <div className="rounded-radius-md rounded-tr-none px-4 py-3 gradient-vivid">
                <p className="text-sm text-white font-body">{msg.text}</p>
              </div>
            </div>
          ),
        )}
        <div className="flex items-start gap-2 max-w-[80%]">
          <div className="w-8 h-8 rounded-full bg-ink-card-2 flex items-center justify-center text-xs flex-shrink-0">👔</div>
          <div className="rounded-radius-md px-4 py-3 bg-ink-card-2 border border-ink-line">
            <div className="flex gap-1">
              <span className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-2 h-2 rounded-full bg-ink-text-3 animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          </div>
        </div>
        <div className="mt-4">
          <HintCardV2 hint="试试反问 deadline ⚡" activeTone={activeTone} onToneChange={setActiveTone} />
        </div>
      </main>
      <footer className="relative z-10 glass px-4 py-3">
        <div className="flex items-center gap-2">
          <input type="text" value={input} onChange={(e) => setInput(e.target.value)} placeholder="说点什么..." className="flex-1 rounded-radius-pill bg-ink-card px-4 py-2 text-sm text-ink-text font-body border border-ink-line focus:border-vivid-purple focus:outline-none transition-colors" />
          <button type="button" className="w-10 h-10 rounded-full gradient-vivid flex items-center justify-center text-white text-lg hover:scale-105 transition-transform" aria-label="发送">🎤</button>
        </div>
      </footer>
    </div>
  )
}

/** Wrapped 卡演示页 — design-spec §10 */
function WrappedPage() {
  return (
    <div className="relative min-h-screen flex flex-col items-center justify-center px-4 py-12 overflow-hidden">
      <BlobBackground />
      <div className="relative z-10 w-full max-w-md space-y-8 text-center">
        <h1 className="text-3xl font-display text-ink-text mb-2">Wrapped 战报</h1>
        <p className="text-sm text-ink-text-2">Canvas 渲染 + PNG 下载 spike</p>
        <GlassCard className="space-y-6">
          <WrappedCard score={8.9} comment="今天的你 我 都 服 了" expression="✨" />
          <WrappedCard score={4.2} comment="翻车了，但还能救" expression="😅" gradient="crash" />
        </GlassCard>
        <p className="text-xs text-ink-text-3">design-spec §10.1 — 正式版将接入 Canvas 绘制完整卡片</p>
      </div>
    </div>
  )
}

/** 首页 — design-spec §9.2 */
function HomePage({ onNavigate }: { onNavigate: (page: Page) => void }) {
  return (
    <div className="relative min-h-screen flex flex-col items-center px-4 py-12 overflow-hidden">
      <BlobBackground />
      <div className="relative z-10 text-center max-w-lg mt-8">
        <MascotReaction expression="confident" size="lg" showLabel />
        <h1 className="mt-6 text-5xl md:text-6xl font-display italic tracking-tight text-gradient-vivid">CareerCoach AI</h1>
        <p className="mt-2 text-xl md:text-2xl text-ink-text-2 font-body">不教你说违心话，只教你说真话还能赢。</p>
        <div className="mt-4 flex items-center justify-center gap-2">
          <VibePill vibe="燃爆" />
          <VibePill vibe="雄心勃勃" />
          <VibePill vibe="佛系" />
        </div>
        <div className="mt-3 flex justify-center"><StreakFire days={12} /></div>
      </div>
      <div className="relative z-10 mt-12 w-full max-w-3xl space-y-6">
        <GlassCard className="text-center">
          <p className="text-sm text-ink-text-2 mb-3">选择体验</p>
          <div className="flex flex-wrap items-center justify-center gap-3">
            <button type="button" onClick={() => onNavigate('sandbox')} className="px-6 py-3 rounded-radius-md bg-vivid-purple/20 border border-vivid-purple/40 text-vivid-purple font-body font-medium hover:bg-vivid-purple/30 transition-colors">🥊 沙盘对练</button>
            <button type="button" onClick={() => onNavigate('wrapped')} className="px-6 py-3 rounded-radius-md bg-vivid-green/20 border border-vivid-green/40 text-vivid-green font-body font-medium hover:bg-vivid-green/30 transition-colors">📊 Wrapped 战报</button>
          </div>
        </GlassCard>
        <GlassCard glow className="text-center">
          <div className="flex flex-wrap items-center justify-center gap-2">
            <StickerBadge variant="green">✨ 封神</StickerBadge>
            <StickerBadge variant="orange">💥 翻车</StickerBadge>
            <StickerBadge variant="cyan">🐶 稳如老狗</StickerBadge>
            <StickerBadge variant="yellow">🤡 整活儿</StickerBadge>
          </div>
        </GlassCard>
      </div>
    </div>
  )
}

function App() {
  const [page, setPage] = useState<Page>('home')
  return (
    <>
      {page === 'home' && <HomePage onNavigate={setPage} />}
      {page === 'sandbox' && <SandboxRoom />}
      {page === 'wrapped' && <WrappedPage />}
    </>
  )
}

export default App
