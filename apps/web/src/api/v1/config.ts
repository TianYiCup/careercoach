/**
 * Resolve the backend API base URL.
 *
 * Precedence lets a demo machine point at a remote backend without a
 * fresh build:
 *
 *   1. Runtime override — `localStorage['cc_api_base']`. Set it on the
 *      machine (a settings field, or devtools) and reload/restart the
 *      app; this is what makes ONE built EXE re-pointable at a different
 *      backend without rebuilding.
 *   2. Build-time default — `VITE_API_BASE_URL`, baked at `pnpm build`
 *      (e.g. `VITE_API_BASE_URL=https://demo-api.example.com/v1`).
 *   3. Dev / preview fallback — relative `/v1`, which the Vite dev/preview
 *      proxy forwards to the local backend.
 *
 * REST (`client.ts`) and SSE (`sse.ts`) both resolve through here. The
 * copilot WebSocket URL is NOT set here: the backend returns its own
 * absolute `ws_url` in the create-session response, so pointing this at a
 * remote backend automatically routes the WS there too (that backend's
 * `COPILOT_WS_BASE_URL` decides the host).
 */

export const API_BASE_OVERRIDE_KEY = 'cc_api_base';

function readRuntimeOverride(): string | null {
  try {
    return localStorage.getItem(API_BASE_OVERRIDE_KEY);
  } catch {
    // localStorage can throw in locked-down / non-browser contexts.
    return null;
  }
}

function normalize(url: string): string {
  // Drop trailing slashes so `${base}${path}` never doubles up (paths
  // already start with `/`). Leaves a bare `/v1` untouched.
  return url.trim().replace(/\/+$/, '');
}

export function resolveApiBaseUrl(): string {
  const override = readRuntimeOverride();
  if (override && override.trim()) return normalize(override);

  const envBase = import.meta.env.VITE_API_BASE_URL;
  if (envBase && envBase.trim()) return normalize(envBase);

  return '/v1';
}
