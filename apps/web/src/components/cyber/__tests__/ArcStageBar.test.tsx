/**
 * Tests for ArcStageBar (L2).
 *
 * @vitest-environment jsdom
 */

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ArcStageBar } from '../ArcStageBar'

afterEach(() => {
  cleanup()
})

describe('ArcStageBar', () => {
  it('renders all four stage labels', () => {
    const { container } = render(<ArcStageBar stage="opening" />)
    const labels = Array.from(container.querySelectorAll('span'))
      .map((el) => el.textContent?.trim())
      .filter(Boolean)
    expect(labels).toContain('开场')
    expect(labels).toContain('冲突')
    expect(labels).toContain('转折')
    expect(labels).toContain('收尾')
  })

  it('exposes the active stage in the list aria-label', () => {
    const { container } = render(<ArcStageBar stage="turning" />)
    const list = container.querySelector('[role="list"]')
    expect(list).not.toBeNull()
    expect(list!.getAttribute('aria-label')).toContain('转折')
  })

  it('renders four stage segments', () => {
    const { container } = render(<ArcStageBar stage="conflict" />)
    expect(container.querySelectorAll('[role="listitem"]')).toHaveLength(4)
  })

  it('does not crash on the closing stage', () => {
    const { container } = render(<ArcStageBar stage="closing" />)
    const list = container.querySelector('[role="list"]')
    expect(list!.getAttribute('aria-label')).toContain('收尾')
  })
})
