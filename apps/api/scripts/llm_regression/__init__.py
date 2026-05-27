"""LLM regression evaluation harness.

Runs the 30-scenario dataset against configured LLM adapters and
scores responses via the LLM-as-judge prompt in `judge_prompt`.
Designed for nightly CI (`.github/workflows/llm-regression.yml`)
plus ad-hoc local runs (`uv run python -m scripts.llm_regression.run_eval`).

Distinct from `scripts.llm_spike` (frozen Sprint 0 artifact) — that
module's `docs/sprint-0/` output is a historical baseline we don't
re-run. This module is the recurring gate.
"""
