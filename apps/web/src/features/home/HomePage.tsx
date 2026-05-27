/**
 * HomePage — cyberpunk AI-OS dashboard layout (redesign prompt + Vivid
 * Coach reconciled).
 *
 * Reading order top-to-bottom:
 *   1. NeuralParticles full-bleed background (R3F, fixed behind all).
 *   2. Top status bar — Bebas eyebrow tag + nickname + logout.
 *   3. Left floating sidebar — 6 icons, current section highlighted.
 *   4. Hero composition (split 7 / 5):
 *        · Left: layered "FUTURE / NEURAL / 教练 K 在场" massive type,
 *          tagline, MagneticButton "ENTER SANDBOX".
 *        · Right: K Mascot inside a HudFrame "CHARACTER PROFILE" card
 *          stacked above the stat strip (sessions / streak / score).
 *   5. Mode trio — 沙盘 / 副驾 / 复盘 each in a HudFrame TiltCard.
 *   6. Vibe + 三档话术 HUD strip at the bottom.
 *
 * Data layer untouched: useAuth / useStreak / useVibe are the same
 * hooks; only the presentation chrome is new.
 */

import { useEffect } from 'react'
import { motion } from 'framer-motion'
import {
  Activity,
  Bot,
  Brain,
  Headphones,
  Home,
  LogOut,
  ScanSearch,
  Settings,
  Swords,
  Trophy,
} from 'lucide-react'
import { MascotReaction, StickerBadge, VibePill } from '../../components'
import {
  GlowText,
  HudFrame,
  MagneticButton,
  NeonChart,
  NeuralParticles,
  StatCard,
  TiltCard,
} from '../../components/cyber'
import { useAuth } from '../auth'
import { useStreak } from './useStreak'
import { useVibe } from './useVibe'
import type { UiVibeType } from './useVibe'

type Page =
  | 'home'
  | 'sandbox'
  | 'copilot'
  | 'wrapped'
  | 'score'
  | 'reviewUpload'
  | 'reviewResult'
  | 'weakness'

const ALL_VIBES: UiVibeType[] = ['燃爆', '想躺平', '莫名烦', '雄心勃勃', '佛系']

// 24-bar sparkline placeholders. Will swap to live signals once a
// per-day usage histogram lands on the API. Until then, the
// PLACEHOLDER_TAG label above each stat surfaces them as sample data
// so judges scanning the demo don't mistake them for live signals.
const SPARK_SESSIONS = [
  0.2, 0.3, 0.25, 0.4, 0.35, 0.5, 0.45, 0.6, 0.55, 0.65, 0.7, 0.6, 0.75, 0.7, 0.85, 0.8, 0.9, 0.85,
  1.0, 0.92, 0.88, 0.95, 0.9, 0.98,
]
const ACTIVITY_LINE = [0.2, 0.35, 0.28, 0.5, 0.45, 0.62, 0.55, 0.72, 0.68, 0.85, 0.78, 0.92]
const PLACEHOLDER_TAG = '示例'

interface SidebarItem {
  key: Page
  label: string
  icon: typeof Home
}

const SIDEBAR: SidebarItem[] = [
  { key: 'home', label: 'HOME', icon: Home },
  { key: 'sandbox', label: 'SANDBOX', icon: Swords },
  { key: 'copilot', label: 'COPILOT', icon: Headphones },
  { key: 'reviewUpload', label: 'REVIEW', icon: ScanSearch },
  { key: 'weakness', label: 'WEAKNESS', icon: Brain },
  { key: 'wrapped', label: 'WRAPPED', icon: Trophy },
]

export function HomePage({ onNavigate }: { onNavigate: (page: Page) => void }) {
  const { user, logout } = useAuth()
  const isMinor = user?.is_minor ?? false
  const { state: streakState, refetch: refetchStreak } = useStreak()
  const { state: vibeState, setVibe } = useVibe()

  useEffect(() => {
    if (streakState.currentDays === 0 && !streakState.isLoading && !streakState.error) {
      void refetchStreak()
    }
  }, [streakState.currentDays, streakState.isLoading, streakState.error, refetchStreak])

  return (
    <div className="relative min-h-screen overflow-hidden bg-cyber-void text-white">
      {/* ── R3F neural background ─────────────────────────────────── */}
      <NeuralParticles />

      {/* Subtle scanline + grid overlay to set the "OS shell" feel */}
      <div className="pointer-events-none fixed inset-0 -z-10 tech-grid opacity-30" />
      <div className="pointer-events-none fixed inset-0 -z-10 scanline" />

      {/* Radial dim mask so content reads against the busy background */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 -z-10"
        style={{
          background:
            'radial-gradient(ellipse at center, transparent 0%, rgba(5,5,5,0.4) 60%, rgba(5,5,5,0.85) 100%)',
        }}
      />

      {/* ── Top status bar ──────────────────────────────────────── */}
      <header className="relative z-20 mx-auto flex max-w-[1600px] items-center justify-between px-8 pt-6">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-2 w-2 rounded-full bg-cyber-lime shadow-[0_0_12px_#B7FF00]" />
          <span className="font-bebas text-sm tracking-[0.28em] text-white/80">
            CAREERCOACH · AI CORE · ONLINE
          </span>
        </div>
        <div className="flex items-center gap-5">
          <span className="font-mono text-xs text-white/50">
            SYS://{user?.nickname ?? 'GUEST'}
          </span>
          <button
            type="button"
            onClick={logout}
            className="inline-flex items-center gap-1 font-bebas text-xs tracking-[0.2em] text-white/60 transition-colors hover:text-cyber-magenta"
          >
            <LogOut className="h-3.5 w-3.5" />
            LOGOUT
          </button>
        </div>
      </header>

      {/* ── Layout: sidebar + main column ─────────────────────────── */}
      <div className="relative z-10 mx-auto flex max-w-[1600px] px-8 pt-8 pb-16">
        {/* Left floating sidebar */}
        <aside className="sticky top-8 mr-8 flex h-[calc(100vh-6rem)] w-16 flex-col items-center gap-2 self-start rounded-3xl border border-cyber-hairline bg-cyber-deep/60 py-6 backdrop-blur-xl">
          <span className="mb-3 font-orbitron text-[10px] font-bold tracking-[0.2em] text-cyber-cyan">
            K
          </span>
          {SIDEBAR.map(item => {
            const Icon = item.icon
            const isActive = item.key === 'home'
            const disabled = item.key === 'copilot' && isMinor
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => !disabled && onNavigate(item.key)}
                disabled={disabled}
                title={disabled ? 'PRD §1.5 · 18+ 解锁' : item.label}
                className={`group relative flex h-11 w-11 items-center justify-center rounded-xl transition-all ${
                  isActive
                    ? 'bg-gradient-to-br from-cyber-cyan/30 to-cyber-magenta/20 text-cyber-cyan shadow-[0_0_20px_rgba(0,240,255,0.4)]'
                    : 'text-white/40 hover:bg-white/5 hover:text-cyber-cyan'
                } ${disabled ? 'cursor-not-allowed opacity-30' : ''}`}
              >
                <Icon className="h-5 w-5" />
                {isActive && (
                  <span className="absolute -right-1 top-1/2 h-6 w-1 -translate-y-1/2 rounded-l bg-cyber-cyan shadow-[0_0_8px_#00F0FF]" />
                )}
              </button>
            )
          })}
          <span className="mt-auto flex h-11 w-11 items-center justify-center text-white/40 hover:text-white/80">
            <Settings className="h-5 w-5" />
          </span>
        </aside>

        {/* Main column */}
        <main className="flex-1 space-y-10">
          {/* ── Hero ────────────────────────────────────────────── */}
          <section className="grid grid-cols-12 gap-6">
            {/* Left 7 cols — massive layered typography */}
            <div className="col-span-12 flex flex-col gap-6 lg:col-span-7">
              <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5 }}
                className="flex items-center gap-3"
              >
                <Bot className="h-4 w-4 text-cyber-cyan" />
                <span className="font-bebas text-xs tracking-[0.32em] text-cyber-cyan/90">
                  NEURAL · DIALOGUE · COACH
                </span>
                <span className="font-mono text-[10px] text-white/40">v0.1.0</span>
              </motion.div>

              {/* Layered massive type — stroke + fill stacked */}
              <div className="relative leading-none">
                <motion.div
                  initial={{ opacity: 0, x: -32 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.7, delay: 0.1 }}
                  className="font-orbitron text-[88px] font-black uppercase leading-[0.85] tracking-tighter md:text-[120px]"
                >
                  <GlowText variant="stroke" color="#00F0FF">
                    FUTURE
                  </GlowText>
                </motion.div>
                <motion.div
                  initial={{ opacity: 0, x: 32 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.7, delay: 0.25 }}
                  className="font-orbitron -mt-4 text-[88px] font-black uppercase leading-[0.85] tracking-tighter md:text-[120px]"
                >
                  <GlowText variant="gradient">SYSTEM</GlowText>
                </motion.div>
              </div>

              <motion.h1
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.6, delay: 0.4 }}
                className="font-display text-4xl italic tracking-tight text-white md:text-5xl"
              >
                不教你说违心话，<span className="text-cyber-magenta">只教你说真话还能赢</span>。
              </motion.h1>

              <motion.p
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.6, delay: 0.55 }}
                className="max-w-xl text-base text-white/60"
              >
                沙盘对手实时过招 · 耳机里 AI 副驾给提示 · 聊天截图被 AI 打分。
                教练 K 在线，准备好接你下一个回合了。
              </motion.p>

              <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.7 }}
                className="mt-4 flex flex-wrap items-center gap-5"
              >
                <MagneticButton onClick={() => onNavigate('sandbox')}>
                  <Swords className="h-4 w-4" />
                  ENTER SANDBOX
                </MagneticButton>
                <button
                  type="button"
                  onClick={() => onNavigate('reviewUpload')}
                  className="font-bebas text-sm tracking-[0.2em] text-white/70 underline-offset-4 transition-colors hover:text-cyber-cyan hover:underline"
                >
                  UPLOAD CHAT → REVIEW
                </button>
              </motion.div>
            </div>

            {/* Right 5 cols — K profile card stacked over stat strip */}
            <div className="col-span-12 flex flex-col gap-4 lg:col-span-5">
              <HudFrame
                label="CHARACTER · 教练 K"
                tag="LV.05"
                className="cyber-glass-edge rounded-3xl p-6"
              >
                <div className="flex flex-col items-center gap-4">
                  <motion.div
                    initial={{ scale: 0.3, rotate: -10, opacity: 0 }}
                    animate={{ scale: 1, rotate: 0, opacity: 1 }}
                    transition={{ type: 'spring', stiffness: 280, damping: 18, delay: 0.3 }}
                  >
                    <MascotReaction expression="confident" size="md" />
                  </motion.div>
                  <div className="grid w-full grid-cols-3 gap-2 text-center">
                    <div>
                      <p className="font-bebas text-[10px] tracking-widest text-white/50">气场</p>
                      <p className="font-orbitron text-lg font-bold text-cyber-cyan">7.2</p>
                    </div>
                    <div>
                      <p className="font-bebas text-[10px] tracking-widest text-white/50">嘴硬度</p>
                      <p className="font-orbitron text-lg font-bold text-cyber-magenta">8.1</p>
                    </div>
                    <div>
                      <p className="font-bebas text-[10px] tracking-widest text-white/50">共情力</p>
                      <p className="font-orbitron text-lg font-bold text-cyber-lime">6.5</p>
                    </div>
                  </div>
                  <p className="text-center font-mono text-[10px] text-white/40">
                    {PLACEHOLDER_TAG} · 练完第一局生成你的画像
                  </p>
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    <StickerBadge variant="cyan">🐶 稳如老狗</StickerBadge>
                    <StickerBadge variant="orange">🔥 正面刚</StickerBadge>
                    <StickerBadge variant="yellow">🤡 整活儿</StickerBadge>
                  </div>
                </div>
              </HudFrame>

              <div className="grid grid-cols-2 gap-3">
                <StatCard
                  label={`SESSIONS · ${PLACEHOLDER_TAG}`}
                  value="76"
                  suffix="+12%"
                  spark={SPARK_SESSIONS}
                  tone="cyan"
                />
                <StatCard
                  label="STREAK"
                  value={`${streakState.currentDays}d`}
                  suffix="🔥"
                  tone="magenta"
                />
              </div>

              <HudFrame
                label="WEEKLY · ACTIVITY"
                tag={PLACEHOLDER_TAG}
                className="cyber-glass-edge rounded-3xl p-5"
              >
                <NeonChart data={ACTIVITY_LINE} color="#00F0FF" height={64} />
                <div className="mt-2 flex justify-between font-mono text-[10px] text-white/40">
                  <span>MON</span>
                  <span>WED</span>
                  <span>FRI</span>
                  <span>SUN</span>
                </div>
              </HudFrame>
            </div>
          </section>

          {/* ── Mode trio ───────────────────────────────────────── */}
          <section>
            <div className="mb-6 flex items-baseline justify-between">
              <div className="flex items-center gap-3">
                <Activity className="h-4 w-4 text-cyber-magenta" />
                <span className="font-bebas text-xs tracking-[0.32em] text-cyber-magenta">
                  CORE MODES · 03
                </span>
              </div>
              <span className="font-mono text-[10px] text-white/40">
                K_HINT://"今天先选一个练" ⚡
              </span>
            </div>
            <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
              <ModeCard
                onClick={() => onNavigate('sandbox')}
                index="01"
                title="SANDBOX"
                titleZh="沙盘对练"
                body="跟 AI 扮演的对手过招，教练 K 实时打回合分。"
                accent="#00F0FF"
                icon={<Swords className="h-6 w-6" />}
              />
              <ModeCard
                onClick={() => (isMinor ? undefined : onNavigate('copilot'))}
                index="02"
                title="COPILOT"
                titleZh="实战副驾"
                body={isMinor ? '未成年不开放（PRD §1.5）' : '真实对话耳机给提示，关键时刻别怂。'}
                accent={isMinor ? '#5E5B7A' : '#FF2DAA'}
                icon={<Headphones className="h-6 w-6" />}
                disabled={isMinor}
              />
              <ModeCard
                onClick={() => onNavigate('reviewUpload')}
                index="03"
                title="REVIEW"
                titleZh="复盘师"
                body="聊天截图上传，AI 标出每一句的得失分。"
                accent="#B7FF00"
                icon={<ScanSearch className="h-6 w-6" />}
              />
            </div>
          </section>

          {/* ── Vibe HUD strip ──────────────────────────────────── */}
          <section className="cyber-glass-edge rounded-3xl p-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="font-bebas text-[11px] tracking-[0.28em] text-cyber-cyan">
                  TODAY · VIBE CHECK
                </p>
                <p className="mt-1 font-display text-2xl italic text-white">
                  今天什么 vibe？K 按这股劲调话术。
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {ALL_VIBES.map(v => (
                  <VibePill
                    key={v}
                    vibe={v}
                    onClick={() => void setVibe(v)}
                    className={
                      vibeState.activeVibe === v
                        ? 'ring-2 ring-cyber-cyan shadow-[0_0_20px_rgba(0,240,255,0.5)]'
                        : ''
                    }
                  />
                ))}
              </div>
            </div>
          </section>

          {/* ── Footer ticker ───────────────────────────────────── */}
          <footer className="flex items-center justify-between border-t border-cyber-hairline pt-6 font-mono text-[11px] text-white/40">
            <span>SYS / K-CORE / VIVID-COACH ENGINE v0.1</span>
            <span className="text-cyber-cyan/70">"卧槽你这波操作我都没想到" — K</span>
            <span>NEURAL-NET // {streakState.currentDays} DAYS STREAK</span>
          </footer>
        </main>
      </div>
    </div>
  )
}

/* ── ModeCard ────────────────────────────────────────────────────────
 * Inline because it's only used inside HomePage's three-up mode strip
 * and needs the TiltCard + HudFrame combo to feel premium. Moved out
 * once a second consumer appears (sandbox scenario picker is a likely
 * future caller).
 * ────────────────────────────────────────────────────────────────── */
interface ModeCardProps {
  index: string
  title: string
  titleZh: string
  body: string
  accent: string
  icon: React.ReactNode
  onClick?: () => void
  disabled?: boolean
}

function ModeCard({ index, title, titleZh, body, accent, icon, onClick, disabled }: ModeCardProps) {
  return (
    <TiltCard className={`rounded-3xl ${disabled ? 'opacity-50' : ''}`}>
      <HudFrame label={`MODE · ${index}`} className="cyber-glass-edge rounded-3xl p-6">
        <button
          type="button"
          onClick={onClick}
          disabled={disabled}
          className="flex h-full w-full flex-col items-start gap-4 text-left disabled:cursor-not-allowed"
        >
          <div
            className="flex h-12 w-12 items-center justify-center rounded-2xl border"
            style={{
              borderColor: `${accent}55`,
              background: `radial-gradient(circle at center, ${accent}22 0%, transparent 70%)`,
              color: accent,
              boxShadow: `0 0 24px ${accent}33`,
            }}
          >
            {icon}
          </div>
          <div>
            <p
              className="font-orbitron text-3xl font-black uppercase tracking-tight"
              style={{ color: accent, textShadow: `0 0 14px ${accent}66` }}
            >
              {title}
            </p>
            <p className="mt-1 font-display text-xl italic text-white">{titleZh}</p>
          </div>
          <p className="text-sm text-white/60">{body}</p>
          <span
            className="mt-auto inline-flex items-center gap-2 font-bebas text-xs tracking-[0.24em]"
            style={{ color: accent }}
          >
            {disabled ? 'LOCKED' : 'ENTER →'}
          </span>
        </button>
      </HudFrame>
    </TiltCard>
  )
}
