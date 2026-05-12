"""Pytest gate for the 200-sample moderation dict regression.

Runs the same `evaluate_samples()` the CLI uses, asserts the published
thresholds. Any future dict edit that drops recall < 99.5 % or raises
FP > 3 % will fail CI here.
"""

from __future__ import annotations

from scripts.moderation_eval.run_eval import (
    FP_THRESHOLD,
    RECALL_THRESHOLD,
    evaluate_samples,
    load_samples,
)


def test_dataset_has_150_positives_and_50_negatives() -> None:
    samples = load_samples()
    positives = [s for s in samples if s.expected_verdict != "allow"]
    negatives = [s for s in samples if s.expected_verdict == "allow"]
    assert len(positives) == 150
    assert len(negatives) == 50


def test_every_positive_declares_a_category() -> None:
    """Catches typos like missing-category rows that would silently allow."""
    samples = load_samples()
    for sample in samples:
        if sample.expected_verdict == "allow":
            assert sample.expected_category == ""
        else:
            assert sample.expected_category in {
                "self_harm",
                "violence",
                "loan",
                "harassment",
                "political",
                "other",
            }, f"{sample.id} has unknown category {sample.expected_category!r}"


def test_self_harm_samples_expect_redirect_others_expect_block() -> None:
    samples = load_samples()
    for sample in samples:
        if sample.expected_category == "self_harm":
            assert sample.expected_verdict == "redirect", sample.id
        elif sample.expected_verdict != "allow":
            assert sample.expected_verdict == "block", sample.id


async def test_dict_meets_recall_threshold() -> None:
    result = await evaluate_samples()
    assert result.recall >= RECALL_THRESHOLD, (
        f"recall {result.recall:.4f} below threshold {RECALL_THRESHOLD}. Misses: {result.misses}"
    )


async def test_dict_meets_fp_threshold() -> None:
    result = await evaluate_samples()
    assert result.fp_rate <= FP_THRESHOLD, (
        f"FP rate {result.fp_rate:.4f} above threshold {FP_THRESHOLD}. "
        f"False positives: {result.fps}"
    )


async def test_every_category_has_at_least_one_sample() -> None:
    """Guards against a future PR shrinking the dataset and silently dropping
    a red-line category from coverage."""
    result = await evaluate_samples()
    for category in ("self_harm", "violence", "loan", "harassment", "political", "other"):
        assert result.cat_total[category] >= 10, (
            f"category {category!r} has only {result.cat_total[category]} samples"
        )
