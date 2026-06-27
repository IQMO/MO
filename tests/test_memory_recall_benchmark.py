"""Tracked-metric regression guard for MO's episodic memory recall.

Establishes a measured floor for `EpisodicMemory` recall quality so a change that
silently degrades recall is caught, and so a future improvement (hybrid
semantic+keyword fusion) can be measured against a known baseline.

Baseline measured 2026-06-27 on the bm25 (keyword-only, dependency-free) path:
recall@1=0.667, recall@3=0.800, recall@5=0.800, MRR=0.728. Floors sit just below
to allow trivial variation while catching real regressions.
"""
import pytest

from core.learning.recall_benchmark import concept_embedder, run_recall_benchmark


@pytest.fixture(scope="module")
def report():
    return run_recall_benchmark()


@pytest.fixture(scope="module")
def hybrid_report():
    # Controlled concept-embedder stands in for a real model so the hybrid fusion
    # path can be measured deterministically (kernel #2). Production lift comes from
    # real embeddings; this proves the fusion MECHANISM lifts recall and never hurts.
    return run_recall_benchmark(embedder=concept_embedder())


def test_recall_benchmark_runs_and_reports(report):
    assert report["queries"] >= 20
    assert report["mode"] == "bm25"
    assert set(report["recall_at"]) == {1, 3, 5}
    assert 0.0 <= report["mrr"] <= 1.0


def test_recall_quality_does_not_regress(report):
    # Floors just below the 2026-06-27 bm25 baseline (0.667 / 0.800 / 0.800).
    assert report["recall_at"][5] >= 0.75, f"recall@5 regressed: {report['recall_at']}"
    assert report["recall_at"][1] >= 0.60, f"recall@1 regressed: {report['recall_at']}"
    assert report["mrr"] >= 0.65, f"MRR regressed: {report['mrr']}"


def test_recall_at_k_is_monotonic(report):
    assert report["recall_at"][1] <= report["recall_at"][3] <= report["recall_at"][5]


def test_hybrid_fusion_lifts_recall(report, hybrid_report):
    # Kernel #2: fusing semantic + bm25 must recover lexical-gap misses and never
    # score worse than bm25 alone on any K.
    assert hybrid_report["mode"] == "hybrid"
    for k in (1, 3, 5):
        assert hybrid_report["recall_at"][k] >= report["recall_at"][k], (
            f"hybrid regressed vs bm25 at recall@{k}: "
            f"{hybrid_report['recall_at'][k]} < {report['recall_at'][k]}"
        )
    assert hybrid_report["recall_at"][5] >= 0.90, hybrid_report["recall_at"]
    assert len(hybrid_report["misses"]) < len(report["misses"])
