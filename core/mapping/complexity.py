"""Choose mapthis request depth without changing the selected provider/model.

``structural_risk_score`` scores a set of files by graph topology (communities
spanned, cross-community edges, god-nodes touched). The shared tier decision
selects standard or deep request treatment for the whole run, with only
trivial slices reduced to standard. The interface-selected model remains the
single model authority in ``core.provider.model_slots``.
"""
from __future__ import annotations

from typing import Any

from ..provider.model_tier import DEFAULT_TIER_THRESHOLD, DEEP, run_depths

# These surfaces change request depth, not provider/model identity.
STANDARD_SURFACE = "mapper"
DEEP_SURFACE = "mapper-deep"
STANDARD_MAX_TOKENS = 3000
DEEP_MAX_TOKENS = 6000


def slice_complexity(files: list[str], base: Any) -> int:
    """Structural risk/complexity score for a slice's files (0 when no graph)."""
    try:
        from ..graph.structural_graph import structural_risk_score

        return int(structural_risk_score(list(files or []), [], base))
    except Exception:
        return 0


def _surface_for(depth: str, score: int) -> tuple[str, int, int]:
    if depth == DEEP:
        return DEEP_SURFACE, DEEP_MAX_TOKENS, score
    return STANDARD_SURFACE, STANDARD_MAX_TOKENS, score


def slice_tiers(slices: list[list[str]], base: Any, *, threshold: int = DEFAULT_TIER_THRESHOLD) -> list[tuple[str, int, int]]:
    """Return ``(surface, max_tokens, score)`` in slice order.

    One shared complexity rule chooses standard or deep request treatment. There
    are no feature-local exceptions based on path names or file categories.
    """
    scores = [slice_complexity(slice_files, base) for slice_files in slices]
    depths = run_depths(scores, threshold=int(threshold))
    return [_surface_for(depth, score) for depth, score in zip(depths, scores)]
