import type { MascotExpression } from '../../components/mascot/types'

/** Narrow MascotExpression to the 3 expressions that make sense on the score page */
export function toScoreExpression(expr: MascotExpression): 'godlike' | 'crashed' | 'confident' {
  if (expr === 'godlike' || expr === 'crashed' || expr === 'confident') return expr
  return 'confident'
}
