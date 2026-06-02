/**
 * Unit tests for StrategyRadar — the pure-SVG playstyle radar.
 *
 * @vitest-environment jsdom
 */

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'

import { StrategyRadar, type RadarDatum } from '../StrategyRadar'

afterEach(() => cleanup())

const DATA: RadarDatum[] = [
  { label: '讨好', value: 5, highlight: true },
  { label: '直球', value: 2 },
  { label: '回避', value: 0 },
  { label: '反问', value: 3 },
]

describe('StrategyRadar', () => {
  it('renders one labelled axis per datum', () => {
    const { getByLabelText, getAllByText } = render(<StrategyRadar data={DATA} />)
    const svg = getByLabelText('strategy usage radar')
    expect(svg.tagName.toLowerCase()).toBe('svg')
    // Every label is drawn as an SVG <text>.
    for (const d of DATA) {
      expect(getAllByText(d.label).length).toBeGreaterThan(0)
    }
  })

  it('draws a data polygon and a vertex dot per axis', () => {
    const { container } = render(<StrategyRadar data={DATA} />)
    // One filled data polygon (rings are also polygons, so just assert >=1).
    expect(container.querySelectorAll('polygon').length).toBeGreaterThanOrEqual(1)
    // A dot per datum.
    expect(container.querySelectorAll('circle').length).toBe(DATA.length)
  })

  it('does not divide by zero when every value is zero', () => {
    const zeroed: RadarDatum[] = DATA.map(d => ({ ...d, value: 0 }))
    const { container } = render(<StrategyRadar data={zeroed} />)
    // All vertices collapse to the centre, but nothing is NaN in the points.
    const polygons = container.querySelectorAll('polygon')
    const dataPolygon = polygons[polygons.length - 1]
    expect(dataPolygon?.getAttribute('points') ?? '').not.toContain('NaN')
  })
})
