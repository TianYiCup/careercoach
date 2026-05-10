/**
 * CareerCoach AI · 常量定义
 * Source: PRD + design-spec
 */

/** 红线类别（六大不可逾越） */
export const RED_LINE_CATEGORIES = [
  'self-harm',          // 自残
  'school-violence',    // 校园暴力
  'predatory-lending',  // 网贷
  'sexual-harassment',  // 性骚扰
  'political',          // 涉政
  'communication-harm', // 沟通伤害
] as const

export type RedLineCategory = (typeof RED_LINE_CATEGORIES)[number]

/** 三大核心模式 */
export const APP_MODES = ['sandbox', 'copilot', 'review'] as const
export type AppMode = (typeof APP_MODES)[number]

/** 内容审核结果 */
export type ModerationResult =
  | { passed: true }
  | { passed: false; reason: RedLineCategory; details: string }
