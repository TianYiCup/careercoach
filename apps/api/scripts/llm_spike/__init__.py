"""Sprint 0 D3 LLM spike — 30 prompt × 2 model evaluation harness.

Foundation §3.4.1 verification: LLM-judge average across all
responses must be ≥ 4/5. Run with:

    uv run python -m scripts.llm_spike.run_eval

Outputs `docs/sprint-0/llm-spike-raw.json` + `llm-spike-report.md`.
"""
