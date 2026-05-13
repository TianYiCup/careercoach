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

/** 表情 → emoji 占位映射（Rive 资源可用后替换） */
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
