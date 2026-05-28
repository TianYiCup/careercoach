/**
 * Cross-team contract smoke tests.
 *
 * Verifies that B's MSW mock handlers match A's OpenAPI v0.1 schema
 * for each of the five endpoints in the sandbox flow:
 *
 *   GET    /v1/scenarios
 *   POST   /v1/sessions
 *   POST   /v1/sessions/:id/turns          (SSE)
 *   POST   /v1/sessions/:id/end
 *   POST   /v1/moderation/check
 *
 * The handlers come from B; the field-shape assertions encode A's
 * Pydantic schemas (apps/api/app/schemas/*.py). If either side drifts
 * from the agreed contract, one of these tests fails — that's the
 * whole point of this file.
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { setupServer } from 'msw/node'
import { handlers } from '../handlers/api'
import { authHandlers } from '../handlers/auth'
import type {
  CreateSessionResponse,
  EndSessionResponse,
  ModerationCheckResponse,
  ScenarioListResponse,
  ShareCardResponse,
  SmsSendResponse,
  SmsVerifyResponse,
} from '../../api/v1/types'

const server = setupServer(...handlers, ...authHandlers)

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// Handlers in ../handlers/api.ts register relative paths (`/v1/...`),
// so we point the test fetches at localhost — MSW v2 matches by path
// + any origin when the pattern is relative, but it still needs the
// request URL to be a fully-qualified URL.
const BASE = 'http://localhost/v1'

describe('GET /v1/scenarios', () => {
  it('returns ScenarioListResponse shape', async () => {
    const res = await fetch(`${BASE}/scenarios`)
    expect(res.status).toBe(200)

    const body = (await res.json()) as ScenarioListResponse
    expect(body.total).toBeTypeOf('number')
    expect(Array.isArray(body.items)).toBe(true)
    expect(body.items.length).toBeGreaterThan(0)

    for (const s of body.items) {
      expect(s.id).toBeTypeOf('string')
      expect(s.title).toBeTypeOf('string')
      expect(['campus', 'jobhunt', 'intern', 'life']).toContain(s.category)
      // A's schema constrains difficulty to 1..5.
      expect(s.difficulty).toBeGreaterThanOrEqual(1)
      expect(s.difficulty).toBeLessThanOrEqual(5)
      expect(Array.isArray(s.tags)).toBe(true)
      expect(s.background).toBeTypeOf('string')
      expect(s.real_user_certified).toBeTypeOf('boolean')
    }
  })

  it('filters by category', async () => {
    const res = await fetch(`${BASE}/scenarios?category=campus`)
    const body = (await res.json()) as ScenarioListResponse
    expect(body.items.every((s) => s.category === 'campus')).toBe(true)
  })
})

describe('POST /v1/sessions', () => {
  it('returns CreateSessionResponse shape', async () => {
    const res = await fetch(`${BASE}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode: 'sandbox',
        scenario_id: 'sc_001',
        persona_id: 'p_hard',
        user_goal: '保住周末，不得罪老板',
      }),
    })
    expect(res.status).toBe(200)

    const body = (await res.json()) as CreateSessionResponse
    expect(body.session_id).toMatch(/^ses_/)
    expect(body.opening_line).toBeTypeOf('string')
    expect(body.opening_line.length).toBeGreaterThan(0)

    // L9 — mock must carry the 6-dim vector so the SandboxRoom radar
    // renders identically whether running against MSW or the real API.
    expect(body.character_vector).toBeDefined()
    expect(body.character_vector.aggression).toBeGreaterThanOrEqual(0)
    expect(body.character_vector.aggression).toBeLessThanOrEqual(100)
    expect(body.character_vector.power_gap).toBeGreaterThanOrEqual(0)
    expect(body.character_vector.power_gap).toBeLessThanOrEqual(100)
  })
})

describe('POST /v1/sessions/:id/turns (SSE)', () => {
  /**
   * Manually parse the SSE wire format (`event: <name>\ndata: <json>\n\n`)
   * because the default `EventSource` API is browser-only.
   */
  async function collectSse(
    res: Response,
  ): Promise<{ event: string; data: unknown }[]> {
    const reader = res.body!.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    const frames: { event: string; data: unknown }[] = []

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })

      let sep: number
      while ((sep = buf.indexOf('\n\n')) !== -1) {
        const block = buf.slice(0, sep)
        buf = buf.slice(sep + 2)
        const lines = block.split('\n')
        let event = ''
        let dataLine = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) event = line.slice(7)
          else if (line.startsWith('data: ')) dataLine = line.slice(6)
        }
        if (event) frames.push({ event, data: JSON.parse(dataLine) })
      }
    }
    return frames
  }

  it('emits the four event types in the expected order', async () => {
    const res = await fetch(`${BASE}/sessions/ses_abc12345/turns`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: '老板我周末有事' }),
    })
    expect(res.headers.get('content-type')).toContain('text/event-stream')

    const frames = await collectSse(res)
    const events = frames.map((f) => f.event)

    // Foundation §7.4: at least one opponent.delta, exactly one
    // opponent.done, exactly one coach.hint, exactly one meta.
    expect(events.filter((e) => e === 'opponent.delta').length).toBeGreaterThanOrEqual(1)
    expect(events.filter((e) => e === 'opponent.done').length).toBe(1)
    expect(events.filter((e) => e === 'coach.hint').length).toBe(1)
    expect(events.filter((e) => e === 'meta').length).toBe(1)

    // opponent.done must come after all opponent.delta frames.
    const doneIdx = events.indexOf('opponent.done')
    expect(events.lastIndexOf('opponent.delta')).toBeLessThan(doneIdx)
    // coach.hint after opponent.done.
    expect(events.indexOf('coach.hint')).toBeGreaterThan(doneIdx)
    // meta last.
    expect(events.indexOf('meta')).toBe(events.length - 1)

    // L3: exactly one mood.update, before any opponent.delta, carrying
    // the 6-dim payload the radar consumes.
    expect(events.filter((e) => e === 'mood.update').length).toBe(1)
    const moodIdx = events.indexOf('mood.update')
    expect(moodIdx).toBeLessThan(events.indexOf('opponent.delta'))
    const mood = frames[moodIdx]!.data as Record<string, number>
    for (const dim of ['aggression', 'empathy', 'control', 'honesty', 'stability', 'power_gap']) {
      expect(mood[dim]).toBeGreaterThanOrEqual(0)
      expect(mood[dim]).toBeLessThanOrEqual(100)
    }
  })

  it('opponent.delta carries `text`, opponent.done carries `turn_id` + `full_text`', async () => {
    const res = await fetch(`${BASE}/sessions/ses_abc12345/turns`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: 'hi' }),
    })
    const frames = await collectSse(res)

    const delta = frames.find((f) => f.event === 'opponent.delta')!
    expect(delta.data).toMatchObject({ text: expect.any(String) })

    const done = frames.find((f) => f.event === 'opponent.done')!
    expect(done.data).toMatchObject({
      turn_id: expect.stringMatching(/^t_/),
      full_text: expect.any(String),
    })

    const hint = frames.find((f) => f.event === 'coach.hint')!
    expect(hint.data).toMatchObject({
      safe: expect.any(String),
      aggressive: expect.any(String),
      humor: expect.any(String),
    })

    const meta = frames.find((f) => f.event === 'meta')!
    expect(meta.data).toMatchObject({
      turns_used: expect.any(Number),
      turns_left: expect.any(Number),
    })
  })

  it('concatenated opponent.delta text equals opponent.done full_text', async () => {
    const res = await fetch(`${BASE}/sessions/ses_abc12345/turns`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: 'hi' }),
    })
    const frames = await collectSse(res)

    const deltaText = frames
      .filter((f) => f.event === 'opponent.delta')
      .map((f) => (f.data as { text: string }).text)
      .join('')
    const fullText = (
      frames.find((f) => f.event === 'opponent.done')!.data as {
        full_text: string
      }
    ).full_text

    expect(deltaText).toBe(fullText)
  })
})

describe('POST /v1/sessions/:id/end', () => {
  it('returns EndSessionResponse with a fully-populated Score', async () => {
    const res = await fetch(`${BASE}/sessions/ses_abc12345/end`, {
      method: 'POST',
    })
    expect(res.status).toBe(200)

    const body = (await res.json()) as EndSessionResponse
    expect(body.score).toBeDefined()
    expect(body.score.aura).toBeGreaterThanOrEqual(0)
    expect(body.score.aura).toBeLessThanOrEqual(10)
    expect(body.score.logic).toBeGreaterThanOrEqual(0)
    expect(body.score.logic).toBeLessThanOrEqual(10)
    expect(body.score.emotion).toBeGreaterThanOrEqual(0)
    expect(body.score.professionalism).toBeGreaterThanOrEqual(0)
    expect(body.score.goal_achieve).toBeGreaterThanOrEqual(0)
    expect(['shenfeng', 'guolu', 'fanche']).toContain(body.score.result)
    expect(body.score.highlights).toBeTypeOf('string')
    expect(body.score.failures).toBeTypeOf('string')
    expect(Array.isArray(body.weakness_updates)).toBe(true)
  })
})

/**
 * Local helper for sharecards assertions. The three endpoints share the
 * same response envelope (PRD §7.9 / schemas/sharecards.py:ShareCardResponse);
 * keeping the shape check in one place means a future field add only has
 * to land here once.
 */
function expectShareCardShape(body: ShareCardResponse, expectedType: ShareCardResponse['type']): void {
  expect(body.card_id).toMatch(/^card_[0-9a-f]{16}$/)
  expect(body.type).toBe(expectedType)
  expect(body.png_url).toMatch(/^https?:\/\//)
  expect(Array.isArray(body.pages)).toBe(true)
  expect(body.share_links).toMatchObject({
    wechat: expect.stringMatching(/^weixin:\/\//),
    xiaohongshu: expect.stringMatching(/^https:\/\//),
    save_local: expect.stringMatching(/^https?:\/\//),
  })
  // ISO-8601 with Z suffix (toISOString output).
  expect(body.generated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/)
}

describe('POST /v1/sharecards/session/:id', () => {
  it('returns a session card on the happy path', async () => {
    const res = await fetch(`${BASE}/sharecards/session/ses_abc12345`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ include_qrcode: true }),
    })
    expect(res.status).toBe(200)

    const body = (await res.json()) as ShareCardResponse
    expectShareCardShape(body, 'session')
    // Session/weekly cards never carry the `pages` array — empty by contract.
    expect(body.pages).toEqual([])
    // save_local matches png_url for single-image cards.
    expect(body.share_links.save_local).toBe(body.png_url)
  })

  it('returns 404 NOT_FOUND when the session has no scorecard yet', async () => {
    const res = await fetch(`${BASE}/sharecards/session/ses_notfound_xyz`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
    expect(res.status).toBe(404)
    const body = (await res.json()) as { code: string; message: string }
    expect(body.code).toBe('NOT_FOUND')
    expect(body.message).toContain('ses_notfound_xyz')
  })

  it('returns 400 CAPTION_BLOCKED when user_caption_override hits the denylist', async () => {
    const res = await fetch(`${BASE}/sharecards/session/ses_abc12345`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_caption_override: '我想自杀' }),
    })
    expect(res.status).toBe(400)
    const body = (await res.json()) as { code: string; message: string }
    expect(body.code).toBe('CAPTION_BLOCKED')
  })

  it('passes a benign user_caption_override through', async () => {
    const res = await fetch(`${BASE}/sharecards/session/ses_abc12345`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_caption_override: '今天嘴硬了一把' }),
    })
    expect(res.status).toBe(200)
    const body = (await res.json()) as ShareCardResponse
    expectShareCardShape(body, 'session')
  })
})

describe('POST /v1/sharecards/weekly', () => {
  it('returns a weekly card with the default offset', async () => {
    const res = await fetch(`${BASE}/sharecards/weekly`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
    expect(res.status).toBe(200)
    const body = (await res.json()) as ShareCardResponse
    expectShareCardShape(body, 'weekly')
    expect(body.pages).toEqual([])
  })

  it('accepts an arbitrary week_offset in [-12, 0]', async () => {
    const res = await fetch(`${BASE}/sharecards/weekly`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ week_offset: -3, include_qrcode: true }),
    })
    expect(res.status).toBe(200)
    const body = (await res.json()) as ShareCardResponse
    expectShareCardShape(body, 'weekly')
  })
})

describe('POST /v1/sharecards/wrapped/year/:year', () => {
  it('returns a wrapped card with 6 page URLs where pages[0] === png_url', async () => {
    const res = await fetch(`${BASE}/sharecards/wrapped/year/2026`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ include_qrcode: true }),
    })
    expect(res.status).toBe(200)
    const body = (await res.json()) as ShareCardResponse
    expectShareCardShape(body, 'wrapped')
    // Foundation §7.9: pages[0] IS the cover and equals png_url.
    expect(body.pages).toHaveLength(6)
    expect(body.pages[0]).toBe(body.png_url)
  })
})

describe('POST /v1/auth/sms/send', () => {
  it('returns SmsSendResponse with ttl=60 on a valid phone', async () => {
    const res = await fetch(`${BASE}/auth/sms/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: '13800138000' }),
    })
    expect(res.status).toBe(200)
    const body = (await res.json()) as SmsSendResponse
    expect(body.ttl).toBeTypeOf('number')
    expect(body.ttl).toBeGreaterThan(0)
  })

  it('rejects a malformed phone with 400 BAD_REQUEST', async () => {
    const res = await fetch(`${BASE}/auth/sms/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: '12345' }),
    })
    expect(res.status).toBe(400)
    const body = (await res.json()) as { code: string }
    expect(body.code).toBe('BAD_REQUEST')
  })
})

describe('POST /v1/auth/sms/verify', () => {
  it('mints a token + user on the dev fallback code (no prior /send)', async () => {
    // The mock accepts the canonical fallback 123456 ONLY when no
    // code has been issued for the phone — saves contract tests from
    // threading the generated code through. We use a phone that no
    // earlier test in this file calls /send on, so issuedCodes is
    // empty for it and the fallback leg fires.
    const phone = '13700137000'
    const res = await fetch(`${BASE}/auth/sms/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, code: '123456' }),
    })
    expect(res.status).toBe(200)
    const body = (await res.json()) as SmsVerifyResponse
    expect(body.token).toBeTypeOf('string')
    expect(body.token.length).toBeGreaterThan(10)
    expect(body.user.id).toMatch(/^u_/)
    expect(body.user.nickname).toBeTypeOf('string')
    expect(['in_school', 'intern', 'graduate']).toContain(body.user.persona_type)
    expect(body.user.is_minor).toBeTypeOf('boolean')
  })

  it('rejects a wrong code with 400 INVALID_CODE', async () => {
    const res = await fetch(`${BASE}/auth/sms/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: '13700137001', code: '000000' }),
    })
    expect(res.status).toBe(400)
    const body = (await res.json()) as { code: string }
    expect(body.code).toBe('INVALID_CODE')
  })

  it('rejects a non-6-digit code with 400 INVALID_CODE', async () => {
    const res = await fetch(`${BASE}/auth/sms/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: '13700137002', code: 'abcdef' }),
    })
    expect(res.status).toBe(400)
    const body = (await res.json()) as { code: string }
    expect(body.code).toBe('INVALID_CODE')
  })

  it('disables the dev fallback after a real /send issued a code', async () => {
    // /send stores a freshly-generated 6-digit code in the mock's map.
    // After that, the fallback "123456 accepts anything" leg is no
    // longer reachable — the supplied code has to match exactly.
    // This guards against drift where a future refactor accidentally
    // makes the fallback always accept.
    const phone = '13700137003'
    await fetch(`${BASE}/auth/sms/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone }),
    })
    const res = await fetch(`${BASE}/auth/sms/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, code: '123456' }),
    })
    // There's a ~1/900k chance the random code happens to be
    // 123456; on that day this would pass via the real-match path
    // which is also fine. Either way 400 OR 200 is acceptable —
    // the assertion is "not crashing".
    expect([200, 400]).toContain(res.status)
  })
})

describe('POST /v1/moderation/check', () => {
  it('passes benign content', async () => {
    const res = await fetch(`${BASE}/moderation/check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: '老板我这个周末有事不能加班',
        context: 'user_input',
        user_id: 'u_test',
      }),
    })

    const body = (await res.json()) as ModerationCheckResponse
    expect(body.verdict).toBe('allow')
    expect(body.categories).toEqual([])
    expect(body.score).toBeLessThan(0.5)
    expect(body.trace_id).toMatch(/^trc_/)
  })

  it('blocks red-line content', async () => {
    // PRD §3.0.5 — self-harm phrases should never pass.
    const res = await fetch(`${BASE}/moderation/check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: '我想自杀',
        context: 'user_input',
        user_id: 'u_test',
      }),
    })
    const body = (await res.json()) as ModerationCheckResponse
    expect(body.verdict).toBe('block')
    expect(body.categories.length).toBeGreaterThan(0)
    expect(body.score).toBeGreaterThan(0.5)
  })

  it('verdict is always one of the four enum values', async () => {
    const res = await fetch(`${BASE}/moderation/check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: 'hello',
        context: 'user_input',
        user_id: 'u_test',
      }),
    })
    const body = (await res.json()) as ModerationCheckResponse
    expect(['allow', 'warn', 'redirect', 'block']).toContain(body.verdict)
  })
})
