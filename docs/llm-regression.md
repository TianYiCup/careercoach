# LLM Regression Gate

Nightly quality check for the LangGraph chain. 30 scenarios × every
configured model, each response scored 1-5 on 5 dimensions by an
LLM-as-judge. Surfaces prompt drift, model swaps, and upstream
behaviour changes before they reach users.

Foundation §3.4.1 fixes the bar: corpus average per model ≥ **4.0/5**.

---

## What runs where

| Trigger | Where | Cost | Purpose |
|---|---|---|---|
| Every PR | `ci.yml` → `test-api` → `test_llm_regression_data.py` | $0 (offline) | Dataset shape, parser contract, threshold band |
| Nightly + manual | `llm-regression.yml` → `scripts.llm_regression.run_eval` | ~$0.15/run | Live model scoring, judge averages, per-dim breakdown |

The two layers are intentional: the PR-time pytest catches careless
dataset edits (missing field, dropped persona, weakened threshold)
without spending a cent; the nightly catches behaviour changes that
only show up against real models.

---

## Dataset

`apps/api/scripts/llm_regression/scenarios.jsonl` — 30 rows, one per
`(scenario, user_prompt)` pair. Sourced from PRD §2 personas:

| Persona | Scenarios | Sample count |
|---|---|---|
| P1 (在校大学生) | 催导师改论文初稿 · 拒绝室友不合理请求 | 12 |
| P2 (实习生) | 实习生拒绝额外加班 · 跨小组要资源被踢皮球 | 12 |
| P3 (应届毕业生) | 面试被压薪资 | 6 |

Each row carries `tags` (e.g. `["campus", "authority", "polite_push"]`)
so future per-axis breakdowns (authority vs peer, urgency vs concession)
are queryable without re-tagging the corpus.

### Adding a sample

```jsonl
{"id": "<topic>.<situation>.p<n>", "persona": "P1|P2|P3", "scenario_title": "...", "tags": ["...", "..."], "opponent_role": "...", "user_prompt": "..."}
```

Then run `pnpm --filter api test tests/test_llm_regression_data.py`
(or the uv equivalent — see below) to confirm the integrity gate is
still happy. Bump `EXPECTED_SAMPLE_COUNT` in the test file at the
same time, otherwise the `test_dataset_has_exactly_thirty_samples`
assertion will fail loud.

---

## Running locally

```bash
cd apps/api

# offline gate — runs in <2s, no API calls
uv run pytest tests/test_llm_regression_data.py -q

# live run — needs DEEPSEEK_API_KEY and/or QWEN_API_KEY in .env
uv run python -m scripts.llm_regression.run_eval

# single-model run (handy when iterating one adapter)
uv run python -m scripts.llm_regression.run_eval --models deepseek

# custom output path (default: docs/llm-regression-report.json)
uv run python -m scripts.llm_regression.run_eval --output /tmp/r.json
```

Exit code is non-zero when any configured model:
- falls below the 4.0/5 corpus average, or
- has more than 5 % failed calls (network/upstream errors).

A failing exit code surfaces in the GitHub Actions step summary and
shows up red on the workflow run — no separate alerting wiring needed.

---

## Reading the report

```json
{
  "version": "1.0",
  "started_at": "2026-05-27T03:00:00+00:00",
  "scenario_count": 30,
  "models": ["deepseek", "qwen"],
  "pass_threshold": 4.0,
  "max_error_rate": 0.05,
  "summary": {
    "deepseek": {
      "count": 30,
      "error_count": 0,
      "error_rate": 0.0,
      "average": 4.3,
      "by_dimension": { "RELEVANCE": 4.5, "AUTHENTICITY": 4.1, ... },
      "judge_unparseable_count": 0,
      "passes_threshold": true,
      "passes_error_rate": true
    }
  },
  "results": [ /* 30 rows × N models */ ]
}
```

Inspect `by_dimension` first — a single low dimension is more
actionable than "the average dropped". Common patterns we've seen
on similar harnesses:

- `IN_CHARACTER` low → the prompt's "不要破坏角色" clause is being
  ignored by the model. Check whether the system prompt grew too
  long and pushed that clause out of attention.
- `LENGTH` low → vendor pushed an over-talkative model variant. Open
  the per-sample `response` field to confirm; if every response is
  120+ chars where the prompt asked for ≤ 60, that's a model swap.
- `AUTHENTICITY` low → the model is in helpful-assistant mode
  ("我建议你这样回复…"). Likely a safety-RLHF regression on the
  vendor side; file with their support and fall over to the alt
  provider via `LLMRouter`.

---

## Repo secrets required

Set in `Settings → Secrets and variables → Actions`:

- `DEEPSEEK_API_KEY` — required for the default judge configuration
- `QWEN_API_KEY` — required if you want the failover provider scored
  too (workflow won't error if missing, just skips that model and
  logs `model_skipped`)

When neither is set, the workflow fails on `run-regression` with
`no LLM keys configured` — visible directly in the step log.

---

## Cost notes

Each call is one generation (≤ 70 tokens out) + one judge call
(≤ 60 tokens out). With both DeepSeek and Qwen configured that's
30 × 2 generations + 30 × 1 judge = 90 model calls per run.

Rough cost on the configured `deepseek-chat` + `qwen-max` pricing
(May 2026): **~$0.15 per run**, or ~$5/month at the nightly cadence.
Workflow dispatch runs are free-ish to invoke ad-hoc; just don't
script a loop.

---

## When this gate fails

1. Pull the artifact: `gh run download <run_id> -n llm-regression-report-<run_id>`
2. Open `llm-regression-report.json`, look at `summary.<model>.by_dimension`
3. Find the lowest-dimension samples: `jq '.results | sort_by(.judge_average) | .[0:5]' report.json`
4. Decide:
   - Vendor regression → file with vendor, route around via
     `LLMRouter` config until they fix it
   - Our prompt regression → revert the offending agent prompt PR
     and re-evaluate locally with `uv run python -m scripts.llm_regression.run_eval`
   - Dataset is wrong (model is actually correct, judge is being
     too strict) → adjust the relevant `opponent_role` to remove
     ambiguity, then re-run the local eval to confirm

Don't loosen `PASS_THRESHOLD` or `MAX_ERROR_RATE` to make a run pass.
The data-integrity pytest pins these to a tight band (`3.5 ≤ pt ≤
4.5`, `0.01 ≤ err ≤ 0.10`) on purpose — anything outside is a covert
weakening of the gate.
