/**
 * Tests for useVibe mapping utilities:
 *   - apiVibeToUi: API VibeType → Chinese UI label
 *   - uiVibeToApi: Chinese UI label → API VibeType
 *   - Bidirectional round-trip consistency
 */

import { describe, expect, it } from 'vitest'
import { apiVibeToUi, uiVibeToApi } from '../useVibe'
import type { UiVibeType } from '../useVibe'
import type { VibeType as ApiVibeType } from '../../../api/v1/types'

const API_VIBES: ApiVibeType[] = ['fire', 'tired', 'anxious', 'excited', 'meh']
const UI_VIBES: UiVibeType[] = ['燃爆', '想躺平', '莫名烦', '雄心勃勃', '佛系']

describe('apiVibeToUi', () => {
  it('maps every API vibe to a Chinese label', () => {
    for (const v of API_VIBES) {
      const ui = apiVibeToUi(v)
      expect(UI_VIBES).toContain(ui)
    }
  })

  it('maps specific known pairs', () => {
    expect(apiVibeToUi('fire')).toBe('燃爆')
    expect(apiVibeToUi('tired')).toBe('想躺平')
    expect(apiVibeToUi('anxious')).toBe('莫名烦')
    expect(apiVibeToUi('excited')).toBe('雄心勃勃')
    expect(apiVibeToUi('meh')).toBe('佛系')
  })
})

describe('uiVibeToApi', () => {
  it('maps every Chinese label to an API vibe', () => {
    for (const v of UI_VIBES) {
      const api = uiVibeToApi(v)
      expect(API_VIBES).toContain(api)
    }
  })

  it('maps specific known pairs', () => {
    expect(uiVibeToApi('燃爆')).toBe('fire')
    expect(uiVibeToApi('想躺平')).toBe('tired')
    expect(uiVibeToApi('莫名烦')).toBe('anxious')
    expect(uiVibeToApi('雄心勃勃')).toBe('excited')
    expect(uiVibeToApi('佛系')).toBe('meh')
  })
})

describe('round-trip consistency', () => {
  it('apiVibeToUi → uiVibeToApi returns original', () => {
    for (const v of API_VIBES) {
      expect(uiVibeToApi(apiVibeToUi(v))).toBe(v)
    }
  })

  it('uiVibeToApi → apiVibeToUi returns original', () => {
    for (const v of UI_VIBES) {
      expect(apiVibeToUi(uiVibeToApi(v))).toBe(v)
    }
  })
})
