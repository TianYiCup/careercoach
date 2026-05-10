export type ToneLevel =
  | 'safe'
  | 'aggro'
  | 'fun'

export type ScoreLevel =
  | 'god'
  | 'mid'
  | 'fail'

export interface CoachHint {
  readonly tone: ToneLevel
  readonly text: string
  readonly reason: string
}

export interface TurnScore {
  readonly level: ScoreLevel
  readonly points: number
  readonly feedback: string
}

export interface SessionScore {
  readonly totalPoints: number
  readonly level: ScoreLevel
  readonly highlights: readonly string[]
  readonly weakPoints: readonly string[]
  readonly improvementTips: readonly string[]
}
