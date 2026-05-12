# Sprint 0 · A↔B Integration Check (2026-05-12)

A small audit + smoke run after B landed Tauri (PR #15) + MSW handlers
(PR #16). Goal: surface contract drift between A's OpenAPI v0.1 and
B's typed client + MSW before either side keeps building on assumptions.

## Audit results — five v1 endpoints

| Endpoint | A side (Pydantic) | B side (`types.ts` + handlers) | Verdict |
|---|---|---|---|
| `GET /v1/scenarios` | `ScenarioListResponse{items, total}`, item has `id/title/category∈{campus,jobhunt,intern,life}/difficulty 1–5/tags/background/real_user_certified` | Same field set, same `category` literal union | ✅ |
| `POST /v1/sessions` | `{mode, scenario_id, persona_id, user_goal}` → `{session_id, opening_line}` | Same | ✅ |
| `POST /v1/sessions/{id}/turns` (SSE) | `event∈{opponent.delta,opponent.done,coach.hint,meta}`, each with its own `data` shape | Same frame names; `data` shapes match (`text` / `turn_id+full_text` / `safe+aggressive+humor` / `turns_used+turns_left`) | ✅ |
| `POST /v1/sessions/{id}/end` | `Score{aura, logic, emotion, professionalism, goal_achieve, highlights, failures, result∈{shenfeng,guolu,fanche}}` + `weakness_updates[]` | Same | ✅ |
| `POST /v1/moderation/check` | `{content, context, user_id, session_id?}` → `{verdict, categories, score, redirect_resource?, trace_id}` | Same | ✅ |

No field drift. B's hand-written `types.ts` matches A's Pydantic models
1-to-1 today; we should switch to OpenAPI-generated types before any
field gets added on either side (tracked separately — not in scope here).

## Drift found and fixed in this PR

**B's MSW handlers used path-only patterns (`/v1/...`).** MSW v2's
node setup (which `vitest` uses) does *not* match relative patterns
against fully-qualified URLs — a real bug for any built EXE that
hits a real origin in production. Fix: prefix every handler with
`*` so the same pattern matches relative dev fetches AND any
fully-qualified URL.

```diff
- const BASE = '/v1'
+ const BASE = '*/v1'
```

This change is invisible in browser dev (MSW matches both shapes
there) but unblocks node-level testing and production runtime.

## Smoke run — `pnpm --filter web test`

10 vitest assertions running against MSW via `msw/node`:

- `GET /v1/scenarios` returns the right shape, including the category
  filter
- `POST /v1/sessions` returns a `ses_*` session id + non-empty opening line
- `POST /v1/sessions/:id/turns` SSE stream:
  - emits `opponent.delta+ → opponent.done → coach.hint → meta` in
    that order, with the right counts
  - per-frame `data` shape matches A's Pydantic models
  - concatenated `opponent.delta.text` equals `opponent.done.full_text`
- `POST /v1/sessions/:id/end` returns a `Score` with all 5 rating
  fields in [0,10] + a `result` ∈ the three-literal union
- `POST /v1/moderation/check`: benign content passes, the keyword
  list in B's mock triggers a `block` verdict on red-line content,
  `verdict` is always one of the four enum values

All 10 pass on `pnpm --filter web test` (and CI when it runs).

## Open items for B (FYI, not blocking)

- The mock has no separate "warn" or "redirect" branches for
  moderation. Sprint 1 fix once we have the real category sets.
- `opponent.delta` chunks are coarse (every 2 chars). Real LLM stream
  via A will be token-level; UI should not assume any particular
  chunk size.
- `EndSessionResponse` always returns the same canned `Score`. A
  later PR should derive `weakness_updates` from session history
  even in mock mode so the Wrapped card is testable.
