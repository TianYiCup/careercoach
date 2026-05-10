/**
 * CareerCoach AI · 跨端共享包入口
 *
 * 三不原则：
 * - 不塞 Web/小程序专属代码（端无关）
 * - 不引入 React 或前端框架（纯逻辑）
 * - 不写 Node-only 代码（小程序跑不了）
 */

export type { ScenarioCategory, OpponentRole, Scenario } from './scenarios'
export type { ToneLevel, ScoreLevel, CoachHint, TurnScore, SessionScore } from './scoring'
export type { MascotExpression, MascotAssetType } from './mascot'
export type { RedLineCategory, AppMode, ModerationResult } from './constants'

export { RED_LINE_CATEGORIES, APP_MODES } from './constants'
