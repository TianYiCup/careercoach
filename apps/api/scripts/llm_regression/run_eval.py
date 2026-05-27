"""Nightly LLM regression evaluation.

Runs the 30-scenario corpus against every configured LLM adapter,
scores each response with the LLM-as-judge prompt in `judge_prompt`,
and writes a JSON report. Exits non-zero when:

  * any configured model's corpus-average score < `PASS_THRESHOLD`, or
  * any model's error rate > `MAX_ERROR_RATE`.

Run with:
    uv run python -m scripts.llm_regression.run_eval
    uv run python -m scripts.llm_regression.run_eval --models deepseek
    uv run python -m scripts.llm_regression.run_eval --output report.json

CI runs it from `.github/workflows/llm-regression.yml` on a nightly
cron + manual workflow_dispatch.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from app.agents._stream import stream_to_text
from app.config import get_settings
from app.llm import DeepSeekProvider, LLMProvider, Message, QwenProvider
from scripts.llm_regression.judge_prompt import (
    JUDGE_SYSTEM_PROMPT,
    PASS_THRESHOLD,
    JudgeResult,
    build_judge_user_prompt,
    parse_judge_output,
)

# Hosting note: when CI invokes this script, GitHub Actions caches the
# repo on a fresh runner so the resolved path lands at
# `<runner>/apps/api/scripts/llm_regression/scenarios.jsonl`. We use
# parents[0] (the llm_regression dir) — robust to apps/api being moved
# in the future because the data sits next to its loader.
_SCENARIOS_PATH = Path(__file__).parent / "scenarios.jsonl"
_DEFAULT_OUTPUT = Path(__file__).resolve().parents[4] / "docs" / "llm-regression-report.json"

# Total budget per call: roleplay generation + judge call. Production
# adapters default to 8s; the regression run can tolerate a longer
# tail because we're not user-facing here.
GEN_TEMPERATURE = 0.7
JUDGE_TEMPERATURE = 0.0
GEN_TIMEOUT_S = 20.0
JUDGE_TIMEOUT_S = 30.0

# Hard ceiling on the share of failed calls per model. A handful of
# transient 5xx is acceptable; >5 % means the upstream is genuinely
# degraded and the corpus average becomes unreliable.
MAX_ERROR_RATE: float = 0.05

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


@dataclass(frozen=True)
class Scenario:
    """One row of `scenarios.jsonl`."""

    id: str
    persona: str
    scenario_title: str
    tags: list[str]
    opponent_role: str
    user_prompt: str


@dataclass(frozen=True)
class SampleResult:
    """One (scenario × model) eval row."""

    scenario_id: str
    persona: str
    scenario_title: str
    tags: list[str]
    user_prompt: str
    model: str
    response: str
    latency_s: float
    judge_scores: dict[str, int] | None
    judge_average: float | None
    judge_note: str
    judge_parsed_all: bool
    error: str | None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelSummary:
    model: str
    count: int
    error_count: int
    error_rate: float
    average: float | None
    by_dimension: dict[str, float] | None
    judge_unparseable_count: int
    passes_threshold: bool
    passes_error_rate: bool

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def load_scenarios(path: Path = _SCENARIOS_PATH) -> list[Scenario]:
    """Parse the JSONL dataset.

    Public so the data-integrity pytest can validate the corpus
    without spinning up any LLM clients.
    """
    rows: list[Scenario] = []
    raw = path.read_text(encoding="utf-8")
    for line_num, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"scenarios.jsonl line {line_num} is not valid JSON: {exc}") from exc
        rows.append(
            Scenario(
                id=obj["id"],
                persona=obj["persona"],
                scenario_title=obj["scenario_title"],
                tags=list(obj.get("tags", [])),
                opponent_role=obj["opponent_role"],
                user_prompt=obj["user_prompt"],
            )
        )
    return rows


def _configured_providers(filter_names: set[str] | None) -> list[tuple[str, LLMProvider]]:
    """Build provider instances for every adapter with a non-empty key.

    `filter_names` (when supplied via --models) restricts the set to a
    subset; an unknown name raises so a typo on the command line
    doesn't silently skip a model.
    """
    settings = get_settings()
    out: list[tuple[str, LLMProvider]] = []

    deepseek_key = settings.deepseek_api_key.get_secret_value()
    if deepseek_key and (filter_names is None or "deepseek" in filter_names):
        out.append(
            (
                "deepseek",
                DeepSeekProvider(
                    api_key=deepseek_key,
                    model=settings.deepseek_model,
                    base_url=settings.deepseek_base_url,
                ),
            )
        )
    elif filter_names is None:
        logger.warning("model_skipped", model="deepseek", reason="missing_key")

    qwen_key = settings.qwen_api_key.get_secret_value()
    if qwen_key and (filter_names is None or "qwen" in filter_names):
        out.append(
            (
                "qwen",
                QwenProvider(
                    api_key=qwen_key,
                    model=settings.qwen_model,
                    base_url=settings.qwen_base_url,
                ),
            )
        )
    elif filter_names is None:
        logger.warning("model_skipped", model="qwen", reason="missing_key")

    if filter_names is not None:
        unknown = filter_names - {"deepseek", "qwen"}
        if unknown:
            raise SystemExit(f"unknown model name(s) in --models: {sorted(unknown)}")

    if not out:
        raise SystemExit("no LLM keys configured. Set DEEPSEEK_API_KEY or QWEN_API_KEY in .env.")

    return out


async def _generate(
    provider: LLMProvider,
    *,
    scenario: Scenario,
) -> tuple[str, float]:
    """Run one roleplay generation and return (text, latency_seconds)."""
    messages = [Message.system(scenario.opponent_role), Message.user(scenario.user_prompt)]
    start = time.perf_counter()
    text = await stream_to_text(
        provider.stream_chat(messages, temperature=GEN_TEMPERATURE, timeout=GEN_TIMEOUT_S)
    )
    return text.strip(), time.perf_counter() - start


async def _judge_call(
    judge_provider: LLMProvider,
    *,
    scenario: Scenario,
    model_response: str,
) -> JudgeResult:
    user_payload = build_judge_user_prompt(
        scenario_title=scenario.scenario_title,
        opponent_role=scenario.opponent_role,
        user_prompt=scenario.user_prompt,
        model_response=model_response,
    )
    messages = [Message.system(JUDGE_SYSTEM_PROMPT), Message.user(user_payload)]
    raw = await stream_to_text(
        judge_provider.stream_chat(messages, temperature=JUDGE_TEMPERATURE, timeout=JUDGE_TIMEOUT_S)
    )
    return parse_judge_output(raw)


async def _eval_one(
    scenario: Scenario,
    *,
    model_name: str,
    provider: LLMProvider,
    judge_provider: LLMProvider,
) -> SampleResult:
    logger.info("eval_call", scenario=scenario.id, model=model_name)
    try:
        response, latency = await _generate(provider, scenario=scenario)
    except Exception as exc:
        logger.error(
            "eval_generation_failed", scenario=scenario.id, model=model_name, error=str(exc)
        )
        return SampleResult(
            scenario_id=scenario.id,
            persona=scenario.persona,
            scenario_title=scenario.scenario_title,
            tags=scenario.tags,
            user_prompt=scenario.user_prompt,
            model=model_name,
            response="",
            latency_s=0.0,
            judge_scores=None,
            judge_average=None,
            judge_note="",
            judge_parsed_all=False,
            error=f"generation: {exc}",
        )

    try:
        judge = await _judge_call(judge_provider, scenario=scenario, model_response=response)
    except Exception as exc:
        logger.error("eval_judge_failed", scenario=scenario.id, model=model_name, error=str(exc))
        return SampleResult(
            scenario_id=scenario.id,
            persona=scenario.persona,
            scenario_title=scenario.scenario_title,
            tags=scenario.tags,
            user_prompt=scenario.user_prompt,
            model=model_name,
            response=response,
            latency_s=round(latency, 3),
            judge_scores=None,
            judge_average=None,
            judge_note="",
            judge_parsed_all=False,
            error=f"judge: {exc}",
        )

    return SampleResult(
        scenario_id=scenario.id,
        persona=scenario.persona,
        scenario_title=scenario.scenario_title,
        tags=scenario.tags,
        user_prompt=scenario.user_prompt,
        model=model_name,
        response=response,
        latency_s=round(latency, 3),
        judge_scores=dict(judge.scores),
        judge_average=round(judge.average, 3),
        judge_note=judge.note,
        judge_parsed_all=judge.parsed_all_dimensions,
        error=None,
    )


def aggregate(results: Iterable[SampleResult], models: list[str]) -> dict[str, ModelSummary]:
    """Compute per-model corpus average + pass flags."""
    out: dict[str, ModelSummary] = {}
    for model in models:
        rows = [r for r in results if r.model == model]
        if not rows:
            out[model] = ModelSummary(
                model=model,
                count=0,
                error_count=0,
                error_rate=0.0,
                average=None,
                by_dimension=None,
                judge_unparseable_count=0,
                passes_threshold=False,
                passes_error_rate=False,
            )
            continue

        errors = [r for r in rows if r.error is not None]
        scored = [r for r in rows if r.judge_average is not None]
        error_rate = len(errors) / len(rows)

        if not scored:
            out[model] = ModelSummary(
                model=model,
                count=len(rows),
                error_count=len(errors),
                error_rate=round(error_rate, 4),
                average=None,
                by_dimension=None,
                judge_unparseable_count=0,
                passes_threshold=False,
                passes_error_rate=error_rate <= MAX_ERROR_RATE,
            )
            continue

        avg = sum(r.judge_average for r in scored if r.judge_average is not None) / len(scored)
        by_dim: dict[str, float] = {}
        for dim in (
            "RELEVANCE",
            "AUTHENTICITY",
            "NATURALNESS",
            "IN_CHARACTER",
            "LENGTH",
        ):
            by_dim[dim] = round(
                sum((r.judge_scores or {}).get(dim, 0) for r in scored) / len(scored), 3
            )
        unparseable = sum(1 for r in scored if not r.judge_parsed_all)

        out[model] = ModelSummary(
            model=model,
            count=len(rows),
            error_count=len(errors),
            error_rate=round(error_rate, 4),
            average=round(avg, 3),
            by_dimension=by_dim,
            judge_unparseable_count=unparseable,
            passes_threshold=avg >= PASS_THRESHOLD,
            passes_error_rate=error_rate <= MAX_ERROR_RATE,
        )
    return out


async def _evaluate(
    scenarios: list[Scenario],
    providers: list[tuple[str, LLMProvider]],
    judge_provider: LLMProvider,
) -> list[SampleResult]:
    """Sequential per-call execution.

    We deliberately do NOT fan out across scenarios — vendor rate
    limits + the LangGraph production path are sequential too, so
    keeping the eval sequential makes the latency numbers comparable
    to the live experience.
    """
    out: list[SampleResult] = []
    for scenario in scenarios:
        for model_name, provider in providers:
            out.append(
                await _eval_one(
                    scenario,
                    model_name=model_name,
                    provider=provider,
                    judge_provider=judge_provider,
                )
            )
    return out


def _write_report(
    *,
    output_path: Path,
    started_at: str,
    finished_at: str,
    scenario_count: int,
    models: list[str],
    results: list[SampleResult],
    summaries: dict[str, ModelSummary],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "started_at": started_at,
        "finished_at": finished_at,
        "scenario_count": scenario_count,
        "models": models,
        "pass_threshold": PASS_THRESHOLD,
        "max_error_rate": MAX_ERROR_RATE,
        "summary": {name: s.to_json() for name, s in summaries.items()},
        "results": [r.to_json() for r in results],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="llm_regression",
        description="Nightly LLM regression eval. Exits non-zero on threshold breach.",
    )
    parser.add_argument(
        "--models",
        type=str,
        default=None,
        help="Comma-separated subset of model names (deepseek,qwen). Default: all configured.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Where to write the JSON report. Default: {_DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--judge",
        type=str,
        default="deepseek",
        choices=["deepseek", "qwen"],
        help="Which configured model judges responses. Default: deepseek.",
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    scenarios = load_scenarios()
    filter_names = set(args.models.split(",")) if args.models else None
    providers = _configured_providers(filter_names)
    model_names = [name for name, _ in providers]

    judge_provider = next((p for name, p in providers if name == args.judge), None)
    if judge_provider is None:
        # `--judge` wasn't in the configured set — fall back to the
        # first available and surface the swap in the log so the
        # report consumer knows judge ≠ what they asked for.
        logger.warning("judge_fallback", requested=args.judge, used=model_names[0])
        judge_provider = providers[0][1]

    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    logger.info(
        "regression_start",
        scenarios=len(scenarios),
        models=model_names,
        judge=args.judge,
        started_at=started_at,
    )

    try:
        results = await _evaluate(scenarios, providers, judge_provider)
    finally:
        for _, provider in providers:
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                await aclose()

    finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    summaries = aggregate(results, model_names)

    _write_report(
        output_path=args.output,
        started_at=started_at,
        finished_at=finished_at,
        scenario_count=len(scenarios),
        models=model_names,
        results=results,
        summaries=summaries,
    )
    logger.info("regression_done", report=str(args.output), summary=summaries)

    # Any model that fell below either gate fails the run. We don't
    # short-circuit — every model gets evaluated so the report is
    # complete even when one upstream is degraded.
    failed = False
    for name, s in summaries.items():
        if s.count == 0:
            logger.error("regression_no_results", model=name)
            failed = True
            continue
        if not s.passes_error_rate:
            logger.error(
                "regression_error_rate_exceeded",
                model=name,
                error_rate=s.error_rate,
                limit=MAX_ERROR_RATE,
            )
            failed = True
        if s.average is not None and not s.passes_threshold:
            logger.error(
                "regression_below_threshold",
                model=name,
                average=s.average,
                threshold=PASS_THRESHOLD,
            )
            failed = True
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
