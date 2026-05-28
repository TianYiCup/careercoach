/**
 * Tests for StrategyCard (L8).
 *
 * @vitest-environment jsdom
 */

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { StrategyCard } from '../StrategyCard'

afterEach(() => {
  cleanup()
})

describe('StrategyCard', () => {
  it('glosses the strategy + upgrade keys to Chinese and shows the effect', () => {
    const { container } = render(
      <StrategyCard read={{ strategy: 'placate', effect: 'poor', upgrade: 'direct' }} />,
    )
    const text = container.textContent ?? ''
    expect(text).toContain('讨好')
    expect(text).toContain('没奏效')
    expect(text).toContain('直球')
    expect(text).toContain('试试')
  })

  it('reads 保持 when the upgrade equals the current strategy', () => {
    const { container } = render(
      <StrategyCard read={{ strategy: 'direct', effect: 'good', upgrade: 'direct' }} />,
    )
    const text = container.textContent ?? ''
    expect(text).toContain('保持')
    expect(text).not.toContain('试试')
    expect(text).toContain('奏效')
  })

  it('glosses every strategy key without falling back to the raw key', () => {
    const keys = ['placate', 'concede', 'avoid', 'deflect', 'counter', 'reason', 'direct'] as const
    for (const key of keys) {
      const { container, unmount } = render(
        <StrategyCard read={{ strategy: key, effect: 'mixed', upgrade: key }} />,
      )
      // The raw English key must never leak into the rendered text.
      expect(container.textContent).not.toContain(key)
      unmount()
    }
  })
})
