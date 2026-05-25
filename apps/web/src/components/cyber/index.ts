/**
 * Cyberpunk primitives — adds an "AI operating system" visual layer on
 * top of the existing Vivid Coach design system. Co-exists with the
 * legacy `components/ui/*` (Mascot K stays 2D) per the path decision.
 *
 * Composition rule: page layout stays in `features/*`; this folder
 * exposes only stateless visual primitives that any feature can compose.
 */

export { NeuralParticles } from './NeuralParticles'
export { HudFrame } from './HudFrame'
export { GlowText } from './GlowText'
export { TiltCard } from './TiltCard'
export { MagneticButton } from './MagneticButton'
export { StatCard } from './StatCard'
export { NeonChart } from './NeonChart'
