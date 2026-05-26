import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig(({ mode }) => {
  // VITE_API_PROXY_TARGET lets the demo / staging stack point the dev
  // proxy at a non-default backend (e.g. docker-host:8000). Defaults to
  // the local uvicorn so `pnpm dev` works out of the box.
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      // Forward /v1 + /health to the backend so the browser only ever
      // sees a single origin. Demo deploys expose just vite via one
      // Cloudflare Tunnel — backend stays local, no CORS preflight,
      // no second tunnel to keep alive. `ws: true` keeps copilot
      // WebSocket sessions working through the same proxy.
      proxy: {
        '/v1': {
          target: proxyTarget,
          changeOrigin: true,
          ws: true,
        },
        '/health': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      // Same proxy rules for `vite preview` so the production build can
      // be served identically to the dev server (used by demo scripts).
      proxy: {
        '/v1': { target: proxyTarget, changeOrigin: true, ws: true },
        '/health': { target: proxyTarget, changeOrigin: true },
      },
    },
  }
})
