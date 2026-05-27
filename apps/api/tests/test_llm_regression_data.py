"""Offline integrity gate for the LLM regression dataset.

These tests run on every PR (they're plain pytest, no network). They
catch dataset edits that drop coverage, break the JSONL contract, or
weaken the judge parser — before the nightly live run consumes
production budget on a corpus that was never going to score.

The nightly live run lives in `.github/workflows/llm-regression.yml`;
that one needs LLM API keys and is gated by schedule + manual
dispatch, not PR triggers.
"""

from __future__ import annotations

import pytest
from scripts.llm_regression.judge_prompt import (
    DIMENSION_KEYS,
    JUDGE_SYSTEM_PROMPT,
    PASS_THRESHOLD,
    JudgeResult,
    build_judge_user_prompt,
    parse_judge_output,
)
from scripts.llm_regression.run_eval import (
    MAX_ERROR_RATE,
    SampleResult,
    Scenario,
    aggregate,
    load_scenarios,
)

# Expected dataset shape — guards against silent shrinkage.
EXPECTED_SAMPLE_COUNT = 30
EXPECTED_PERSONAS = {"P1", "P2", "P3"}
EXPECTED_SCENARIO_TITLES = {
    "催导师改论文初稿",
    "拒绝室友不合理请求",
    "实习生拒绝额外加班",
    "跨小组要资源被踢皮球",
    "面试被压薪资",
}


@pytest.fixture(scope="module")
def scenarios() -> list[Scenario]:
    return load_scenarios()


def test_dataset_has_exactly_thirty_samples(scenarios: list[Scenario]) -> None:
    assert len(scenarios) == EXPECTED_SAMPLE_COUNT


def test_every_persona_p1_p2_p3_is_represented(scenarios: list[Scenario]) -> None:
    """PRD §2 personas — losing any one means the gate stops covering
    that life stage and we'd ship a regression silently."""
    personas = {s.persona for s in scenarios}
    assert personas == EXPECTED_PERSONAS, f"personas drifted: {personas}"


def test_each_persona_has_at_least_six_samples(scenarios: list[Scenario]) -> None:
    """Per-persona floor — guards against the corpus skewing toward a
    single persona after a careless rebalance."""
    counts: dict[str, int] = {}
    for s in scenarios:
        counts[s.persona] = counts.get(s.persona, 0) + 1
    for persona, count in counts.items():
        assert count >= 6, f"{persona} only has {count} samples (floor: 6)"


def test_all_five_scenario_titles_present(scenarios: list[Scenario]) -> None:
    titles = {s.scenario_title for s in scenarios}
    assert titles == EXPECTED_SCENARIO_TITLES, f"titles drifted: {titles}"


def test_every_sample_id_is_unique(scenarios: list[Scenario]) -> None:
    ids = [s.id for s in scenarios]
    assert len(ids) == len(set(ids)), (
        f"duplicate scenario IDs: {[i for i in ids if ids.count(i) > 1]}"
    )


def test_every_sample_has_non_empty_fields(scenarios: list[Scenario]) -> None:
    """Catches typo'd JSONL rows (blank `user_prompt`, missing
    `opponent_role`, etc.) before they hit the live runner."""
    for s in scenarios:
        assert s.id, "blank id"
        assert s.persona, f"{s.id}: blank persona"
        assert s.scenario_title, f"{s.id}: blank scenario_title"
        assert s.opponent_role, f"{s.id}: blank opponent_role"
        assert s.user_prompt, f"{s.id}: blank user_prompt"
        assert isinstance(s.tags, list), f"{s.id}: tags not a list"


def test_every_sample_has_at_least_two_tags(scenarios: list[Scenario]) -> None:
    """Tags drive future per-axis breakdowns (campus vs intern vs
    fresh, authority vs peer, etc.). Anything < 2 means the entry was
    added without thinking about the slice it belongs to."""
    for s in scenarios:
        assert len(s.tags) >= 2, f"{s.id} only has {len(s.tags)} tag(s)"


def test_dimension_keys_match_expected_five() -> None:
    """If someone adds a 6th dimension to `judge_prompt` they MUST
    also bump `_aggregate()`'s explicit dimension list — this test
    catches the half-edit."""
    assert DIMENSION_KEYS == (
        "RELEVANCE",
        "AUTHENTICITY",
        "NATURALNESS",
        "IN_CHARACTER",
        "LENGTH",
    )


def test_pass_threshold_is_within_expected_band() -> None:
    """Foundation §3.4.1 fixes the bar at 4.0/5. Anything outside
    [3.5, 4.5] is almost certainly a typo or a covert weakening of
    the gate."""
    assert 3.5 <= PASS_THRESHOLD <= 4.5


def test_max_error_rate_is_within_expected_band() -> None:
    """Same shape of guard for the error-rate ceiling. >10 % means
    the gate basically can't fail on flakiness; <1 % is too tight to
    survive a single transient 5xx in a 30-call run."""
    assert 0.01 <= MAX_ERROR_RATE <= 0.10


def test_judge_system_prompt_mentions_all_five_dimensions() -> None:
    """Drift between the prompt text and the parser's regex would
    silently neutral-score every call. Pin the contract."""
    for key in DIMENSION_KEYS:
        assert key in JUDGE_SYSTEM_PROMPT, f"prompt missing {key}"


@pytest.mark.parametrize(
    ("raw", "expected_average"),
    [
        # All 5s — top mark.
        ("RELEVANCE: 5\nAUTHENTICITY: 5\nNATURALNESS: 5\nIN_CHARACTER: 5\nLENGTH: 5", 5.0),
        # Average = 4.0 (the pass threshold).
        ("RELEVANCE: 4\nAUTHENTICITY: 4\nNATURALNESS: 4\nIN_CHARACTER: 4\nLENGTH: 4", 4.0),
        # Mixed — the corpus we actually expect.
        ("RELEVANCE: 5\nAUTHENTICITY: 4\nNATURALNESS: 5\nIN_CHARACTER: 4\nLENGTH: 3", 4.2),
    ],
)
def test_parse_judge_output_happy_path(raw: str, expected_average: float) -> None:
    result = parse_judge_output(raw)
    assert result.average == pytest.approx(expected_average)
    assert result.parsed_all_dimensions is True


def test_parse_judge_output_tolerates_whitespace_case_and_chinese_colon() -> None:
    """Vendors sometimes return zh full-width colon or odd spacing."""
    raw = "  relevance ：4\n  AUTHENTICITY:  3\nnaturalness: 5\nIN_CHARACTER ： 4\nLENGTH:5"
    result = parse_judge_output(raw)
    assert result.scores == {
        "RELEVANCE": 4,
        "AUTHENTICITY": 3,
        "NATURALNESS": 5,
        "IN_CHARACTER": 4,
        "LENGTH": 5,
    }
    assert result.parsed_all_dimensions is True


def test_parse_judge_output_clamps_out_of_range_scores() -> None:
    """Vendors occasionally emit 0 or 9 despite the prompt. Clamp to
    [1, 5] so the aggregate stays bounded."""
    # The parser regex only matches a single digit, so we use 1 / 9.
    raw = "RELEVANCE: 9\nAUTHENTICITY: 1\nNATURALNESS: 0\nIN_CHARACTER: 3\nLENGTH: 4"
    result = parse_judge_output(raw)
    # 9 → 5, 0 → 1, 1 stays 1, others as-is.
    assert result.scores["RELEVANCE"] == 5
    assert result.scores["AUTHENTICITY"] == 1
    assert result.scores["NATURALNESS"] == 1


def test_parse_judge_output_fills_missing_dimensions_with_neutral_three() -> None:
    """A single missing line shouldn't crash the run, but it MUST be
    flagged via parsed_all_dimensions so the runner can count drift."""
    raw = "RELEVANCE: 5\nAUTHENTICITY: 5\nNATURALNESS: 5"  # missing IN_CHARACTER + LENGTH
    result = parse_judge_output(raw)
    assert result.scores["IN_CHARACTER"] == 3
    assert result.scores["LENGTH"] == 3
    assert result.parsed_all_dimensions is False


def test_parse_judge_output_returns_empty_note_when_absent() -> None:
    raw = "RELEVANCE: 5\nAUTHENTICITY: 5\nNATURALNESS: 5\nIN_CHARACTER: 5\nLENGTH: 5"
    assert parse_judge_output(raw).note == ""


def test_parse_judge_output_extracts_note_line() -> None:
    raw = (
        "RELEVANCE: 5\nAUTHENTICITY: 5\nNATURALNESS: 5\nIN_CHARACTER: 5\nLENGTH: 5\n"
        "NOTE: 回应直接, 角色稳, 略啰嗦半句。"
    )
    assert parse_judge_output(raw).note == "回应直接, 角色稳, 略啰嗦半句。"


def test_build_judge_user_prompt_includes_all_four_fields() -> None:
    out = build_judge_user_prompt(
        scenario_title="测试场景",
        opponent_role="测试角色",
        user_prompt="测试用户输入",
        model_response="测试模型输出",
    )
    assert "测试场景" in out
    assert "测试角色" in out
    assert "测试用户输入" in out
    assert "测试模型输出" in out
    assert "请按规则评分" in out


# ----- aggregate() coverage -----


def _make_sample(
    *,
    model: str,
    scenario_id: str = "x.p1",
    judge_average: float | None,
    judge_scores: dict[str, int] | None = None,
    error: str | None = None,
    parsed_all: bool = True,
) -> SampleResult:
    return SampleResult(
        scenario_id=scenario_id,
        persona="P1",
        scenario_title="x",
        tags=["t"],
        user_prompt="u",
        model=model,
        response="r" if error is None else "",
        latency_s=0.1,
        judge_scores=judge_scores
        or ({k: int(judge_average or 0) for k in DIMENSION_KEYS} if judge_average else None),
        judge_average=judge_average,
        judge_note="",
        judge_parsed_all=parsed_all,
        error=error,
    )


def test_aggregate_marks_model_pass_when_average_meets_threshold() -> None:
    rows = [_make_sample(model="deepseek", judge_average=4.2) for _ in range(10)]
    summaries = aggregate(rows, ["deepseek"])
    s = summaries["deepseek"]
    assert s.count == 10
    assert s.error_count == 0
    assert s.average == pytest.approx(4.2)
    assert s.passes_threshold is True
    assert s.passes_error_rate is True


def test_aggregate_marks_model_fail_when_average_below_threshold() -> None:
    rows = [_make_sample(model="qwen", judge_average=3.5) for _ in range(10)]
    summaries = aggregate(rows, ["qwen"])
    assert summaries["qwen"].passes_threshold is False


def test_aggregate_marks_model_fail_when_error_rate_exceeds_ceiling() -> None:
    """1 in 10 errors = 10 % > 5 % ceiling — fails even if scored
    rows passed."""
    rows = [_make_sample(model="deepseek", judge_average=5.0) for _ in range(9)]
    rows.append(_make_sample(model="deepseek", judge_average=None, error="boom"))
    summaries = aggregate(rows, ["deepseek"])
    s = summaries["deepseek"]
    assert s.error_count == 1
    assert s.error_rate == pytest.approx(0.10)
    assert s.passes_error_rate is False


def test_aggregate_counts_unparseable_judge_responses() -> None:
    rows = [
        _make_sample(model="deepseek", judge_average=4.0, parsed_all=True),
        _make_sample(model="deepseek", judge_average=4.0, parsed_all=False),
    ]
    summaries = aggregate(rows, ["deepseek"])
    assert summaries["deepseek"].judge_unparseable_count == 1


def test_aggregate_returns_zeroed_summary_when_model_had_no_results() -> None:
    summaries = aggregate([], ["deepseek", "qwen"])
    for name in ("deepseek", "qwen"):
        s = summaries[name]
        assert s.count == 0
        assert s.average is None
        assert s.passes_threshold is False


def test_judge_result_average_is_arithmetic_mean() -> None:
    result = JudgeResult(
        scores={
            "RELEVANCE": 5,
            "AUTHENTICITY": 4,
            "NATURALNESS": 3,
            "IN_CHARACTER": 2,
            "LENGTH": 1,
        },
        note="",
        parsed_all_dimensions=True,
    )
    assert result.average == pytest.approx(3.0)
