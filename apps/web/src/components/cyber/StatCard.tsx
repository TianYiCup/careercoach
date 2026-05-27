/**
 * StatCard — single analytics tile for the dashboard sidebar.
 *
 * Mirrors the "76k · 1.5m · $3.6k · 47" stat strip from the Channel
 * Analytics reference image: tiny mono label up top, big numeric below,
 * an optional 24-bar mini sparkline, and an underline accent in the
 * stat's tone colour.
 *
 * Stays presentation-only — caller provides label / value / spark data.
 */

import type { ReactNode } from 'react'

type Tone = 'cyan' | 'magenta' | 'lime' | 'amber' | 'purple'

interface StatCardProps {
  label: string
  value: string | number
  /** Optional trailing icon / unit (e.g. "%", arrow). */
  suffix?: ReactNode
  /** 24-bar sparkline values (normalised 0..1). */
  spark?: number[]
  tone?: Tone
  className?: string
}

const TONE: Record<Tone, { bar: string; under: string; text: string }> = {
  cyan: { bar: 'bg-cyber-cyan', under: 'bg-cyber-cyan', text: 'text-cyber-cyan' },
  magenta: { bar: 'bg-cyber-magenta', under: 'bg-cyber-magenta', text: 'text-cyber-magenta' },
  lime: { bar: 'bg-cyber-lime', under: 'bg-cyber-lime', text: 'text-cyber-lime' },
  amber: { bar: 'bg-cyber-amber', under: 'bg-cyber-amber', text: 'text-cyber-amber' },
  purple: { bar: 'bg-vivid-purple', under: 'bg-vivid-purple', text: 'text-vivid-purple-soft' },
}

export function StatCard({
  label,
  value,
  suffix,
  spark,
  tone = 'cyan',
  className = '',
}: StatCardProps) {
  const palette = TONE[tone]

  return (
    <div
      className={`relative overflow-hidden rounded-2xl border border-cyber-hairline bg-cyber-deep/60 p-4 backdrop-blur-xl ${className}`.trim()}
    >
      <div className="flex items-baseline justify-between">
        <span className="font-bebas text-[11px] tracking-[0.28em] text-white/60 uppercase">
          {label}
        </span>
        {suffix && <span className={`font-mono text-xs ${palette.text}`}>{suffix}</span>}
      </div>

      <div className="mt-2 flex items-baseline gap-2">
        <span className="font-orbitron text-3xl font-bold text-white">{value}</span>
      </div>

      {spark && spark.length > 0 && (
        <div className="mt-3 flex h-8 items-end gap-[2px]">
          {spark.map((v, i) => (
            <span
              key={i}
              className={`flex-1 rounded-sm ${palette.bar} opacity-70 transition-all`}
              style={{ height: `${Math.max(8, v * 100)}%` }}
            />
          ))}
        </div>
      )}

      <span className={`absolute bottom-0 left-0 h-[2px] w-full ${palette.under} opacity-50`} />
    </div>
  )
}
