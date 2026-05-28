import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

async function bootstrap() {
  // PR-D6: MSW is opt-in via VITE_USE_MSW=true. Default-off so
  // `pnpm dev` hits the real FastAPI backend through the vite proxy —
  // otherwise every UI test runs against the canned `mocks/handlers`
  // fixtures and we never see prompt drift, scenario lookup misses,
  // or empty weakness profiles until prod.
  if (import.meta.env.VITE_USE_MSW === 'true') {
    try {
      const { worker } = await import('./mocks/browser')
      await worker.start({ onUnhandledRequest: 'bypass' })
    } catch (err) {
      console.warn('[MSW] Service Worker registration failed — mocks disabled:', err)
    }
  } else if ('serviceWorker' in navigator) {
    // A previously-registered MSW worker survives a hot reload even
    // after we flip the flag, because the SW lives in the browser not
    // the bundle. Unregister proactively so the page stops intercepting
    // /v1 requests on the very next refresh. Without this, judges who
    // visited an older build keep getting MSW responses indefinitely.
    const regs = await navigator.serviceWorker.getRegistrations()
    await Promise.all(
      regs
        .filter((r) => r.active?.scriptURL.includes('mockServiceWorker'))
        .map((r) => r.unregister()),
    )
  }

  const rootEl = document.getElementById('root')
  if (!rootEl) throw new Error('Root element not found')

  createRoot(rootEl).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

bootstrap()
