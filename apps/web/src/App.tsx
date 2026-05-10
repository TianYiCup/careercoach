import type { AppMode } from '@careercoach/shared'
import { APP_MODES } from '@careercoach/shared'

const MODE_ICONS: Record<AppMode, { icon: string; label: string; color: string }> = {
  sandbox: { icon: '🥊', label: '沙盘对练', color: 'text-vivid-purple' },
  copilot: { icon: '🎧', label: '实战副驾', color: 'text-vivid-cyan' },
  review: { icon: '🔍', label: '复盘师', color: 'text-vivid-green' },
}

function App() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-bg-primary px-4">
      {/* Hero Section */}
      <div className="text-center max-w-lg">
        <h1 className="text-5xl md:text-6xl font-display text-vivid-purple mb-4 tracking-tight">
          CareerCoach AI
        </h1>
        <p className="text-xl md:text-2xl text-text-secondary font-body mb-2">
          不教你说违心话，只教你说真话还能赢。
        </p>
        <p className="text-sm text-text-muted font-body mb-8">
          中文语境对话练习教练 · 面向 18-25 岁在校大学生 + 实习生
        </p>

        {/* CTA */}
        <a
          href="#sandbox"
          className="inline-block px-8 py-3 rounded-xl bg-vivid-purple text-white font-body font-medium text-lg shadow-glow-purple hover:scale-105 transition-transform"
        >
          开始对练
        </a>
      </div>

      {/* Three Modes */}
      <div className="mt-16 grid grid-cols-1 md:grid-cols-3 gap-6 max-w-3xl w-full">
        {APP_MODES.map((mode) => {
          const m = MODE_ICONS[mode]
          return (
            <div key={mode} className="glass rounded-xl p-6 text-center">
              <div className="text-3xl mb-3" role="img" aria-label={m.label}>{m.icon}</div>
              <h2 className={`text-lg font-display ${m.color} mb-2`}>{m.label}</h2>
              <p className="text-sm text-text-secondary">
                {mode === 'sandbox' && '和 AI 扮演的对手练习对话，教练 K 实时给提示'}
                {mode === 'copilot' && '真实对话中耳机里小声提示下一句怎么回'}
                {mode === 'review' && '上传聊天截图，AI 标记每句的得失分'}
              </p>
            </div>
          )
        })}
      </div>

      {/* Footer */}
      <footer className="mt-auto pt-12 pb-6 text-center text-xs text-text-muted">
        CareerCoach AI · Sprint 0 · Made with 🥊
      </footer>
    </div>
  )
}

export default App
