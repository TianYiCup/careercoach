import { lazy, Suspense, type ComponentProps } from 'react'

import type { NeuralParticles as NeuralParticlesImpl } from './NeuralParticles'

/**
 * Lazy boundary for the R3F particle background.
 *
 * `NeuralParticles` pulls in three.js (~880KB) — by far the heaviest
 * dependency in the app — yet it's purely decorative chrome behind every
 * page. Deferring it behind a dynamic import keeps three.js out of the
 * initial critical path: the page shell + content render from the small
 * main chunk first, then the background fades in once three loads. The
 * fallback is `null` because the page is fully usable without it.
 *
 * Re-exported as `NeuralParticles` from `./index`, so the ~11 call sites
 * are unchanged.
 */
const Inner = lazy(() => import('./NeuralParticles').then((m) => ({ default: m.NeuralParticles })))

export function NeuralParticles(props: ComponentProps<typeof NeuralParticlesImpl>) {
  return (
    <Suspense fallback={null}>
      <Inner {...props} />
    </Suspense>
  )
}
