import { useState } from 'react'
import { BlobBackground, GlassCard, WrappedCard } from '../../components'
import { useShareCard } from '../sandbox/useShareCard'

/** Wrapped 卡演示页 — design-spec §10
 *  D14-B: 三种分享卡模式 (session / weekly / wrapped)
 *  + useShareCard 服务端生成 + WrappedCard Canvas 端渲染
 */
export function WrappedPage({ onBack }: { onBack: () => void }) {
  const [activeTab, setActiveTab] = useState<'weekly' | 'wrapped'>('weekly')
  const {
    state: shareState,
    generateWeeklyCard,
    generateWrappedCard,
    dismissError,
    reset,
  } = useShareCard()

  const currentYear = new Date().getFullYear()

  const handleGenerate = async () => {
    if (activeTab === 'weekly') {
      await generateWeeklyCard({ include_qrcode: false })
    } else {
      await generateWrappedCard(currentYear, { include_qrcode: true })
    }
  }

  const handleDownloadServerPng = () => {
    if (!shareState.card?.png_url) return
    const link = document.createElement('a')
    link.href = shareState.card.png_url
    link.download = `careercoach-${activeTab}-${Date.now()}.png`
    link.target = '_blank'
    link.click()
  }

  return (
    <div className="relative min-h-screen flex flex-col items-center px-4 py-12 overflow-y-auto">
      <BlobBackground />

      <div className="relative z-10 w-full max-w-md space-y-6 text-center">
        <button
          type="button"
          onClick={() => { reset(); onBack() }}
          className="self-start text-ink-text-2 text-sm hover:text-ink-text transition-colors"
        >
          &larr; 返回
        </button>
        <h1 className="text-3xl font-display text-ink-text mb-1">Wrapped 战报</h1>
        <p className="text-sm text-ink-text-2">生成你的专属分享卡</p>

        <div className="flex justify-center gap-3">
          <button
            type="button"
            onClick={() => { setActiveTab('weekly'); reset() }}
            className={`px-5 py-2 rounded-radius-pill font-body text-sm transition-colors ${
              activeTab === 'weekly'
                ? 'gradient-vivid text-white font-medium'
                : 'bg-ink-card border border-ink-line text-ink-text-2 hover:bg-ink-card-2'
            }`}
          >
            周报
          </button>
          <button
            type="button"
            onClick={() => { setActiveTab('wrapped'); reset() }}
            className={`px-5 py-2 rounded-radius-pill font-body text-sm transition-colors ${
              activeTab === 'wrapped'
                ? 'gradient-vivid text-white font-medium'
                : 'bg-ink-card border border-ink-line text-ink-text-2 hover:bg-ink-card-2'
            }`}
          >
            {currentYear} 年报
          </button>
        </div>

        {!shareState.card && (
          <GlassCard className="space-y-4">
            <p className="text-sm text-ink-text-2 font-body">
              {activeTab === 'weekly'
                ? '回顾你这周的对练表现，生成周报战报卡'
                : `${currentYear} 年度 Wrapped，看看你的沟通成长轨迹`}
            </p>
            <button
              type="button"
              onClick={handleGenerate}
              disabled={shareState.isGenerating}
              className="px-6 py-2.5 rounded-radius-pill gradient-vivid text-white text-sm font-body font-medium hover:scale-105 transition-transform disabled:opacity-50 glow-purple"
            >
              {shareState.isGenerating ? '生成中...' : `生成${activeTab === 'weekly' ? '周报' : '年报'}卡`}
            </button>
          </GlassCard>
        )}

        {shareState.error && (
          <div className="flex items-center justify-between px-4 py-2 bg-vivid-orange/15 border border-vivid-orange/40 rounded-radius-md">
            <span className="text-sm text-vivid-orange font-body">{shareState.error}</span>
            <button
              type="button"
              onClick={dismissError}
              className="text-vivid-orange/80 hover:text-vivid-orange text-lg leading-none"
            >
              &times;
            </button>
          </div>
        )}

        {shareState.card && (
          <GlassCard glow className="space-y-4">
            <p className="text-sm text-ink-text-2 font-body">
              {activeTab === 'weekly' ? '本周战报' : `${currentYear} Wrapped`}
            </p>
            <WrappedCard
              score={activeTab === 'weekly' ? 7.5 : 8.2}
              comment={activeTab === 'weekly' ? '这周表现可圈可点' : '今年的你，我都服了'}
              gradient={activeTab === 'weekly' ? 'vivid' : 'glory'}
              expression={activeTab === 'weekly' ? '😎' : '✨'}
              badges={activeTab === 'weekly' ? ['气场+1', '逻辑+1'] : ['封神时刻', '气场+1', '共情+1']}
            />
            <div className="space-y-2">
              <p className="text-xs text-ink-text-3 font-body">高清版（服务端渲染）</p>
              <button
                type="button"
                onClick={handleDownloadServerPng}
                className="px-5 py-2 rounded-radius-pill bg-vivid-green/20 border border-vivid-green/40 text-vivid-green font-body text-sm hover:bg-vivid-green/30 transition-colors"
              >
                下载高清 PNG
              </button>
            </div>
            {activeTab === 'wrapped' && shareState.card.pages.length > 1 && (
              <p className="text-xs text-ink-text-3 font-body">
                共 {shareState.card.pages.length} 页（首页为封面）
              </p>
            )}
          </GlassCard>
        )}
      </div>
    </div>
  )
}
