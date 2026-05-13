/** `@careercoach/shared` — cross-end shared types & constants.
 *
 *  This package is intentionally small and only holds types that
 *  genuinely span web/wxapp/EXE. Domain types (scenarios, mascot,
 *  scoring) live next to their owning module to avoid the drift we
 *  saw in Sprint 0: duplicated `MascotExpression` / `ScoreLevel` /
 *  `RED_LINE_CATEGORIES` shapes that disagreed with the API.
 *
 *  Add a type here ONLY when:
 *    - The API schema can't already serve as the source of truth
 *      (e.g. derived OpenAPI types from `apps/api/openapi.yaml`), AND
 *    - At least two of {web, wxapp, EXE} consume the same shape.
 */

export { APP_MODES } from './constants'
export type { AppMode } from './constants'
