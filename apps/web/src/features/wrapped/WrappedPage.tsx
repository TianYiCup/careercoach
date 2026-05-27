/**
 * WrappedPage — cyberpunk redesign (design-spec §10).
 *
 * Data layer unchanged: `useShareCard` from sandbox + WrappedCard Canvas.
 * Visual rebuild only:
 *   · NeuralParticles + tech-grid + scanline background.
 *   · Tab switcher reskinned as cyber pill toggle.
 *   · Generate CTA → MagneticButton lime variant.
 *   · Card preview wrapped in HudFrame "WRAPPED · 9:16" panel.
 */

import { useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowLeft, Download, Sparkles } from 'lucide-react'

import { WrappedCard } from '../../components'
import {
  GlowText,
  HudFrame,
  MagneticButton,
  NeuralParticles,
} from '../../components/cyber'
import { useShareCard } from '../sandbox/useShareCard'

type Tab = 'weekly' | 'wrapped'

export function WrappedPage({ onBack }: { onBack: () => void }) {
  const [activeTab, setActiveTab] = useState<Tab>('weekly')
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
    if (!shareState.card?.png_url) {
      return
    }
    const link = document.createElement('a')
    link.href = shareState.card.png_url
    link.download = `careercoach-${activeTab}-${Date.now()}.png`
    link.target = '_blank'
    link.click()
  }

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-cyber-void text-white">
      <NeuralParticles count={1000} />
      <div className="pointer-events-none fixed inset-0 -z-10 tech-grid opacity-20" />
      <div className="pointer-events-none fixed inset-0 -z-10 scanline" />
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 -z-10"
        style={{
          background:
            'radial-gradient(ellipse at center, transparent 0%, rgba(5,5,5,0.4) 50%, rgba(5,5,5,0.92) 100%)',
        }}
      />

      <div className="relative z-10 mx-auto flex w-full max-w-2xl flex-1 flex-col px-6 py-8">
        <button
          type="button"
          onClick={() => {
            reset()
            onBack()
          }}
          className="inline-flex w-fit items-center gap-1 font-bebas text-xs tracking-[0.24em] text-white/60 transition-colors hover:text-cyber-cyan"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          BACK
        </button>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mt-6 text-center"
        >
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-cyber-lime/30 bg-cyber-lime/5 px-3 py-1">
            <Sparkles className="h-3 w-3 text-cyber-lime" />
            <span className="font-bebas text-[11px] tracking-[0.28em] text-cyber-lime">
              REPLAY · SHAREABLE
            </span>
          </div>
          <h1 className="font-orbitron text-6xl font-black uppercase leading-none tracking-tight">
            <GlowText variant="gradient">WRAPPED</GlowText>
          </h1>
          <p className="mt-3 font-display text-xl italic text-white/80">
            把今年练得最离谱的那条对线，打包成 9:16 大卡。
          </p>
        </motion.div>

        {/* Tab pill */}
        <div className="mt-8 flex justify-center">
          <div className="inline-flex items-center gap-1 rounded-full border border-cyber-hairline bg-cyber-deep/60 p-1 backdrop-blur-md">
            {(['weekly', 'wrapped'] as const).map(t => (
              <button
                key={t}
                type="button"
                onClick={() => {
                  setActiveTab(t)
                  reset()
                }}
                className={`rounded-full px-5 py-2 font-bebas text-xs tracking-[0.22em] transition-colors ${
                  activeTab === t
                    ? 'bg-gradient-to-r from-cyber-cyan via-vivid-purple to-cyber-magenta text-white shadow-[0_0_16px_rgba(108,77,255,0.5)]'
                    : 'text-white/60 hover:text-white'
                }`}
              >
                {t === 'weekly' ? 'WEEKLY' : `${currentYear} YEAR`}
              </button>
            ))}
          </div>
        </div>

        {/* Generate CTA */}
        {!shareState.card && (
          <HudFrame
            label="GENERATE · CARD"
            tag={activeTab === 'weekly' ? '7D' : `${currentYear}`}
            className="cyber-glass-edge mt-8 rounded-3xl p-8"
          >
            <div className="flex flex-col items-center gap-5 text-center">
              <p className="text-sm text-white/70">
                {activeTab === 'weekly'
                  ? '回顾你这周的对练表现，生成周报战报卡'
                  : `${currentYear} 年度 Wrapped，看看你的沟通成长轨迹`}
              </p>
              <MagneticButton
                type="button"
                variant="lime"
                onClick={handleGenerate}
                disabled={shareState.isGenerating}
              >
                <Sparkles className="h-4 w-4" />
                {shareState.isGenerating
                  ? '生成中…'
                  : `生成 ${activeTab === 'weekly' ? '周报' : '年报'} 卡`}
              </MagneticButton>
            </div>
          </HudFrame>
        )}

        {shareState.error && (
          <div
            role="alert"
            className="mt-6 flex items-center justify-between rounded-2xl border border-cyber-magenta/40 bg-cyber-magenta/10 px-4 py-2"
          >
            <span className="font-mono text-xs text-cyber-magenta">⚠ {shareState.error}</span>
            <button
              type="button"
              onClick={dismissError}
              className="text-cyber-magenta/70 hover:text-cyber-magenta"
            >
              ×
            </button>
          </div>
        )}

        {shareState.card && (
          <HudFrame
            label={activeTab === 'weekly' ? 'WEEKLY · CARD' : `${currentYear} · YEAR`}
            tag="SHARE"
            className="cyber-glass-edge mt-8 rounded-3xl p-8"
          >
            <div className="flex flex-col items-center gap-5">
              <WrappedCard
                score={activeTab === 'weekly' ? 7.5 : 8.2}
                comment={activeTab === 'weekly' ? '这周表现可圈可点' : '今年的你，我都服了'}
                gradient={activeTab === 'weekly' ? 'vivid' : 'glory'}
                expression={activeTab === 'weekly' ? '😎' : '✨'}
                badges={
                  activeTab === 'weekly'
                    ? ['气场+1', '逻辑+1']
                    : ['封神时刻', '气场+1', '共情+1']
                }
              />
              <p className="font-mono text-[11px] text-white/50">HIGH-RES · SERVER RENDERED</p>
              <MagneticButton type="button" variant="lime" onClick={handleDownloadServerPng}>
                <Download className="h-4 w-4" />
                下载高清 PNG
              </MagneticButton>
              {activeTab === 'wrapped' && shareState.card.pages.length > 1 && (
                <p className="font-mono text-[11px] text-white/50">
                  PAGES · {shareState.card.pages.length}（首页为封面）
                </p>
              )}
            </div>
          </HudFrame>
        )}
      </div>
    </div>
  )
}
