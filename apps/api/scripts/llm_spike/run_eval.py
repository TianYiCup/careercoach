"""Sprint 0 D3 spike runner — generates one report from the live models.

For each scenario × each user prompt × each configured model:
  1. Build a roleplay-style prompt and stream a response from the
     model via the app's own DeepSeek/Qwen adapters.
  2. Hand the (prompt, response) pair to a judge LLM (DeepSeek, since
     it's required for the spike anyway) and parse its 5-dim rating.

Writes:
  * docs/sprint-0/llm-spike-raw.json  — full record, reproducible.
  * docs/sprint-0/llm-spike-report.md — human-readable summary.

Run with:
    uv run python -m scripts.llm_spike.run_eval
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from app.agents._stream import stream_to_text
from app.config import get_settings
from app.llm import DeepSeekProvider, LLMProvider, Message, QwenProvider
from scripts.llm_spike.judge_prompt import (
    JUDGE_SYSTEM_PROMPT,
    JudgeResult,
    build_judge_user_prompt,
    parse_judge_output,
)

_SCENARIOS_PATH = Path(__file__).parent / "scenarios.json"
# scripts/llm_spike/run_eval.py  →  parents[0]=llm_spike, [1]=scripts,
# [2]=apps/api, [3]=apps, [4]=<repo root>.
_REPORT_DIR = Path(__file__).resolve().parents[4] / "docs" / "sprint-0"
_RAW_PATH = _REPORT_DIR / "llm-spike-raw.json"
_REPORT_PATH = _REPORT_DIR / "llm-spike-report.md"

# Foundation §3.4.1 gate.
PASS_THRESHOLD = 4.0

logging.basicConfig(level=logging.INFO, format="%(message)s")
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger(__name__)


def _load_scenarios() -> list[dict[str, Any]]:
    payload = json.loads(_SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    return scenarios


def _configured_models() -> list[tuple[str, LLMProvider]]:
    settings = get_settings()
    models: list[tuple[str, LLMProvider]] = []

    deepseek_key = settings.deepseek_api_key.get_secret_value()
    if deepseek_key:
        models.append(
            (
                "deepseek",
                DeepSeekProvider(
                    api_key=deepseek_key,
                    model=settings.deepseek_model,
                    base_url=settings.deepseek_base_url,
                ),
            )
        )
    else:
        logger.warning("model_skipped", model="deepseek", reason="missing_key")

    qwen_key = settings.qwen_api_key.get_secret_value()
    if qwen_key:
        models.append(
            (
                "qwen",
                QwenProvider(
                    api_key=qwen_key,
                    model=settings.qwen_model,
                    base_url=settings.qwen_base_url,
                ),
            )
        )
    else:
        logger.warning("model_skipped", model="qwen", reason="missing_key")

    if not models:
        raise SystemExit("No model keys configured. Set DEEPSEEK_API_KEY or QWEN_API_KEY in .env.")

    return models


async def _generate(
    provider: LLMProvider,
    *,
    opponent_role: str,
    user_prompt: str,
) -> tuple[str, float]:
    """Run one roleplay generation and return (text, latency_seconds)."""
    messages = [Message.system(opponent_role), Message.user(user_prompt)]
    start = time.perf_counter()
    text = await stream_to_text(provider.stream_chat(messages, temperature=0.7))
    return text.strip(), time.perf_counter() - start


async def _judge(
    judge_provider: LLMProvider,
    *,
    scenario_title: str,
    opponent_role: str,
    user_prompt: str,
    model_response: str,
) -> JudgeResult:
    user_payload = build_judge_user_prompt(
        scenario_title=scenario_title,
        opponent_role=opponent_role,
        user_prompt=user_prompt,
        model_response=model_response,
    )
    messages = [
        Message.system(JUDGE_SYSTEM_PROMPT),
        Message.user(user_payload),
    ]
    raw = await stream_to_text(judge_provider.stream_chat(messages, temperature=0.0))
    return parse_judge_output(raw)


async def _evaluate(
    scenarios: list[dict[str, Any]],
    models: list[tuple[str, LLMProvider]],
    judge_provider: LLMProvider,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        scenario_id = scenario["id"]
        opponent_role = scenario["opponent_role"]
        for prompt_idx, user_prompt in enumerate(scenario["prompts"]):
            for model_name, provider in models:
                logger.info(
                    "eval_call",
                    scenario=scenario_id,
                    prompt=prompt_idx + 1,
                    model=model_name,
                )
                try:
                    response, latency = await _generate(
                        provider,
                        opponent_role=opponent_role,
                        user_prompt=user_prompt,
                    )
                except Exception as exc:
                    logger.error(
                        "eval_call_failed",
                        scenario=scenario_id,
                        prompt=prompt_idx + 1,
                        model=model_name,
                        error=str(exc),
                    )
                    results.append(
                        {
                            "scenario_id": scenario_id,
                            "scenario_title": scenario["title"],
                            "persona": scenario["persona"],
                            "prompt_index": prompt_idx + 1,
                            "user_prompt": user_prompt,
                            "model": model_name,
                            "response": "",
                            "latency_s": 0.0,
                            "judge": None,
                            "error": str(exc),
                        }
                    )
                    continue

                judge_result = await _judge(
                    judge_provider,
                    scenario_title=scenario["title"],
                    opponent_role=opponent_role,
                    user_prompt=user_prompt,
                    model_response=response,
                )
                results.append(
                    {
                        "scenario_id": scenario_id,
                        "scenario_title": scenario["title"],
                        "persona": scenario["persona"],
                        "prompt_index": prompt_idx + 1,
                        "user_prompt": user_prompt,
                        "model": model_name,
                        "response": response,
                        "latency_s": round(latency, 3),
                        "judge": {
                            "scores": judge_result.scores,
                            "average": round(judge_result.average, 3),
                            "note": judge_result.note,
                        },
                        "error": None,
                    }
                )
    return results


def _aggregate(results: list[dict[str, Any]], models: list[str]) -> dict[str, dict[str, Any]]:
    """Compute per-model averages and per-dimension averages."""
    out: dict[str, dict[str, Any]] = {}
    for model in models:
        all_rows = [r for r in results if r["model"] == model]
        rows = [r for r in all_rows if r["judge"] is not None]
        errors = [r for r in all_rows if r["error"] is not None]
        if not rows:
            out[model] = {
                "count": 0,
                "error_count": len(errors),
                "average": None,
                "by_dimension": None,
            }
            continue
        avg = sum(r["judge"]["average"] for r in rows) / len(rows)
        dims: dict[str, float] = {}
        for dim in (
            "RELEVANCE",
            "AUTHENTICITY",
            "NATURALNESS",
            "IN_CHARACTER",
            "LENGTH",
        ):
            dims[dim] = round(sum(r["judge"]["scores"][dim] for r in rows) / len(rows), 3)
        out[model] = {
            "count": len(rows),
            "error_count": len(errors),
            "average": round(avg, 3),
            "by_dimension": dims,
            "pass": avg >= PASS_THRESHOLD,
        }
    return out


def _write_report(
    *,
    results: list[dict[str, Any]],
    summary: dict[str, dict[str, Any]],
    models: list[str],
    started_at: str,
) -> None:
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    _RAW_PATH.write_text(
        json.dumps(
            {
                "version": "0.1",
                "started_at": started_at,
                "models": models,
                "pass_threshold": PASS_THRESHOLD,
                "summary": summary,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines: list[str] = []
    lines.append("# Sprint 0 · D3 LLM Spike Report")
    lines.append("")
    lines.append(f"- Generated: `{started_at}`")
    lines.append(f"- Models evaluated: {', '.join(models)}")
    lines.append(f"- Scenarios × prompts: 5 × 6 = **{len(results)} calls**")
    lines.append(f"- Pass gate (foundation §3.4.1): average ≥ **{PASS_THRESHOLD}/5**")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    for model in models:
        s = summary.get(model)
        if s is None or s["count"] == 0:
            errs = (s or {}).get("error_count", 0)
            reason = f"all {errs} calls failed (auth or upstream)" if errs else "no key configured"
            lines.append(f"- **{model}**: not evaluated — {reason}.")
            continue
        marker = "✅ PASS" if s["pass"] else "❌ FAIL"
        lines.append(
            f"- **{model}**: avg = `{s['average']}` over {s['count']} responses — {marker}"
        )
    lines.append("")
    lines.append("## Per-dimension averages")
    lines.append("")
    lines.append("| Model | RELEVANCE | AUTHENTICITY | NATURALNESS | IN_CHARACTER | LENGTH |")
    lines.append("|---|---|---|---|---|---|")
    for model in models:
        s = summary.get(model)
        if s is None or s["count"] == 0:
            lines.append(f"| {model} | — | — | — | — | — |")
            continue
        d = s["by_dimension"]
        lines.append(
            f"| {model} | {d['RELEVANCE']} | {d['AUTHENTICITY']} | "
            f"{d['NATURALNESS']} | {d['IN_CHARACTER']} | {d['LENGTH']} |"
        )
    lines.append("")
    lines.append("## Per-scenario breakdown")
    lines.append("")
    scenarios_seen: list[str] = []
    for r in results:
        if r["scenario_id"] not in scenarios_seen:
            scenarios_seen.append(r["scenario_id"])
    for sid in scenarios_seen:
        rows = [r for r in results if r["scenario_id"] == sid]
        title = rows[0]["scenario_title"]
        persona = rows[0]["persona"]
        lines.append(f"### `{sid}` · {title} ({persona})")
        lines.append("")
        for model in models:
            ms = [r for r in rows if r["model"] == model and r["judge"] is not None]
            if not ms:
                lines.append(f"- **{model}**: skipped.")
                continue
            avg = sum(m["judge"]["average"] for m in ms) / len(ms)
            lines.append(f"- **{model}**: avg `{avg:.2f}` over {len(ms)} prompts.")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("- Judge model: DeepSeek (self-evaluating; flagged in §3.7.1 follow-ups).")
    lines.append("- Raw data: [`llm-spike-raw.json`](./llm-spike-raw.json)")
    lines.append("- Methodology: 5 PRD §2 scenarios × 6 user prompts; per response the")
    lines.append("  judge rates 5 dimensions 1–5 and we average to one score.")
    _REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> int:
    scenarios = _load_scenarios()
    models = _configured_models()
    model_names = [name for name, _ in models]

    judge_provider: LLMProvider | None = next((p for name, p in models if name == "deepseek"), None)
    if judge_provider is None:
        # No DeepSeek key configured — judging falls back to the first
        # configured model, which we report transparently.
        logger.warning("judge_fallback", reason="no_deepseek_key")
        judge_provider = models[0][1]

    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    logger.info("eval_start", models=model_names, started_at=started_at)

    try:
        results = await _evaluate(scenarios, models, judge_provider)
    finally:
        for _, provider in models:
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()

    summary = _aggregate(results, model_names)
    _write_report(
        results=results,
        summary=summary,
        models=model_names,
        started_at=started_at,
    )
    logger.info("eval_done", summary=summary, raw=str(_RAW_PATH), report=str(_REPORT_PATH))

    # Exit code mirrors the gate so CI integrations can fail loudly.
    failed = any(s["count"] and not s["pass"] for s in summary.values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
