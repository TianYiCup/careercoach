import { useState, useCallback } from 'react'
import { GlassCard, BlobBackground, MascotReaction, StickerBadge } from '../../components'
import type { WeaknessProfile } from '../../api/v1/types'

// --- Mock data (until A-role implements GET /me/weaknesses) ---

const MOCK_PROFILE: WeaknessProfile = {
  total_sessions: 12,
  top_weakness: {
    tag: '主动让步',
    count: 9,
    percentage: 38,
    remark: '你怎么这么爱认错',
  },
  weaknesses: [
    { tag: '缺数据支撑', count: 6, percentage: 24, remark: '嘴上没毛，数据来凑啊' },
    { tag: '情绪外露', count: 4, percentage: 17, remark: '脸上写满了"我好慌"' },
    { tag: '被带节奏', count: 3, percentage: 13, remark: '对方一施压你就跟跑了' },
    { tag: '不敢提问', count: 2, percentage: 8, remark: '问一句不会少块肉' },
  ],
  recommendations: [
    { scenario_id: 'sc_001', title: '拒绝加班谈判', reason: '练"说不"最对症' },
    { scenario_id: 'sc_002', title: '实习转正薪资谈判', reason: '用数据反击压价' },
    { scenario_id: 'sc_003', title: '室友深夜打游戏', reason: '从低冲突开始练边界感' },
  ],
}

/** Progress bar color based on percentage */
function barColor(pct: number): string {
  if (pct >= 35) return 'bg-vivid-orange'
  if (pct >= 20) return 'bg-vivid-yellow'
  return 'bg-vivid-cyan'
}

// --- WeaknessProfilePage ---

export function WeaknessProfilePage({ onBack }: { onBack: () => void }) {
  // Currently uses mock data; will switch to API call when A-role
  // implements GET /me/weaknesses
  const [profile] = useState<WeaknessProfile>(MOCK_PROFILE)
  const [shareMode, setShareMode] = useState(false)

  const handleShare = useCallback(() => {
    setShareMode(true)
    // In production: generate a sharecard or native share sheet
    setTimeout(() => setShareMode(false), 2000)
  }, [])

  const topPct = profile.top_weakness.percentage

  // Dynamic mascot: many weaknesses = caring, few = confident
  const mascotExpression = profile.weaknesses.length >= 3 ? 'caring' : 'confident'

  return (
    <div className="relative min-h-screen flex flex-col items-center px-4 py-8 overflow-y-auto">
      <BlobBackground />

      <div className="relative z-10 w-full max-w-md space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={onBack}
            className="text-ink-text-2 text-sm hover:text-ink-text transition-colors"
          >
            &larr; 返回
          </button>
          <button
            type="button"
            onClick={handleShare}
            className="text-sm text-vivid-purple font-body hover:text-vivid-purple-soft transition-colors"
          >
            {shareMode ? '已复制链接' : '分享'}
          </button>
        </div>

        {/* K intro */}
        <div className="flex items-start gap-4">
          <MascotReaction expression={mascotExpression} size="md" />
          <div className="flex-1">
            <p className="text-sm text-ink-text-2 font-body">
              教练 K 说：
            </p>
            <p className="text-base text-ink-text font-body mt-1">
              扒了你 {profile.total_sessions} 次复盘
            </p>
            <p className="text-sm text-ink-text-3 font-body">
              发现了这些……
            </p>
          </div>
        </div>

        {/* Top weakness — hero GlassCard (Wrapped style) */}
        <GlassCard glow className="space-y-3 text-center">
          <p className="text-xs text-ink-text-3 font-body">
            你最常翻车的瞬间
          </p>

          {/* Big percentage — display italic */}
          <p className="text-5xl font-display italic text-gradient-vivid">
            {topPct}%
          </p>

          <p className="text-lg text-ink-text font-body font-medium">
            {profile.top_weakness.tag}
          </p>
          <p className="text-sm text-ink-text-2 font-body">
            出现 {profile.top_weakness.count} 次
          </p>

          {/* K's sharp remark */}
          <div className="pt-2 border-t border-ink-line/40">
            <p className="text-sm text-ink-text-2 font-body">
              K 锐评：
              <span className="text-vivid-orange ml-1">
                "{profile.top_weakness.remark}"
              </span>
            </p>
          </div>
        </GlassCard>

        {/* Secondary weaknesses — ranked list */}
        <div className="space-y-3">
          {profile.weaknesses.map((w, idx) => (
            <div
              key={w.tag}
              className="flex items-center gap-3 px-1"
            >
              <span className="text-xs text-ink-text-3 font-mono w-6">
                #{idx + 2}
              </span>
              <div className="flex-1">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm text-ink-text font-body">{w.tag}</span>
                  <span className="text-xs text-ink-text-3 font-body">{w.percentage}%</span>
                </div>
                <div className="h-2 rounded-full bg-ink-card overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${barColor(w.percentage)}`}
                    style={{ width: `${w.percentage}%` }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* K's recommendations — "开小灶" section */}
        <GlassCard className="space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-base" role="img" aria-label="训练">💪</span>
            <p className="text-sm text-ink-text font-body font-medium">
              K 给你开小灶
            </p>
          </div>

          <div className="flex gap-3 overflow-x-auto pb-2 -mx-1 px-1">
            {profile.recommendations.map((rec) => (
              <button
                key={rec.scenario_id}
                type="button"
                onClick={() => onBack()} // Navigate to sandbox with scenario pre-selected
                className="flex-shrink-0 w-40 p-3 rounded-radius-md bg-ink-card/60 border border-ink-line text-left hover:border-vivid-purple transition-colors"
              >
                <p className="text-sm text-ink-text font-body font-medium truncate">
                  {rec.title}
                </p>
                <p className="text-xs text-ink-text-3 font-body mt-1 line-clamp-2">
                  {rec.reason}
                </p>
              </button>
            ))}
          </div>
        </GlassCard>

        {/* Quick stats row */}
        <div className="flex items-center justify-center gap-3">
          <StickerBadge variant="orange">
            {profile.weaknesses.length + 1} 个弱点
          </StickerBadge>
          <StickerBadge variant="cyan">
            {profile.total_sessions} 次复盘
          </StickerBadge>
        </div>
      </div>
    </div>
  )
}
