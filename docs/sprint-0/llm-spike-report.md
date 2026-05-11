# Sprint 0 · D3 LLM Spike Report

- Generated: `2026-05-11T12:33:23+00:00`
- Models evaluated: deepseek, qwen
- Scenarios × prompts: 5 × 6 = **60 calls**
- Pass gate (foundation §3.4.1): average ≥ **4.0/5**

## Verdict

- **deepseek**: avg = `4.967` over 30 responses — ✅ PASS
- **qwen**: avg = `4.842` over 24 responses — ✅ PASS

## Per-dimension averages

| Model | RELEVANCE | AUTHENTICITY | NATURALNESS | IN_CHARACTER | LENGTH |
|---|---|---|---|---|---|
| deepseek | 5.0 | 4.933 | 5.0 | 5.0 | 4.9 |
| qwen | 4.958 | 4.625 | 4.917 | 4.958 | 4.75 |

## Per-scenario breakdown

### `campus.tutor.thesis` · 催导师改论文初稿 (P1)

- **deepseek**: avg `4.93` over 6 prompts.
- **qwen**: avg `4.83` over 6 prompts.

### `campus.roommate.boundary` · 拒绝室友不合理请求 (P1)

- **deepseek**: avg `4.97` over 6 prompts.
- **qwen**: avg `4.83` over 6 prompts.

### `intern.overtime.refuse` · 实习生拒绝额外加班 (P2)

- **deepseek**: avg `4.97` over 6 prompts.
- **qwen**: avg `4.73` over 6 prompts.

### `intern.resource.crossteam` · 跨小组要资源被踢皮球 (P2)

- **deepseek**: avg `5.00` over 6 prompts.
- **qwen**: avg `4.97` over 6 prompts.

### `fresh.interview.salary` · 面试被压薪资 (P3)

- **deepseek**: avg `4.97` over 6 prompts.
- **qwen**: skipped.

## Notes

- Judge model: DeepSeek (self-evaluating; flagged in §3.7.1 follow-ups).
- Raw data: [`llm-spike-raw.json`](./llm-spike-raw.json)
- Methodology: 5 PRD §2 scenarios × 6 user prompts; per response the
  judge rates 5 dimensions 1–5 and we average to one score.
