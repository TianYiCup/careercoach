/**
 * Component tests for CharacterRadar (L9).
 *
 * Asserts the SVG structure rather than visual pixel output:
 *  - 6 axis labels render in the correct on-screen order
 *  - the data polygon scales with value (sc_001 contrast vs neutral)
 *  - out-of-range values clamp instead of escaping the chart
 *  - accessibility (role + aria-label) is present for screen readers
 *
 * @vitest-environment jsdom
 */

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { CharacterRadar, type RadarVector } from '../CharacterRadar'

// No global jest-dom matchers configured, so each test renders into a
// fresh DOM. Without this, the second `render` finds two SVGs at the
// same role and getByRole explodes.
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

const SC_001: RadarVector = {
  aggression: 60,
  empathy: 30,
  control: 75,
  honesty: 50,
  stability: 80,
  power_gap: 70,
}

describe('CharacterRadar', () => {
  it('renders all six dimension labels', () => {
    const { container } = render(<CharacterRadar vector={NEUTRAL} />)

    const labels = Array.from(container.querySelectorAll('text')).map((el) =>
      el.textContent?.trim(),
    )
    expect(labels).toEqual(['攻击', '共情', '控制', '诚实', '稳定', '权力'])
  })

  it('exposes an accessible role + label for screen readers', () => {
    const { container } = render(<CharacterRadar vector={NEUTRAL} />)

    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg!.getAttribute('role')).toBe('img')
    expect(svg!.getAttribute('aria-label')).toMatch(/opponent character vector/i)
  })

  it('produces a data polygon whose vertex count matches the axes (6)', () => {
    const { container } = render(<CharacterRadar vector={SC_001} />)

    // The last <polygon> in render order is the data shape (rings come first).
    const polygons = container.querySelectorAll('polygon')
    expect(polygons.length).toBeGreaterThanOrEqual(5) // 4 rings + 1 data
    const dataPolygon = polygons[polygons.length - 1]!
    const points = dataPolygon.getAttribute('points')!.trim().split(/\s+/)
    expect(points).toHaveLength(6)
  })

  it('renders a value dot per axis at the data radius', () => {
    const { container } = render(<CharacterRadar vector={SC_001} />)

    const circles = container.querySelectorAll('circle')
    expect(circles).toHaveLength(6)
  })

  it('clamps out-of-range values into the chart (defensive against bad payloads)', () => {
    const badVector: RadarVector = {
      aggression: 200,
      empathy: -50,
      control: 999,
      honesty: 50,
      stability: 50,
      power_gap: 50,
    }
    const { container } = render(<CharacterRadar vector={badVector} />)

    const polygons = container.querySelectorAll('polygon')
    const dataPolygon = polygons[polygons.length - 1]!
    const coords = dataPolygon
      .getAttribute('points')!
      .split(/[\s,]+/)
      .map(Number)
    // All vertices must stay inside the 180x180 viewBox — a 200 value
    // that escaped the clamp would push x past 140 (center 90 + radius
    // 50). With clamp, max distance is exactly 50 from center.
    for (const v of coords) {
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThanOrEqual(180)
    }
  })

  it('produces visibly different polygons for contrasting vectors', () => {
    const { container: hrContainer, unmount } = render(<CharacterRadar vector={SC_001} />)
    const hrPoints = hrContainer
      .querySelectorAll('polygon')[4]!
      .getAttribute('points')
    unmount()

    const { container: neutralContainer } = render(<CharacterRadar vector={NEUTRAL} />)
    const neutralPoints = neutralContainer
      .querySelectorAll('polygon')[4]!
      .getAttribute('points')

    expect(hrPoints).not.toEqual(neutralPoints)
  })
})
