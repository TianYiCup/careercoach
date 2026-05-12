"""200-sample adversarial regression for the moderation dict (PR ④).

What this does
--------------
Loads `tests/sensitive_samples.csv` (150 red-line positives + 50 clean
negatives), runs each row through `DictBackend`, and asserts:

  * red-line recall ≥ 99.5 %  — every red-line sample must produce a
    `block` or `redirect` verdict;
  * false-positive rate ≤ 3 % — at most 3 % of the clean sample set may
    flip to `block` / `redirect`.

The same `evaluate_samples()` entry point is reused from a pytest gate
so CI catches dict edits that regress either number.

Run with
--------
    cd apps/api
    uv run python -m scripts.moderation_eval.run_eval

Writes `docs/sprint-0/moderation-eval-report.md` and exits 1 on
threshold breach (so the script can also gate a manual run).
"""

from __future__ import annotations

import asyncio
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.services.moderation.local_dict import DictBackend
from app.services.moderation.types import Decision

# scripts/moderation_eval/run_eval.py
# parents[0]=moderation_eval, [1]=scripts, [2]=apps/api, [3]=apps, [4]=repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SAMPLES_PATH = _REPO_ROOT / "tests" / "sensitive_samples.csv"
_REPORT_PATH = _REPO_ROOT / "docs" / "sprint-0" / "moderation-eval-report.md"

RECALL_THRESHOLD = 0.995
FP_THRESHOLD = 0.03

_FLAGGED_VERDICTS = frozenset({"block", "redirect"})


@dataclass(frozen=True)
class SampleRow:
    id: str
    content: str
    expected_verdict: str
    expected_category: str
    notes: str


@dataclass
class EvalResult:
    cat_total: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cat_hit: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    neg_total: int = 0
    neg_fp: int = 0
    # (id, content, expected_category)
    misses: list[tuple[str, str, str]] = field(default_factory=list)
    # (id, content, actual_category)
    fps: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def total_positives(self) -> int:
        return sum(self.cat_total.values())

    @property
    def total_hits(self) -> int:
        return sum(self.cat_hit.values())

    @property
    def recall(self) -> float:
        return self.total_hits / self.total_positives if self.total_positives else 0.0

    @property
    def fp_rate(self) -> float:
        return self.neg_fp / self.neg_total if self.neg_total else 0.0

    @property
    def passes(self) -> bool:
        return self.recall >= RECALL_THRESHOLD and self.fp_rate <= FP_THRESHOLD


def load_samples(path: Path = _SAMPLES_PATH) -> list[SampleRow]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [
            SampleRow(
                id=row["id"],
                content=row["content"],
                expected_verdict=row["expected_verdict"],
                expected_category=row["expected_category"],
                notes=row.get("notes", ""),
            )
            for row in reader
        ]


async def evaluate_samples(
    *,
    samples_path: Path = _SAMPLES_PATH,
    backend: DictBackend | None = None,
) -> EvalResult:
    """Run every sample through the dict backend and tally per-category metrics."""
    samples = load_samples(samples_path)
    if backend is None:
        backend = DictBackend.from_file()

    result = EvalResult()
    for sample in samples:
        decision = await backend.evaluate(sample.content, "user_input")
        flagged = decision.verdict in _FLAGGED_VERDICTS

        if sample.expected_verdict == "allow":
            result.neg_total += 1
            if flagged:
                actual = ",".join(decision.categories) if decision.categories else "?"
                result.neg_fp += 1
                result.fps.append((sample.id, sample.content, actual))
        else:
            result.cat_total[sample.expected_category] += 1
            if flagged and _flagged_for_expected_category(decision, sample.expected_category):
                result.cat_hit[sample.expected_category] += 1
            else:
                result.misses.append((sample.id, sample.content, sample.expected_category))

    return result


def _flagged_for_expected_category(decision: Decision, expected: str) -> bool:
    """Hit only counts if the expected category is in the decision's category set.

    A self_harm sample that flagged as `block`+`violence` is technically
    a miss for recall purposes — the audit log would route the user
    wrong. v1 will tighten this once we have cloud agreement.
    """
    return expected in decision.categories


def _format_console_report(result: EvalResult) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(f"red-line samples : {result.total_positives}")
    lines.append(f"clean samples    : {result.neg_total}")
    lines.append("")
    lines.append("per-category recall")
    lines.append("-" * 48)
    for cat in sorted(result.cat_total):
        total = result.cat_total[cat]
        hit = result.cat_hit[cat]
        pct = hit / total if total else 0.0
        lines.append(f"  {cat:<12} {hit:>3}/{total:<3}  {pct * 100:6.2f}%")
    lines.append("-" * 48)
    lines.append(
        f"  overall      {result.total_hits:>3}/{result.total_positives:<3}  "
        f"{result.recall * 100:6.2f}%   (threshold {RECALL_THRESHOLD * 100:.1f}%)"
    )
    lines.append("")
    lines.append(
        f"false-positive rate: {result.neg_fp}/{result.neg_total}  "
        f"{result.fp_rate * 100:6.2f}%   (threshold {FP_THRESHOLD * 100:.1f}%)"
    )
    lines.append("")
    if result.misses:
        lines.append("misses:")
        for sid, content, expected in result.misses:
            lines.append(f"  - {sid} [{expected}] {content}")
        lines.append("")
    if result.fps:
        lines.append("false positives:")
        for sid, content, actual in result.fps:
            lines.append(f"  - {sid} [{actual}] {content}")
        lines.append("")
    lines.append("RESULT: PASS" if result.passes else "RESULT: FAIL")
    return "\n".join(lines)


def _format_markdown_report(result: EvalResult, *, source: Path) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    status = "PASS" if result.passes else "FAIL"
    lines: list[str] = [
        "# Sprint 0 D5-A · 内容审核词典回归报告",
        "",
        f"- 生成时间: {now}",
        f"- 样本来源: `{source.relative_to(_REPO_ROOT).as_posix()}` (150 红线 + 50 干净负例)",
        "- 评测后端: `DictBackend.from_file()` (本地词典 v0, 单臂)",
        f"- 阈值: 红线召回 ≥ {RECALL_THRESHOLD * 100:.1f}% / 误杀 ≤ {FP_THRESHOLD * 100:.1f}%",
        f"- 结论: **{status}** (recall = {result.recall * 100:.2f}%, "
        f"FP = {result.fp_rate * 100:.2f}%)",
        "",
        "## 类目召回",
        "",
        "| 类目 | 命中 | 总数 | 召回率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for cat in sorted(result.cat_total):
        total = result.cat_total[cat]
        hit = result.cat_hit[cat]
        pct = hit / total if total else 0.0
        lines.append(f"| {cat} | {hit} | {total} | {pct * 100:.2f}% |")
    lines.append(
        f"| **overall** | **{result.total_hits}** | **{result.total_positives}** | "
        f"**{result.recall * 100:.2f}%** |"
    )

    lines.extend(
        [
            "",
            "## 误杀",
            "",
            f"- 干净样本数: {result.neg_total}",
            f"- 误杀数: {result.neg_fp}",
            f"- 误杀率: {result.fp_rate * 100:.2f}%",
        ]
    )

    if result.misses:
        lines.extend(["", "## 漏召样本 (recall miss)", ""])
        lines.append("| ID | 期望类目 | 文本 |")
        lines.append("| --- | --- | --- |")
        for sid, content, expected in result.misses:
            lines.append(f"| {sid} | {expected} | {content} |")
    else:
        lines.extend(["", "## 漏召样本", "", "无 — 150 / 150 全部命中。"])

    if result.fps:
        lines.extend(["", "## 误杀样本 (false positive)", ""])
        lines.append("| ID | 实际命中类目 | 文本 |")
        lines.append("| --- | --- | --- |")
        for sid, content, actual in result.fps:
            lines.append(f"| {sid} | {actual} | {content} |")
    else:
        lines.extend(["", "## 误杀样本", "", "无 — 50 / 50 全部 allow。"])

    lines.extend(
        [
            "",
            "## 已知限制 (v0)",
            "",
            "- `DictBackend` 是字面子串匹配,",
            "  教育/科普场景里出现的红线词 (如 *预防校园暴力讲座*) 会被误杀;",
            "  这是设计权衡,不在 v0 修正范围内。",
            "- PR ③ 的 `CascadingBackend(aliyun, local_dict)` 上线后,",
            "  云端语义层会先处理上下文,本地词典只在云端失败时兜底。",
            "- 50 条负例刻意避开了字典里的精确子串;",
            "  下一轮可加 50 条 *字面命中但语义无害* 的反样本,",
            "  以衡量云端→本地降级时的真实误杀面。",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(result: EvalResult, *, path: Path = _REPORT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _format_markdown_report(result, source=_SAMPLES_PATH),
        encoding="utf-8",
    )


async def main() -> int:
    result = await evaluate_samples()
    print(_format_console_report(result))
    write_report(result)
    print(f"\nreport: {_REPORT_PATH.relative_to(_REPO_ROOT).as_posix()}")
    return 0 if result.passes else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
