import { clsx } from 'clsx'
import { motion, type Variants } from 'framer-motion'
import { useState } from 'react'
import {
  EXPRESSION_IMAGE,
  EXPRESSION_LABEL,
  type MascotExpression,
  type MascotSize,
} from './types'

/** MascotReaction — 教练 K 表情容器 + 弹簧入场
 *  design-spec §6.3:
 *    入场：scale(0.3) → spring(500, 18) + rotate -10° → 0°
 *    切换表情：crossfade 200ms
 *    点击交互：scale(1.1)
 *
 *  TODO: .riv 文件到位后替换 emoji 占位符为 Rive 组件
 */

const SIZE_MAP: Record<MascotSize, string> = {
  sm: 'w-16 h-16 text-3xl',
  md: 'w-32 h-32 text-6xl',
  lg: 'w-60 h-60 text-8xl',
}

/** framer-motion spring 配置：stiffness=500, damping=18 */
const springEntrance: Variants = {
  hidden: {
    scale: 0.3,
    rotate: -10,
    opacity: 0,
  },
  visible: {
    scale: 1,
    rotate: 0,
    opacity: 1,
    transition: {
      type: 'spring',
      stiffness: 500,
      damping: 18,
    },
  },
}

interface MascotReactionProps {
  /** 当前表情，默认 confident */
  expression?: MascotExpression
  /** 尺寸，默认 md */
  size?: MascotSize
  /** 是否显示表情标签 */
  showLabel?: boolean
  className?: string
}

export function MascotReaction({
  expression = 'confident',
  size = 'md',
  showLabel = false,
  className,
}: MascotReactionProps) {
  const [current, setCurrent] = useState<MascotExpression>(expression)

  return (
    <motion.div
      className={clsx('flex flex-col items-center gap-2', className)}
      variants={springEntrance}
      initial="hidden"
      animate="visible"
    >
      <motion.button
        type="button"
        className={clsx(
          SIZE_MAP[size],
          'flex items-center justify-center overflow-hidden rounded-radius-blob',
          'bg-vivid-purple/20 border-2 border-vivid-purple/40',
          'select-none cursor-pointer',
        )}
        onClick={() => setCurrent(nextExpression(current))}
        whileTap={{ scale: 1.1 }}
        aria-label={`教练 K 表情：${EXPRESSION_LABEL[current]}`}
      >
        <motion.img
          key={current}
          src={EXPRESSION_IMAGE[current]}
          alt=""
          aria-hidden="true"
          draggable={false}
          className="h-full w-full object-contain"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.2 }}
        />
      </motion.button>

      {showLabel && (
        <span className="text-xs text-ink-text-2 font-body">
          {EXPRESSION_LABEL[current]}
        </span>
      )}
    </motion.div>
  )
}

/** 循环切换到下一个表情 */
const EXPRESSIONS: MascotExpression[] = [
  'confident',
  'fired-up',
  'thinking',
  'godlike',
  'crashed',
  'clowning',
  'caring',
  'slacking',
]

function nextExpression(current: MascotExpression): MascotExpression {
  const idx = EXPRESSIONS.indexOf(current)
  return EXPRESSIONS[(idx + 1) % EXPRESSIONS.length] ?? 'confident'
}
