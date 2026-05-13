import {
  BlobBackground,
  GlassCard,
  MascotReaction,
  StickerBadge,
} from './components'

const BADGE_VARIANTS = [
  'purple',
  'green',
  'orange',
  'pink',
  'cyan',
  'yellow',
] as const

function App() {
  return (
    <div className="relative min-h-screen flex flex-col items-center px-4 py-12 overflow-hidden">
      {/* Blob 背景装饰 */}
      <BlobBackground />

      {/* ── 启动页 Hero ────────────────────────────── */}
      <div className="relative z-10 text-center max-w-lg mt-8">
        {/* 教练 K 弹簧入场 */}
        <MascotReaction expression="confident" size="lg" showLabel />

        <h1 className="mt-6 text-5xl md:text-6xl font-display italic tracking-tight text-gradient-vivid">
          CareerCoach AI
        </h1>
        <p className="mt-2 text-xl md:text-2xl text-ink-text-2 font-body">
          不教你说违心话，只教你说真话还能赢。
        </p>
        <p className="mt-1 text-sm text-ink-text-3 font-body">
          中文语境对话练习教练 · 面向 18-25 岁
        </p>

        <a
          href="#demo"
          className="mt-6 inline-block px-8 py-3 rounded-radius-pill gradient-vivid text-white font-body font-medium text-lg glow-purple hover:scale-105 transition-transform"
        >
          开始对练
        </a>
      </div>

      {/* ── 组件展示区 ────────────────────────────── */}
      <section id="demo" className="relative z-10 mt-16 w-full max-w-3xl space-y-12">
        {/* 卡片区 */}
        <GlassCard glow className="text-center">
          <h2 className="text-lg font-display text-vivid-purple mb-2">
            GlassCard + Glow
          </h2>
          <p className="text-sm text-ink-text-2">
            毛玻璃卡片 + 紫色发光边框
          </p>
        </GlassCard>

        {/* StickerBadge 展示 */}
        <div className="text-center">
          <h2 className="text-lg font-display text-ink-text mb-4">
            StickerBadge
          </h2>
          <div className="flex flex-wrap items-center justify-center gap-3">
            {BADGE_VARIANTS.map((v) => (
              <StickerBadge key={v} variant={v}>
                {v === 'purple' && '🥊 沙盘'}
                {v === 'green' && '✨ 封神'}
                {v === 'orange' && '💥 翻车'}
                {v === 'pink' && '😎 自信'}
                {v === 'cyan' && '🐶 稳如老狗'}
                {v === 'yellow' && '🤡 整活儿'}
              </StickerBadge>
            ))}
          </div>
        </div>

        {/* Mascot 表情切换 */}
        <div className="text-center">
          <h2 className="text-lg font-display text-ink-text mb-4">
            MascotReaction — 点击切换表情
          </h2>
          <div className="flex items-center justify-center gap-8">
            <MascotReaction expression="confident" size="sm" />
            <MascotReaction expression="thinking" size="md" showLabel />
            <MascotReaction expression="godlike" size="sm" />
          </div>
        </div>

        {/* 渐变展示 */}
        <div className="text-center">
          <h2 className="text-lg font-display text-ink-text mb-4">
            Gradient Tokens
          </h2>
          <div className="grid grid-cols-3 gap-4">
            <div className="gradient-vivid rounded-radius-md py-4 text-white text-sm font-body font-medium">
              vivid
            </div>
            <div className="gradient-glory rounded-radius-md py-4 text-ink-bg text-sm font-body font-bold">
              glory
            </div>
            <div className="gradient-crash rounded-radius-md py-4 text-white text-sm font-body font-medium">
              crash
            </div>
          </div>
        </div>
      </section>

      <footer className="relative z-10 mt-auto pt-12 pb-6 text-center text-xs text-ink-text-3">
        CareerCoach AI · Sprint 0 D3 · Design Token + Mascot + Components
      </footer>
    </div>
  )
}

export default App
