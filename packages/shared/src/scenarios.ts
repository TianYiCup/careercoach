export type ScenarioCategory =
  | 'campus'
  | 'internship'
  | 'job-hunt'
  | 'family'
  | 'dorm'

export type OpponentRole =
  | 'advisor'
  | 'boss'
  | 'hr'
  | 'roommate'
  | 'parent'

export interface Scenario {
  readonly id: string
  readonly category: ScenarioCategory
  readonly title: string
  readonly opponentRole: OpponentRole
  readonly description: string
  readonly difficulty: 1 | 2 | 3
}
