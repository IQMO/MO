"""One model-neutral request-depth threshold shared by mapping and review.

Model identity is resolved independently by ``core.provider.model_slots``. Each
feature computes its relevant complexity score, then uses this module only to
choose standard or deep request treatment.
"""
from __future__ import annotations

STANDARD = "standard"
DEEP = "deep"

DEFAULT_TIER_THRESHOLD = 10
TRIVIAL_FLOOR = 2


def decide_depth(complexity_score: int, *, threshold: int = DEFAULT_TIER_THRESHOLD) -> str:
    """Return deep treatment only when the score reaches the shared threshold."""
    return DEEP if int(complexity_score) >= max(1, int(threshold)) else STANDARD


def run_depths(
    scores: list[int],
    *,
    threshold: int = DEFAULT_TIER_THRESHOLD,
    trivial_floor: int = TRIVIAL_FLOOR,
) -> list[str]:
    """Choose one run depth, reducing only genuinely trivial parts to standard."""
    ints = [int(score) for score in (scores or [])]
    if not ints:
        return []
    overall = decide_depth(max(ints), threshold=threshold)
    if overall == STANDARD:
        return [STANDARD] * len(ints)
    floor = max(0, int(trivial_floor))
    return [STANDARD if score <= floor else DEEP for score in ints]
