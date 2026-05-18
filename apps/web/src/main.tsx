import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

async function bootstrap() {
  // Enable MSW in development only.
  // Wrap in try/catch so a missing Service Worker file (e.g. after
  // git clone without `pnpm exec msw init public/`) doesn't block
  // the React tree from mounting — the app simply runs without mocks.
  if (import.meta.env.DEV) {
    try {
      const { worker } = await import('./mocks/browser')
      await worker.start({ onUnhandledRequest: 'bypass' })
    } catch (err) {
      console.warn('[MSW] Service Worker registration failed — mocks disabled:', err)
    }
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
