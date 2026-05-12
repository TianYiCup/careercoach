import { http, HttpResponse, delay } from 'msw'
import type {
  ScenarioListResponse,
  CreateSessionRequest,
  CreateSessionResponse,
  EndSessionResponse,
  ModerationCheckRequest,
  ModerationCheckResponse,
} from '../../api/v1/types'

// Wildcard origin so handlers match both relative dev fetches (`/v1/...`)
// and fully-qualified URLs (e.g. `http://localhost/v1/...` from tests
// or `https://api.careercoach.app/v1/...` from a built EXE). MSW v2's
// path-only patterns don't match fully-qualified URLs.
const BASE = '*/v1'

// --- Mock Data ---
const mockScenarios: ScenarioListResponse = {
  items: [
    {
      id: 'sc_001',
      title: '周末加班谈判',
      category: 'intern',
      difficulty: 3,
      tags: ['拒绝', '上下级'],
      background: '你刚结束周五的项目，老板在群里@你让周末加班赶进度。',
      real_user_certified: true,
    },
    {
      id: 'sc_002',
      title: '实习转正薪资谈判',
      category: 'jobhunt',
      difficulty: 4,
      tags: ['薪资', '谈判'],
      background: '实习期结束，HR约你聊转正，薪资比你预期低30%。',
      real_user_certified: true,
    },
    {
      id: 'sc_003',
      title: '室友深夜打游戏',
      category: 'campus',
      difficulty: 2,
      tags: ['室友', '沟通'],
      background: '室友每天打游戏到凌晨2点，你明天有早八。',
      real_user_certified: false,
    },
  ],
  total: 3,
}

let turnCounter = 0

// --- Handlers ---
export const handlers = [
  // GET /v1/scenarios
  http.get(`${BASE}/scenarios`, async ({ request }) => {
    await delay(300)
    const url = new URL(request.url)
    const category = url.searchParams.get('category')

    let items = mockScenarios.items
    if (category) {
      items = items.filter((s) => s.category === category)
    }

    return HttpResponse.json({ items, total: items.length })
  }),

  // POST /v1/sessions
  http.post(`${BASE}/sessions`, async ({ request }) => {
    await delay(500)
    const body = (await request.json()) as CreateSessionRequest

    const openings: Record<string, string> = {
      sc_001: '小林啊，这个周末项目得加个班，应该没问题吧？',
      sc_002: '坐吧，转正的事情我们聊聊。你的期望薪资是多少？',
      sc_003: '嘿，再来一把？这把一定赢！',
    }

    const response: CreateSessionResponse = {
      session_id: `ses_${crypto.randomUUID().slice(0, 8)}`,
      opening_line: openings[body.scenario_id] ?? '我们来聊聊吧。',
    }

    turnCounter = 0
    return HttpResponse.json(response)
  }),

  // POST /v1/sessions/:sessionId/turns (SSE)
  http.post(`${BASE}/sessions/:sessionId/turns`, async () => {
    turnCounter++

    const opponentReplies = [
      '什么安排比工作还重要？',
      '你这种态度，转正的时候别怪我没提醒你。',
      '行吧，那这个项目延误的责任谁来承担？',
    ]

    const reply = opponentReplies[(turnCounter - 1) % opponentReplies.length]!
    const turnId = `t_${crypto.randomUUID().slice(0, 8)}`

    const coachHints = {
      safe: '可以反问deadline，让对方先暴露底牌',
      aggressive: '直接质疑加班合理性，引用劳动法',
      humor: '说"我已经和床约好了，不能放它鸽子"',
    }

    const frames = [
      { event: 'opponent.delta', data: { text: reply.slice(0, 2) } },
      { event: 'opponent.delta', data: { text: reply.slice(2) } },
      { event: 'opponent.done', data: { turn_id: turnId, full_text: reply } },
      { event: 'coach.hint', data: coachHints },
      { event: 'meta', data: { turns_used: turnCounter, turns_left: 30 - turnCounter } },
    ]

    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      async start(controller) {
        for (const frame of frames) {
          await delay(200)
          controller.enqueue(
            encoder.encode(`event: ${frame.event}\ndata: ${JSON.stringify(frame.data)}\n\n`),
          )
        }
        controller.close()
      },
    })

    return new Response(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      },
    })
  }),

  // POST /v1/sessions/:sessionId/end
  http.post(`${BASE}/sessions/:sessionId/end`, async () => {
    await delay(800)
    const response: EndSessionResponse = {
      score: {
        aura: 7,
        logic: 6,
        emotion: 5,
        professionalism: 8,
        goal_achieve: 4,
        highlights: '在压力下保持了专业态度',
        failures: '过早让步，没有坚持底线',
        result: 'guolu',
      },
      weakness_updates: [{ tag: '过早让步', delta: 1 }],
    }
    return HttpResponse.json(response)
  }),

  // POST /v1/moderation/check
  http.post(`${BASE}/moderation/check`, async ({ request }) => {
    await delay(200)
    const body = (await request.json()) as ModerationCheckRequest

    const response: ModerationCheckResponse = {
      verdict: 'allow',
      categories: [],
      score: 0.01,
      trace_id: `trc_${crypto.randomUUID().slice(0, 8)}`,
    }

    // Simple keyword check for mock
    const dangerWords = ['自杀', '死', '杀', '网贷', '贷款']
    const found = dangerWords.find((w) => body.content.includes(w))
    if (found) {
      response.verdict = 'block'
      response.categories = ['other']
      response.score = 0.95
    }

    return HttpResponse.json(response)
  }),
]
