/**
 * NeuralParticles — full-bleed R3F neural-network background.
 *
 * Renders a 3D cloud of additively-blended particles connected to their
 * nearest neighbours by glowing lines. Camera lerps toward the cursor
 * so the whole field parallaxes on mouse-move. The visual target is the
 * "Neurones" reference image — a soft, deep-space brain actively
 * processing information.
 *
 * Performance notes:
 *   · Particles: ~1800. Each is one point in a single Points geometry
 *     (one draw call). Increasing past ~3000 starts costing FPS on
 *     integrated GPUs — keep this constant unless we audit.
 *   · Connections: regenerated each frame from a precomputed neighbour
 *     list. We pick fixed pairs at startup (no per-frame kNN search) to
 *     keep the inner loop allocation-free.
 *   · Postprocessing bloom would be ideal but requires
 *     `@react-three/postprocessing`. We get a softer cheap glow by
 *     using a radial sprite texture + AdditiveBlending. Worth upgrading
 *     to true UnrealBloom once we audit bundle impact.
 */

import { Canvas, useFrame } from '@react-three/fiber'
import { useMemo, useRef } from 'react'
import * as THREE from 'three'

interface NeuralParticlesProps {
  /** Particle count. Production-tuned default is 1800. */
  count?: number
  /** Sphere radius the particles fill. */
  radius?: number
  /** Number of neighbour connections per particle (one direction). */
  connectionsPerParticle?: number
  /** Max distance for a connection to be drawn (in world units). */
  connectionMaxDistance?: number
  /** Optional className to position the Canvas wrapper. */
  className?: string
}

/* Single soft-circle sprite as a glow texture — generated in JS so we
 * don't ship a binary PNG. 64×64 alpha mask, radial falloff. */
function makeGlowTexture(): THREE.Texture {
  const size = 64
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
  gradient.addColorStop(0, 'rgba(255,255,255,1)')
  gradient.addColorStop(0.4, 'rgba(255,255,255,0.4)')
  gradient.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, size, size)
  const texture = new THREE.CanvasTexture(canvas)
  texture.needsUpdate = true
  return texture
}

interface ParticleField {
  positions: Float32Array
  colors: Float32Array
  connectionIndices: Uint16Array
}

/* Pre-compute particle positions / per-particle colours / fixed neighbour
 * connection list. Doing this once at mount keeps the render loop pure. */
function buildField(
  count: number,
  radius: number,
  connectionsPerParticle: number,
  maxDistance: number,
): ParticleField {
  const positions = new Float32Array(count * 3)
  const colors = new Float32Array(count * 3)

  // Vivid Coach / cyberpunk palette — cyan, blue, purple, magenta, lime.
  const palette = [
    new THREE.Color('#00F0FF'),
    new THREE.Color('#4F8BFF'),
    new THREE.Color('#6C4DFF'),
    new THREE.Color('#FF2DAA'),
    new THREE.Color('#B7FF00'),
  ]

  for (let i = 0; i < count; i++) {
    // Uniform-sphere sampling via three random angles + cube-root radius
    // — gives a dense centre, sparse edges (matches "Neurones" feel).
    const u = Math.random()
    const v = Math.random()
    const theta = 2 * Math.PI * u
    const phi = Math.acos(2 * v - 1)
    const r = radius * Math.cbrt(Math.random())

    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta)
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta)
    positions[i * 3 + 2] = r * Math.cos(phi)

    const color = palette[Math.floor(Math.random() * palette.length)]!
    colors[i * 3] = color.r
    colors[i * 3 + 1] = color.g
    colors[i * 3 + 2] = color.b
  }

  // For each particle, find connectionsPerParticle nearest within
  // maxDistance. O(n²) sweep is fine at n=1800 since we only do it once.
  const indices: number[] = []
  for (let i = 0; i < count; i++) {
    const candidates: { idx: number; dist: number }[] = []
    for (let j = 0; j < count; j++) {
      if (i === j) {
        continue
      }
      const dx = positions[i * 3]! - positions[j * 3]!
      const dy = positions[i * 3 + 1]! - positions[j * 3 + 1]!
      const dz = positions[i * 3 + 2]! - positions[j * 3 + 2]!
      const d2 = dx * dx + dy * dy + dz * dz
      if (d2 < maxDistance * maxDistance) {
        candidates.push({ idx: j, dist: d2 })
      }
    }
    candidates.sort((a, b) => a.dist - b.dist)
    candidates.slice(0, connectionsPerParticle).forEach(c => {
      // Avoid duplicate i<->j edges — only push when j > i.
      if (c.idx > i) {
        indices.push(i, c.idx)
      }
    })
  }

  return {
    positions,
    colors,
    connectionIndices: new Uint16Array(indices),
  }
}

interface FieldProps {
  field: ParticleField
  glowTexture: THREE.Texture
}

function ParticleFieldMesh({ field, glowTexture }: FieldProps) {
  const groupRef = useRef<THREE.Group>(null)
  const linePositions = useMemo(() => {
    const out = new Float32Array(field.connectionIndices.length * 3)
    for (let k = 0; k < field.connectionIndices.length; k++) {
      const idx = field.connectionIndices[k]!
      out[k * 3] = field.positions[idx * 3]!
      out[k * 3 + 1] = field.positions[idx * 3 + 1]!
      out[k * 3 + 2] = field.positions[idx * 3 + 2]!
    }
    return out
  }, [field])

  useFrame((state, delta) => {
    if (!groupRef.current) {
      return
    }
    // Slow auto-rotation — "an AI brain actively processing".
    groupRef.current.rotation.y += delta * 0.05
    groupRef.current.rotation.x += delta * 0.02

    // Mouse parallax — camera leans toward cursor.
    const targetX = state.pointer.x * 0.6
    const targetY = state.pointer.y * 0.4
    state.camera.position.x += (targetX - state.camera.position.x) * 0.04
    state.camera.position.y += (targetY - state.camera.position.y) * 0.04
    state.camera.lookAt(0, 0, 0)
  })

  return (
    <group ref={groupRef}>
      <points>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            args={[field.positions, 3]}
          />
          <bufferAttribute
            attach="attributes-color"
            args={[field.colors, 3]}
          />
        </bufferGeometry>
        <pointsMaterial
          size={0.18}
          vertexColors
          transparent
          opacity={0.95}
          map={glowTexture}
          alphaTest={0.01}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          sizeAttenuation
        />
      </points>
      <lineSegments>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            args={[linePositions, 3]}
          />
        </bufferGeometry>
        <lineBasicMaterial
          color="#4F8BFF"
          transparent
          opacity={0.18}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </lineSegments>
    </group>
  )
}

export function NeuralParticles({
  count = 1800,
  radius = 6,
  connectionsPerParticle = 2,
  connectionMaxDistance = 1.6,
  className = '',
}: NeuralParticlesProps) {
  const field = useMemo(
    () => buildField(count, radius, connectionsPerParticle, connectionMaxDistance),
    [count, radius, connectionsPerParticle, connectionMaxDistance],
  )
  const glowTexture = useMemo(() => makeGlowTexture(), [])

  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute inset-0 -z-10 ${className}`.trim()}
    >
      <Canvas
        camera={{ position: [0, 0, 12], fov: 60 }}
        gl={{ antialias: false, alpha: true, powerPreference: 'high-performance' }}
        dpr={[1, 1.5]}
      >
        <color attach="background" args={['#050505']} />
        <fog attach="fog" args={['#050505', 8, 18]} />
        <ParticleFieldMesh field={field} glowTexture={glowTexture} />
      </Canvas>
    </div>
  )
}
