import kCaring from './k_caring.jpg'
import kClowning from './k_clowning.jpg'
import kConfident from './k_confident.png'
import kCrashed from './k_crashed.jpg'
import kFiredUp from './k_fired-up.png'
import kGodlike from './k_godlike.jpg'
import kSlacking from './k_slacking.jpg'
import kThinking from './k_thinking.jpg'

/** Mascot 表情类型 — 对应 design-spec §3.3 八种表情 */
export type MascotExpression =
  | 'confident'
  | 'fired-up'
  | 'thinking'
  | 'godlike'
  | 'crashed'
  | 'clowning'
  | 'caring'
  | 'slacking'

/** 表情 → 教练 K 静态图（design-spec §3.3）。MascotReaction 实际渲染用这套。 */
export const EXPRESSION_IMAGE: Record<MascotExpression, string> = {
  confident: kConfident,
  'fired-up': kFiredUp,
  thinking: kThinking,
  godlike: kGodlike,
  crashed: kCrashed,
  clowning: kClowning,
  caring: kCaring,
  slacking: kSlacking,
}

/** 表情 → emoji 兜底（图片未加载时的语义占位，仍用于 aria / 极端兜底） */
export const EXPRESSION_EMOJI: Record<MascotExpression, string> = {
  confident: '😎',
  'fired-up': '🔥',
  thinking: '🤔',
  godlike: '✨',
  crashed: '😅',
  clowning: '🤡',
  caring: '🥹',
  slacking: '💤',
}

/** 表情 → 中文标签 */
export const EXPRESSION_LABEL: Record<MascotExpression, string> = {
  confident: '自信',
  'fired-up': '热血',
  thinking: '思考',
  godlike: '封神',
  crashed: '翻车',
  clowning: '整活',
  caring: '心疼',
  slacking: '摆烂',
}

/** Mascot 尺寸规格 — design-spec §6.3 */
export type MascotSize = 'sm' | 'md' | 'lg'
