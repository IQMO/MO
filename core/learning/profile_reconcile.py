"""On-demand local dialectic reconciliation of MO's operator model.

MO accumulates operator learning from two places: confirmed learning suggestions
(``learning_suggestions.jsonl``) and profile prose (``learning.md`` / ``behavior.md``).
Over time these drift, duplicate, and occasionally contradict. This module gathers
those inputs and builds a single structured "dialectic" prompt (assess -> self-audit
-> reconcile) for ONE cheap provider call, then writes the result as a REVIEW
PROPOSAL under profile state.

Boundary: it NEVER rewrites ``learning.md``/``behavior.md`` itself. Applying the
reconciliation stays an explicit operator act, preserving the inert-by-default
learning boundary. The provider call lives in the caller (the agent owns provider
access); everything here is pure and testable without a model.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from ..utils.text_safety import contains_secret_value

PROFILE_PROSE_FILES = ("learning.md", "behavior.md")

DIALECTIC_SYSTEM = (
    "You reconcile an AI agent's model of its operator. Work in three phases and show each:\n"
    "1) ASSESS - list the distinct, durable operator preferences/traits implied by the inputs.\n"
    "2) SELF-AUDIT - flag duplicates, redundancies, and any CONTRADICTIONS between the inputs.\n"
    "3) RECONCILE - output one clean, deduplicated, prioritized operator model, then a short list "
    "of contradictions that need the operator to decide.\n"
    "Be faithful to the operator's own words; do not invent preferences. This is a PROPOSAL for "
    "operator review, not an applied change. Keep it tight."
)


def _profile_dir(profile: Any) -> Path | None:
    profile_path = getattr(profile, "_path", None)
    if not profile_path:
        return None
    return Path(profile_path).expanduser().parent / "profile"


def gather_reconcile_inputs(
    profile: Any,
    *,
    suggestions_path: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect confirmed learnings (clustered) + profile prose for reconciliation."""
    from .proactive_learning import cluster_suggestions, read_learning_suggestions
    from ..state.paths import resolve_state_path

    path = suggestions_path or resolve_state_path("memory/learning_suggestions.jsonl", config or {})
    confirmed = [
        s for s in read_learning_suggestions(path=str(path), include_inactive=True)
        if str(s.status).lower() == "confirmed"
    ]
    learnings = [f"[{c.kind}] {c.recommendation}" for c in cluster_suggestions(confirmed)]

    prose: dict[str, str] = {}
    profile_dir = _profile_dir(profile)
    if profile_dir and profile_dir.exists():
        for name in PROFILE_PROSE_FILES:
            fp = profile_dir / name
            if fp.exists():
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    continue
                if text:
                    prose[name] = text[:6000]
    return {"learnings": learnings, "prose": prose}


def build_dialectic_prompt(inputs: dict[str, Any]) -> tuple[str, str]:
    """Return (system, user) messages for the one-call dialectic reconciliation."""
    learnings = "\n".join(f"- {item}" for item in inputs.get("learnings") or []) or "(none)"
    prose_parts = inputs.get("prose") or {}
    prose = "\n\n".join(f"### {name}\n{text}" for name, text in prose_parts.items()) or "(none)"
    user = (
        f"CONFIRMED LEARNINGS:\n{learnings}\n\n"
        f"PROFILE PROSE:\n{prose}\n\n"
        "Produce the three-phase reconciliation."
    )
    return DIALECTIC_SYSTEM, user


def has_reconcile_inputs(inputs: dict[str, Any]) -> bool:
    return bool(inputs.get("learnings") or inputs.get("prose"))


def write_reconcile_proposal(
    text: str,
    *,
    config: dict[str, Any] | None = None,
    stamp: str = "",
) -> Path | None:
    """Write the reconciliation as a review proposal under profile state.

    Returns the path, or None if the text trips the secret detector (refusing to
    persist a proposal that echoes a secret). Never touches profile prose files.
    """
    body = str(text or "").strip()
    if not body or contains_secret_value(body):
        return None
    from ..state.paths import resolve_state_path

    stamp = stamp or datetime.now().strftime("%Y-%m-%dT%H%M%S")
    out = Path(resolve_state_path(f"memory/profile_reconcile/{stamp}.md", config or {}))
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Operator-model reconciliation proposal — {stamp}\n\n"
        "_Review proposal only. MO did not edit your profile. Apply by hand if you agree._\n\n"
    )
    atomic_write_text(out, header + body + "\n", encoding="utf-8")
    return out
