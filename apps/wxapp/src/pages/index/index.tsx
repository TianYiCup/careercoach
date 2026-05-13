import { View, Text } from '@tarojs/components'
import { Button } from '@nutui/nutui-react-taro'
import './index.scss'

/** 首页 — 设计还原 design-spec §9.2
 *  顶栏：问候 + VibePill
 *  StreakFire 连胜
 *  推荐场景卡片
 *  继续练习
 *  本周战报
 *  副驾 CTA
 */
const SCENARIOS = [
  { icon: '🥊', title: '谈薪砍价', desc: '强硬型 HR', stars: 4 },
  { icon: '😤', title: '拒绝加班', desc: 'PUA 老板', stars: 3 },
  { icon: '💼', title: '面试自我介绍', desc: '冷面面试官', stars: 5 },
]

export default function Index() {
  return (
    <View className="home">
      {/* ── 顶栏 ──────────────────── */}
      <View className="home-header">
        <View className="home-greeting">
          <Text className="home-greeting-text">早上好，小林 ☀️</Text>
        </View>
        <View className="home-vibe">
          <Text className="home-vibe-pill">🔥 燃爆</Text>
        </View>
      </View>

      {/* ── StreakFire ─────────────── */}
      <View className="home-streak">
        <Text className="home-streak-text">🔥 已经卷了 12 天</Text>
        <View className="home-streak-bar">
          <View className="home-streak-fill" />
        </View>
      </View>

      {/* ── 推荐场景 ───────────────── */}
      <View className="home-section">
        <Text className="home-section-title">教练 K 觉得你该练这个 👇</Text>
        <View className="home-scenarios">
          {SCENARIOS.map((s) => (
            <View key={s.title} className="home-scenario-card">
              <Text className="home-scenario-icon">{s.icon}</Text>
              <Text className="home-scenario-title">{s.title}</Text>
              <Text className="home-scenario-desc">{s.desc}</Text>
              <Text className="home-scenario-stars">{'⭐'.repeat(s.stars)}</Text>
            </View>
          ))}
        </View>
      </View>

      {/* ── 继续练习 ───────────────── */}
      <View className="home-continue">
        <Text className="home-continue-label">上次没练完...</Text>
        <View className="home-continue-card">
          <Text className="home-continue-title">和领导提涨薪 · 进度 60%</Text>
          <Button size="small" type="primary" className="home-continue-btn">
            继续 ▶
          </Button>
        </View>
      </View>

      {/* ── 本周战报 ───────────────── */}
      <View className="home-section">
        <Text className="home-section-title">本周战报 📊</Text>
        <View className="home-report">
          <View className="home-report-row">
            <Text className="home-report-label">气场</Text>
            <View className="home-report-bar">
              <View className="home-report-fill" style={{ width: '68%' }} />
            </View>
            <Text className="home-report-value">6.8</Text>
          </View>
          <View className="home-report-row">
            <Text className="home-report-label">嘴硬度</Text>
            <View className="home-report-bar">
              <View className="home-report-fill" style={{ width: '72%' }} />
            </View>
            <Text className="home-report-value">7.2</Text>
          </View>
        </View>
        <Text className="home-report-link">查看完整 Wrapped →</Text>
      </View>

      {/* ── 副驾 CTA ───────────────── */}
      <View className="home-copilot-cta">
        <Text className="home-copilot-cta-text">🎧 实战副驾 · 关键时刻在线</Text>
      </View>
    </View>
  )
}
