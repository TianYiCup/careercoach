import { http, HttpResponse, delay } from 'msw'
import type {
  ScenarioListResponse,
  CreateSessionRequest,
  CreateSessionResponse,
  EndSessionResponse,
  ModerationCheckRequest,
  ModerationCheckResponse,
  ShareCardResponse,
  ShareCardType,
  SessionShareCardRequest,
  WeeklyShareCardRequest,
  CreateReviewUploadRequest,
  ReviewUploadResponse,
  CreateCopilotSessionRequest,
  CreateCopilotSessionResponse,
  SetVibeRequest,
  VibeResponse,
  VibeType as ApiVibeType,
  StreakResponse,
  CustomScenarioRequest,
  CustomScenarioResponse,
  WeaknessProfileResponse,
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

/** 16-char hex card id matching the backend schema example. */
function newCardId(): string {
  return `card_${crypto.randomUUID().replaceAll('-', '').slice(0, 16)}`
}

interface ShareCardExtras {
  weekOffset?: number
  year?: number
}

/** Build a ShareCardResponse — shape mirrors schemas/sharecards.py. */
function buildShareCard(type: ShareCardType, extras: ShareCardExtras = {}): ShareCardResponse {
  const cardId = newCardId()
  const origin = 'https://cdn.example.com/sharecards'
  const cover = `${origin}/${cardId}.png`
  const pages =
    type === 'wrapped'
      ? [
          cover,
          `${origin}/${cardId}/p1_count.png`,
          `${origin}/${cardId}/p2_opponent.png`,
          `${origin}/${cardId}/p3_shenfeng.png`,
          `${origin}/${cardId}/p4_fanche.png`,
          `${origin}/${cardId}/p5_letter.png`,
        ]
      : []

  void extras // reserved for future variations (week_offset / year overlays)

  return {
    card_id: cardId,
    type,
    png_url: cover,
    pages,
    share_links: {
      wechat: `weixin://dl/share?card=${cardId}`,
      xiaohongshu: `https://www.xiaohongshu.com/share?img=${cardId}`,
      save_local: cover,
    },
    generated_at: new Date().toISOString(),
  }
}

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

  // POST /v1/sharecards/session/:sessionId — PRD §7.9
  // Mocks the happy path; mirrors backend's CAPTION_BLOCKED + NOT_FOUND
  // shapes so the UI's error branches are exercisable without spinning
  // up the real service. Real backend route is wired in PR ③ of D6;
  // /weekly and /wrapped are still 501 server-side, mocked here so
  // B-track UI work isn't blocked on F5 (sessions epic).
  http.post(`${BASE}/sharecards/session/:sessionId`, async ({ request, params }) => {
    await delay(400)
    const sessionId = params.sessionId as string
    const body = (await request.json()) as SessionShareCardRequest

    // Predictable 404 hook: any session id containing "notfound" lets
    // the UI test its "no scorecard yet" path. Real backend issues this
    // when the session was never ended (no row in session_scores).
    if (sessionId.includes('notfound')) {
      return HttpResponse.json(
        { code: 'NOT_FOUND', message: `session ${sessionId} has no scorecard yet` },
        { status: 404 },
      )
    }

    // Caption moderation mirrors POST /v1/moderation/check's denylist.
    const caption = body.user_caption_override ?? ''
    const dangerWords = ['自杀', '死', '杀', '网贷', '贷款']
    const hit = dangerWords.find((w) => caption.includes(w))
    if (hit) {
      return HttpResponse.json(
        {
          code: 'CAPTION_BLOCKED',
          message: `user_caption_override blocked by content moderation (other)`,
        },
        { status: 400 },
      )
    }

    return HttpResponse.json(buildShareCard('session'))
  }),

  // POST /v1/sharecards/weekly — PRD §7.9
  http.post(`${BASE}/sharecards/weekly`, async ({ request }) => {
    await delay(400)
    const body = (await request.json()) as WeeklyShareCardRequest
    // Surface the week_offset back into the caption so UI dev can
    // distinguish "this week" vs backfilled previous weeks visually.
    return HttpResponse.json(buildShareCard('weekly', { weekOffset: body.week_offset ?? 0 }))
  }),

  // POST /v1/sharecards/wrapped/year/:year — PRD §7.9
  http.post(`${BASE}/sharecards/wrapped/year/:year`, async ({ params }) => {
    await delay(500)
    const year = Number(params.year)
    return HttpResponse.json(buildShareCard('wrapped', { year }))
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

  // POST /v1/review/uploads — review text (PRD §3.3)
  http.post(`${BASE}/review/uploads`, async ({ request }) => {
    await delay(1500)
    const body = (await request.json()) as CreateReviewUploadRequest

    // Parse the text into turns (split by \n, prefix "对方：" / "我：")
    const lines = body.text.split('\n').filter((l) => l.trim())
    const turns = lines.map((line, idx) => {
      const isOpponent = line.startsWith('对方') || line.startsWith('🤵')
      const content = line.replace(/^(对方|🤵|我|👤)[：:]\s*/, '').trim()
      const isLose = !isOpponent && idx % 3 === 1
      return {
        turn_idx: idx,
        speaker: (isOpponent ? 'opponent' : 'user') as 'opponent' | 'user',
        content,
        verdict: (isOpponent ? 'neutral' : isLose ? 'lose' : 'win') as 'win' | 'neutral' | 'lose',
        reason: isLose ? '语气过软，缺少数据支撑' : null,
        better: isLose ? '可以用具体成果来回应，比如"本周完成了A和B"' : null,
      }
    })

    const loseTurns = turns.filter((t) => t.verdict === 'lose')
    const response: ReviewUploadResponse = {
      upload_id: `up_${crypto.randomUUID().slice(0, 8)}`,
      status: 'done',
      turns,
      summary: {
        score: 6.4,
        top_failures: loseTurns.length > 0
          ? loseTurns.slice(0, 3).map((t) => t.reason ?? '未追问')
          : ['主动让步', '缺数据支撑'],
        improvements: ['先认可对方再表达立场', '用具体数据替代模糊表态', '给一个替代方案而非直接拒绝'],
      },
      created_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
    }
    return HttpResponse.json(response)
  }),

  // GET /v1/review/uploads/:uploadId
  http.get(`${BASE}/review/uploads/:uploadId`, async () => {
    await delay(300)
    // Return same mock as POST for now
    return HttpResponse.json({
      upload_id: 'up_replay001',
      status: 'done',
      turns: [
        { turn_idx: 0, speaker: 'opponent', content: '你这周做的不够多', verdict: 'neutral' },
        { turn_idx: 1, speaker: 'user', content: '我尽力了', verdict: 'lose', reason: '主动让步，没有用事实反驳', better: '本周完成了X、Y、Z三件事，具体哪部分需要加强？' },
        { turn_idx: 2, speaker: 'opponent', content: '你看A同事多努力', verdict: 'neutral' },
        { turn_idx: 3, speaker: 'user', content: '好的我加班', verdict: 'lose', reason: '被贬损式比较带节奏，直接妥协', better: '每个人的工作节奏不同，我看重的是产出质量' },
      ],
      summary: { score: 4.2, top_failures: ['主动让步', '被带节奏'], improvements: ['用具体成果回应', '质疑比较的合理性'] },
      created_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
    } satisfies ReviewUploadResponse)
  }),

  // POST /v1/copilot/sessions — PRD §7.5 / design-spec §9.5
  // Returns a mock ws_url; actual WS events are simulated client-side
  // by useCopilotSession in mock mode (MSW can't intercept WebSocket).
  http.post(`${BASE}/copilot/sessions`, async ({ request }) => {
    await delay(600)
    const body = (await request.json()) as CreateCopilotSessionRequest
    const response: CreateCopilotSessionResponse = {
      copilot_id: `cop_${crypto.randomUUID().replaceAll('-', '').slice(0, 16)}`,
      ws_url: `wss://mock.careercoach.ai/copilot/ws`,
    }
    void body.scenario_hint // consumed by real backend, stored but not acted on in mock
    return HttpResponse.json(response)
  }),

  // GET /v1/streak — PR #130
  http.get(`${BASE}/streak`, async () => {
    await delay(200)
    const response: StreakResponse = {
      current_days: 12,
      max_days: 21,
    }
    return HttpResponse.json(response)
  }),

  // POST /v1/vibe/today — PR #129
  http.post(`${BASE}/vibe/today`, async ({ request }) => {
    await delay(300)
    const body = (await request.json()) as SetVibeRequest
    const response: VibeResponse = {
      vibe: body.vibe as ApiVibeType,
      logged_date: new Date().toISOString().slice(0, 10),
    }
    return HttpResponse.json(response)
  }),

  // POST /v1/scenarios/custom — PR #132-133
  http.post(`${BASE}/scenarios/custom`, async ({ request }) => {
    await delay(1500)
    const body = (await request.json()) as CustomScenarioRequest
    const desc = body.description.trim()
    const response: CustomScenarioResponse = {
      scenario_id: `cs_${crypto.randomUUID().slice(0, 8)}`,
      title: desc.length > 20 ? desc.slice(0, 20) + '...' : desc,
      background: `自定义场景：${desc}`,
      persona_title: 'custom_opponent',
      opening_line: '你来找我有什么事？说说看吧。',
    }
    return HttpResponse.json(response)
  }),

  // GET /v1/users/me/weaknesses — PR #131
  http.get(`${BASE}/users/me/weaknesses`, async () => {
    await delay(400)
    const response: WeaknessProfileResponse = {
      weaknesses: [
        { tag: '主动让步', frequency: 9, last_seen: '2026-05-19' },
        { tag: '缺数据支撑', frequency: 6, last_seen: '2026-05-18' },
        { tag: '情绪外露', frequency: 4, last_seen: '2026-05-15' },
        { tag: '被带节奏', frequency: 3, last_seen: '2026-05-12' },
        { tag: '不敢提问', frequency: 2, last_seen: '2026-05-08' },
      ],
      recommended_scenarios: [
        { id: 'sc_001', title: '拒绝加班谈判', category: 'intern', difficulty: 3, tags: ['拒绝', '上下级'], background: '你刚结束周五的项目，老板在群里@你让周末加班赶进度。', real_user_certified: true },
        { id: 'sc_002', title: '实习转正薪资谈判', category: 'jobhunt', difficulty: 4, tags: ['薪资', '谈判'], background: '实习期结束，HR约你聊转正，薪资比你预期低30%。', real_user_certified: true },
      ],
    }
    return HttpResponse.json(response)
  }),
]
