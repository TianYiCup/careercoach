import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// Split the heavy, independently-cacheable vendors out of the single
// app bundle: three.js (3D cyber scenes) and recharts/d3 (Wrapped +
// profile charts) are large and change rarely, so isolating them lets
// the browser cache them across app deploys and download chunks in
// parallel instead of one 1.6MB blob.
function vendorChunk(id: string): string | undefined {
  if (!id.includes('node_modules')) return undefined
  if (id.includes('node_modules/three/') || id.includes('@react-three')) return 'three'
  if (id.includes('recharts') || id.includes('victory-vendor') || id.includes('/d3-')) {
    return 'charts'
  }
  if (id.includes('framer-motion')) return 'motion'
  return 'vendor'
}

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
    build: {
      // three.js is ~880KB but now lives in its own lazy chunk (loaded
      // by NeuralParticlesLazy, off the initial critical path), so the
      // default 500KB warning is just noise for that one known chunk.
      chunkSizeWarningLimit: 900,
      rollupOptions: {
        output: {
          manualChunks: vendorChunk,
        },
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
