/**
 * Tests for MoodGauge + moodPressure (L9.2).
 *
 * The scalar derivation is the load-bearing part — it's what the user
 * reads at a glance — so the pressure math gets the most coverage:
 * monotonic in the three driving dims, clamped, banded correctly.
 *
 * @vitest-environment jsdom
 */

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MoodGauge } from '../MoodGauge'
import { moodPressure } from '../moodPressure'
import type { RadarVector } from '../CharacterRadar'

afterEach(() => {
  cleanup()
})

const NEUTRAL: RadarVector = {
  aggression: 50,
  empathy: 50,
  control: 50,
  honesty: 50,
  stability: 50,
  power_gap: 50,
}

function withDim(base: RadarVector, dim: keyof RadarVector, value: number): RadarVector {
  return { ...base, [dim]: value }
}

describe('moodPressure', () => {
  it('rises with aggression', () => {
    const low = moodPressure(withDim(NEUTRAL, 'aggression', 10))
    const high = moodPressure(withDim(NEUTRAL, 'aggression', 90))
    expect(high).toBeGreaterThan(low)
  })

  it('rises as stability falls (more volatile = more pressure)', () => {
    const calm = moodPressure(withDim(NEUTRAL, 'stability', 90))
    const volatile = moodPressure(withDim(NEUTRAL, 'stability', 10))
    expect(volatile).toBeGreaterThan(calm)
  })

  it('rises with power_gap', () => {
    const peer = moodPressure(withDim(NEUTRAL, 'power_gap', 10))
    const boss = moodPressure(withDim(NEUTRAL, 'power_gap', 90))
    expect(boss).toBeGreaterThan(peer)
  })

  it('ignores empathy / control / honesty (they shape how, not how hard)', () => {
    const base = moodPressure(NEUTRAL)
    expect(moodPressure(withDim(NEUTRAL, 'empathy', 0))).toBe(base)
    expect(moodPressure(withDim(NEUTRAL, 'empathy', 100))).toBe(base)
    expect(moodPressure(withDim(NEUTRAL, 'control', 100))).toBe(base)
    expect(moodPressure(withDim(NEUTRAL, 'honesty', 0))).toBe(base)
  })

  it('stays within 0-100 for extreme inputs', () => {
    const max = moodPressure({
      aggression: 100,
      empathy: 0,
      control: 100,
      honesty: 0,
      stability: 0,
      power_gap: 100,
    })
    const min = moodPressure({
      aggression: 0,
      empathy: 100,
      control: 0,
      honesty: 100,
      stability: 100,
      power_gap: 0,
    })
    expect(max).toBeLessThanOrEqual(100)
    expect(min).toBeGreaterThanOrEqual(0)
    expect(max).toBe(100)
    expect(min).toBe(0)
  })

  it('clamps out-of-range payloads instead of overshooting', () => {
    const wild = moodPressure({
      aggression: 200,
      empathy: 50,
      control: 50,
      honesty: 50,
      stability: -50,
      power_gap: 999,
    })
    expect(wild).toBe(100)
  })
})

describe('MoodGauge component', () => {
  it('renders the pressure number and a band label', () => {
    const { container } = render(
      <MoodGauge
        vector={{
          aggression: 90,
          empathy: 30,
          control: 75,
          honesty: 50,
          stability: 20,
          power_gap: 80,
        }}
      />,
    )
    // 0.45*90 + 0.3*80 + 0.25*80 = 40.5 + 24 + 20 = 84.5 → 85 → 爆发边缘
    expect(container.textContent).toContain('85')
    expect(container.textContent).toContain('爆发边缘')
  })

  it('exposes a meter role with the pressure value for screen readers', () => {
    const { container } = render(<MoodGauge vector={NEUTRAL} />)
    const meter = container.querySelector('[role="meter"]')
    expect(meter).not.toBeNull()
    expect(meter!.getAttribute('aria-valuenow')).toBe(String(moodPressure(NEUTRAL)))
  })

  it('shows 平静 for a calm low-pressure opponent', () => {
    const { container } = render(
      <MoodGauge
        vector={{
          aggression: 15,
          empathy: 50,
          control: 30,
          honesty: 70,
          stability: 90,
          power_gap: 15,
        }}
      />,
    )
    expect(container.textContent).toContain('平静')
  })
})
