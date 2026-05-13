/**
 * wxapp API endpoint configuration.
 *
 * Priority:
 *   1. `process.env.TARO_APP_API_BASE` — set via `config/dev.ts` /
 *      `config/prod.ts` defineConstants. Preferred for CI / staging.
 *   2. Fallback constants below — only meaningful once domains are
 *      ICP 备案-cleared and registered in the WeChat console
 *      "服务器域名" whitelist (CLAUDE.md #14).
 *
 * TODO(sprint-1): replace fallback domains with the real ones after
 * ICP 备案 lands. Until then, real-device smoke testing requires the
 * "不校验合法域名" toggle in the WeChat DevTools.
 */

const ENV_BASE = process.env.TARO_APP_API_BASE as string | undefined

const FALLBACK_BASE =
  process.env.NODE_ENV === 'production'
    ? 'https://api.careercoach.ai' // ICP 备案 pending
    : 'https://dev-api.careercoach.ai' // ICP 备案 pending

export const API_BASE = ENV_BASE ?? FALLBACK_BASE
