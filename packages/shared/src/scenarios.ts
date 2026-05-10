/**
 * CareerCoach AI · 场景相关类型定义
 * Source: PRD §3 Epic A + §6 Data Model
 */

/** 场景大类 */
export type ScenarioCategory =
  | 'campus'      // 校园
  | 'internship'  // 实习
  | 'job-hunt'    // 求职
  | 'family'      // 家庭
  | 'dorm'        // 宿舍

/** 对手角色 */
export type OpponentRole =
  | 'advisor'     // 导师
  | 'boss'        // 老板
  | 'hr'          // HR
  | 'roommate'    // 室友
  | 'parent'      // 父母

/** 场景摘要 */
export interface Scenario {
  readonly id: string
  readonly category: ScenarioCategory
  readonly title: string
  readonly opponentRole: OpponentRole
  readonly description: string
  readonly difficulty: 1 | 2 | 3
}
