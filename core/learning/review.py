"""Read-only review projection over the existing suggestion/workflow owners."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from .proactive_learning import _LEARNING_LABELS, _cluster_evidence_count, _resolve_suggestions_path, suggestion_review_clusters
from .workflow_learning import active_workflow_skills, pending_workflow_candidates


def learning_review_items(profile: Any = None, *, config=None, active=False) -> list[tuple[str, object]]:
    pending, accepted = suggestion_review_clusters(
        path=_resolve_suggestions_path(profile=profile, config=config),
    )
    if active:
        return [("suggestion", item) for item in accepted] + [
            ("workflow_skill", skill) for skill in active_workflow_skills(profile, config=config)
        ]
    return [("suggestion", item) for item in pending] + [
        ("workflow", item) for item in pending_workflow_candidates(profile)
    ]


def describe_review_item(item: tuple[str, object]) -> dict[str, str]:
    """Bind actions to reviewed content, not a moving row number or UI label."""
    kind, value = item
    if kind == "suggestion":
        candidate = value.representative
        source_id = candidate.id
        label = _LEARNING_LABELS.get(value.kind, value.kind.replace("_", " ").title())
        changes = value.recommendation
        active = candidate.status == "confirmed"
        reason = ("Learned automatically" if candidate.auto_promoted else "Approved by you") if active else (
            f"Based on {_cluster_evidence_count(value)} matching conversation(s)."
        )
        example = next((e.snippet for e in candidate.evidence if e.snippet), "No example available.")
        lines = [f"What changes: {changes}", f"Why: {reason}", f"Example: {example}"]
        content = [candidate.as_dict(), sorted(value.ids)]
    elif kind == "workflow_skill":
        source_id = value.candidate_id
        label = "Workflow habit"
        changes = value.description or value.name
        active = True
        lines = [
            f"Skill: {value.name}",
            f"When: {', '.join(value.triggers)}",
            f"Scope: {value.scope or 'matching work'}",
            "Source: approved workflow skill",
            f"Current guidance: {value.body}",
        ]
        content = asdict(value)
        # Usage counters may advance without changing the reviewed guidance.
        content.pop("mastery", None)
    else:
        source_id = str(value.get("id") or "")
        label = "Workflow habit"
        changes = str(value.get("behavior") or "apply the reviewed workflow")
        active = False
        external = bool(value.get("source_text"))
        lines = [
            f"When: {value.get('trigger') or 'matching work'}",
            f"What changes: {changes}",
            "Why: " + ("You requested this workflow source." if external else "You gave an explicit workflow preference."),
        ]
        for key, title in (("scope", "Scope"), ("anti_pattern", "Avoid"), ("source_label", "Source"), ("source_text", "Source guidance")):
            if value.get(key):
                lines.append(f"{title}: {value[key]}")
        content = value
    revision = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=True).encode()).hexdigest()[:16]
    from ..tooling.sandbox import redact_sensitive_text
    from unicodedata import category

    def safe(text):
        return "".join(c for c in redact_sensitive_text(str(text)) if c == "\n" or not category(c).startswith("C"))

    return {
        "id": source_id,
        "ref": f"{source_id} {revision}",
        "label": safe(label),
        "summary": " ".join(safe(changes).split()),
        "state": "active" if active else "pending",
        "details": safe("\n".join([label, "Active — applies only when relevant." if active else "Pending — not active until you approve.", *lines])),
    }
