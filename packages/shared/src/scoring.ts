/**
 * CareerCoach AI · 评分与话术类型定义
 * Source: PRD §3 Epic A + design-spec §2.2
 */

/** 三档话术风格 */
export type ToneLevel =
  | 'safe'    // 稳如老狗 🐶
  | 'aggro'   // 正面刚 🔥
  | 'fun'     // 整活儿 🤡

/** 单轮评分层级 */
export type ScoreLevel =
  | 'god'     // 封神 ✨
  | 'mid'     // 路过 🌀
  | 'fail'    // 翻车 💥

/** 教练提示 */
export interface CoachHint {
  readonly tone: ToneLevel
  readonly text: string
  readonly reason: string
}

/** 单轮评分 */
export interface TurnScore {
  readonly level: ScoreLevel
  readonly points: number
  readonly feedback: string
}

/** 整场评分 */
export interface SessionScore {
  readonly totalPoints: number
  readonly level: ScoreLevel
  readonly highlights: readonly string[]
  readonly weakPoints: readonly string[]
  readonly improvementTips: readonly string[]
}
