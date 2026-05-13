import { clsx } from 'clsx'

/** HintCardV2 — 三档话术卡
 *  design-spec §6.2 / §9.3:
 *    当前档高亮 + 对应 glow
 *    三档：稳如老狗🐶 / 正面刚🔥 / 整活儿🤡
 */
export type ToneLevel = 'safe' | 'aggro' | 'fun'

interface HintCardV2Props {
  /** 教练提示内容 */
  hint: string
  /** 当前选中档位 */
  activeTone: ToneLevel
  /** 档位切换回调 */
  onToneChange: (tone: ToneLevel) => void
  className?: string
}

const TONE_CONFIG: Record<ToneLevel, { label: string; emoji: string; color: string; glow: string }> = {
  safe: { label: '稳', emoji: '🐶', color: 'text-tone-safe', glow: 'shadow-[0_0_16px_rgba(60,255,232,0.3)]' },
  aggro: { label: '刚', emoji: '🔥', color: 'text-tone-aggro', glow: 'shadow-[0_0_16px_rgba(255,107,53,0.3)]' },
  fun: { label: '活', emoji: '🤡', color: 'text-tone-fun', glow: 'shadow-[0_0_16px_rgba(255,233,77,0.3)]' },
}

export function HintCardV2({ hint, activeTone, onToneChange, className }: HintCardV2Props) {
  const tones: ToneLevel[] = ['safe', 'aggro', 'fun']

  return (
    <div className={clsx('glass rounded-radius-md p-4', className)}>
      {/* 教练 K 标题 */}
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm" role="img" aria-label="思考">🤔</span>
        <span className="text-sm text-ink-text-2 font-body">教练 K 来了</span>
      </div>

      {/* 提示内容 */}
      <p className="text-base text-ink-text font-body mb-4">
        {hint}
      </p>

      {/* 三档按钮 */}
      <div className="flex gap-2">
        {tones.map((tone) => {
          const cfg = TONE_CONFIG[tone]
          const isActive = tone === activeTone
          return (
            <button
              key={tone}
              type="button"
              onClick={() => onToneChange(tone)}
              className={clsx(
                'flex-1 py-2 rounded-radius-pill text-sm font-bold border transition-all',
                isActive
                  ? `${cfg.color} border-current ${cfg.glow}`
                  : 'text-ink-text-3 border-ink-line hover:border-ink-text-3',
              )}
            >
              <span role="img" aria-label={cfg.label}>{cfg.emoji}</span>{cfg.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
