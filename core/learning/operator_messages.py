"""One deterministic owner for learning from direct operator messages.

High-confidence profile facts and explicit corrections apply through their canonical
writers. Product requirements and uncertain durable signals are retained as inert,
private candidates instead of being dropped or misfiled as personal preferences.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from ..runtime.backend_monitor import redact_monitor_text
from ..runtime.lock import file_byte_lock
from ..state.paths import (
    OPERATOR_MESSAGE_RECEIPTS_PATH,
    OPERATOR_MESSAGE_RECONCILE_PATH,
    PRODUCT_INTENT_CANDIDATES_PATH,
    SESSION_ROOT_DIR,
    resolve_state_path,
)
from ..utils.atomic_write import atomic_write_json
from ..utils.jsonl_utils import read_jsonl, write_jsonl
from ..utils.text_safety import contains_secret_value
from .feedback_learning import extract_feedback_learning, is_explicit_feedback, record_feedback_learning
from .terms_learning import record_terms_learning
from .workflow_learning import promote_workflow_candidate, record_workflow_candidate_result

_PATH_RE = re.compile(r"(?<!\w)([A-Za-z]:[\\/][^\s`\"'<>|;,]+|~/[^\s`\"'<>|;,]+)")
_PROJECT_ASSERTION_RE = re.compile(
    r"\b(?:(?:i|we)\s+(?:have|own|maintain|use|built)|my|our|the)\b.{0,100}\b(?:project|repo(?:sitory)?)\b|"
    r"\b(?:project|repo(?:sitory)?)\b.{0,60}\b(?:is|lives|located)\s+(?:at|in|under)\b",
    re.I | re.S,
)
_PRIVATE_BOUNDARY_RE = re.compile(
    r"\b(?:do\s+not|don'?t|never)\s+(?:read|inspect|scan|open|index)\b.{0,180}"
    r"\b(?:private|privacy|unless\s+i\s+(?:ask|request|approve))\b",
    re.I | re.S,
)
_EXPLICIT_PREFERENCE_RE = re.compile(
    r"\b(?:remember|record|save|note)\b.{0,50}\bpreference\b\s*[:,-]?\s*(.+)", re.I | re.S,
)
_PRODUCT_COMPONENT_RE = re.compile(
    r"\b(?:mo\s+connected(?:\s+tab)?|connected\s+tab|mo\s+desktop|mo\s+design|mo\s+board|"
    r"mo\s+agent|gateway|project\s+review\s+team|prt)\b",
    re.I,
)
_REQUIREMENT_RE = re.compile(
    r"\b(?:must|should|supposed\s+to|needs?\s+to|required?|without|do\s+not|don'?t|never|"
    r"remove|replace|automatic(?:ally)?|workflow|acceptance)\b",
    re.I,
)


def _digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="ignore")).hexdigest()


def _compact(text: str, limit: int = 220) -> str:
    clean = " ".join(str(text or "").split()).strip(" .,:;-\t")
    return redact_monitor_text(clean, limit)


def extract_profile_facts(user_text: str) -> list[tuple[str, str]]:
    """Extract only concrete, high-confidence facts safe for automatic profile writes."""
    text = str(user_text or "")
    facts: list[tuple[str, str]] = []
    paths = _PATH_RE.findall(text)
    if paths and _PROJECT_ASSERTION_RE.search(text):
        path = paths[0].rstrip(".)]")
        name = Path(path.replace("\\", "/")).name or "project"
        privacy = " private" if re.search(r"\bprivate|privacy\b", text, re.I) else ""
        facts.append(("project", f"{name} is the operator's{privacy} project at {path}"))
    if _PRIVATE_BOUNDARY_RE.search(text):
        facts.append(("preference", "Treat operator-declared private projects as opaque; inspect their contents only after an explicit request."))
    explicit = _EXPLICIT_PREFERENCE_RE.search(text)
    if explicit:
        value = _compact(explicit.group(1), 190)
        if value and not re.search(r"\b(?:this\s+(?:turn|task|time)|for\s+now|today|temporar)\b", value, re.I):
            facts.append(("preference", value))
    return list(dict.fromkeys(facts))


def extract_product_intent(user_text: str) -> str:
    """Return a compact product requirement, never a personal-profile rule."""
    text = str(user_text or "").strip()
    if not text or not _PRODUCT_COMPONENT_RE.search(text) or not _REQUIREMENT_RE.search(text):
        return ""
    if contains_secret_value(text):
        return ""
    return _compact(text, 320)


def _state_path(profile: Any, relative: str, config: dict | None = None) -> Path:
    raw = str(getattr(profile, "_path", "") or "")
    if raw:
        return Path(raw).parent / Path(relative).relative_to("memory")
    return Path(resolve_state_path(relative, config))


def _append_product_intent(profile: Any, text: str, *, source_id: str, config: dict | None) -> bool:
    path = _state_path(profile, PRODUCT_INTENT_CANDIDATES_PATH, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    component = (_PRODUCT_COMPONENT_RE.search(text).group(0) if _PRODUCT_COMPONENT_RE.search(text) else "MO").lower()
    family = "automatic-browser-control" if re.search(r"connected|chrome|tab", text, re.I) and re.search(r"automatic|without|click|manual", text, re.I) else component
    identity = "product-intent:" + hashlib.sha1(family.encode()).hexdigest()[:12]
    now = time.time()
    with file_byte_lock(path.parent / ".candidates.lock"):
        rows = read_jsonl(path)
        for row in rows:
            if row.get("id") == identity:
                evidence = list(row.get("evidence") or [])
                marker = {"source": source_id, "message_sha256": _digest(text)}
                if marker not in evidence:
                    evidence.append(marker)
                    row["evidence"] = evidence[-12:]
                    row["updated_at"] = now
                    write_jsonl(path, rows)
                    return True
                return False
        rows.append({
            "id": identity, "status": "candidate", "destination": "project_history",
            "requirement": text, "evidence": [{"source": source_id, "message_sha256": _digest(text)}],
            "created_at": now, "updated_at": now,
        })
        write_jsonl(path, rows[-200:])
    return True


def _receipt_path(profile: Any, config: dict | None) -> Path:
    return _state_path(profile, OPERATOR_MESSAGE_RECEIPTS_PATH, config)


def _already_processed(path: Path, message_sha256: str) -> bool:
    for row in read_jsonl(path):
        if row.get("message_sha256") != message_sha256:
            continue
        return not _receipt_needs_retry(row)
    return False


def _receipt_needs_retry(row: dict) -> bool:
    return any(
        event.get("status") == "failed"
        or (event.get("destination") == "learning_review" and event.get("status") == "staged" and not event.get("id"))
        for event in row.get("events") or []
    )


def _write_receipt(path: Path, *, source_id: str, message_sha256: str, events: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_byte_lock(path.parent / ".receipts.lock"):
        rows = read_jsonl(path)
        receipt = {"source": source_id, "message_sha256": message_sha256, "events": events, "created_at": time.time()}
        for index, row in enumerate(rows):
            if row.get("message_sha256") != message_sha256:
                continue
            if _receipt_needs_retry(row):
                rows[index] = receipt
                write_jsonl(path, rows[-2000:])
            return
        rows.append(receipt)
        write_jsonl(path, rows[-2000:])


def process_operator_message(
    profile: Any, user_text: str, assistant_text: str = "", *, source_id: str = "", config: dict | None = None,
) -> list[dict[str, str]]:
    """Route one direct operator message exactly once and return audit events."""
    text = str(user_text or "").strip()
    if not profile or not text:
        return []
    message_sha256 = _digest(text)
    receipts = _receipt_path(profile, config)
    if _already_processed(receipts, message_sha256):
        return []
    source = source_id or "message:" + message_sha256[:12]
    events: list[dict[str, str]] = []
    try:
        from ..profile import capture_operator_name
        if capture_operator_name(profile, text):
            events.append({"destination": "profile", "kind": "name", "status": "recorded"})
        terms = record_terms_learning(profile, text)
        if terms:
            events.append({"destination": "terms", "kind": ", ".join(terms[:2]), "status": "recorded"})
        from ..profile.facts import list_profile_facts, record_profile_fact
        existing_facts = list_profile_facts(profile=profile, config=config)
        for category, fact in extract_profile_facts(text):
            paths = {item.casefold() for item in _PATH_RE.findall(fact)}
            covered = any(
                prior.category == category
                and (prior.fact.casefold() == fact.casefold()
                     or (paths and paths.intersection(item.casefold() for item in _PATH_RE.findall(prior.fact))))
                for prior in existing_facts
            )
            if covered:
                events.append({"destination": "facts", "kind": category, "status": "already recorded"})
                continue
            status, item = record_profile_fact(category, fact, evidence=source, profile=profile, config=config)
            if item and status in {"recorded", "already recorded"}:
                events.append({"destination": "facts", "kind": category, "status": status})
                existing_facts.append(item)
        insights = extract_feedback_learning(text, assistant_text)
        if insights:
            recorded = record_feedback_learning(profile, text, assistant_text)
            events.append({"destination": "behavior", "kind": "correction", "status": "recorded" if recorded else "already recorded"})
        promote = promote_workflow_candidate(profile, text, assistant_text)
        if promote.get("promoted"):
            events.append({"destination": "skills", "kind": "workflow", "status": "promoted"})
        workflow = record_workflow_candidate_result(profile, text, assistant_text)
        if workflow.get("recorded"):
            events.append({"destination": "workflow", "kind": "candidate", "status": "recorded"})
        product = extract_product_intent(text)
        if product and _append_product_intent(profile, product, source_id=source, config=config):
            events.append({"destination": "project_history", "kind": "product intent candidate", "status": "staged"})
        if (is_explicit_feedback(text) and not contains_secret_value(text)
                and not any(event["destination"] in {"behavior", "project_history", "workflow", "skills"} for event in events)):
            from .proactive_learning import (
                LearningSuggestion, SuggestionEvidence, _resolve_suggestions_path,
                read_learning_suggestions, write_learning_suggestions,
            )

            suggestion = LearningSuggestion(
                id="learning-suggestion:operator_correction:" + message_sha256[:16],
                kind="operator_correction",
                recommendation=_compact(text, 500),
                evidence=(SuggestionEvidence(source, _compact(text, 500)),),
            )
            path = write_learning_suggestions([suggestion], path=_resolve_suggestions_path(profile=profile, config=config))
            if any(item.id == suggestion.id for item in read_learning_suggestions(path=path)):
                events.append({"destination": "learning_review", "kind": "unclassified correction", "status": "staged", "id": suggestion.id})
    except Exception as exc:
        events.append({"destination": "learning", "kind": type(exc).__name__, "status": "failed"})
        try:
            from ..runtime.backend_monitor import get_monitor
            monitor = get_monitor()
            if monitor:
                monitor.emit("learning_write_error", {"stage": "operator_message_router", "error": type(exc).__name__})
        except Exception:
            pass
    _write_receipt(receipts, source_id=source, message_sha256=message_sha256, events=events)
    return events


def _reconcile_saved_operator_messages(profile: Any, *, sessions_dir: str | Path = SESSION_ROOT_DIR, config: dict | None = None) -> dict[str, int]:
    """Backfill changed conversation snapshots once, then stay incremental."""
    from ..session.session import INTERNAL_CONTINUATION_KEY, is_runtime_owned_session_summary
    from ..session.sessions import iter_conversation_session_paths
    root = Path(resolve_state_path(sessions_dir, config))
    checkpoint_path = _state_path(profile, OPERATOR_MESSAGE_RECONCILE_PATH, config)
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        # Version 2 repairs old receipts that claimed review staging without
        # writing a candidate. Existing successful captures remain idempotent.
        known = checkpoint.get("snapshots", {}) if isinstance(checkpoint, dict) and checkpoint.get("version") == 2 else {}
    except (OSError, json.JSONDecodeError):
        known = {}
    # One changed snapshot may contain a long conversation. Lazily preload the
    # settled receipt set once instead of rereading the whole JSONL ledger for
    # every old user message in that snapshot. ``process_operator_message`` still
    # owns the authoritative locked recheck for new or retryable messages.
    settled_receipts: set[str] | None = None
    scanned = processed = failed = changed = 0
    updated = dict(known)
    for path in iter_conversation_session_paths(root):
        try:
            stamp = path.stat().st_mtime_ns
            key = str(path.resolve(strict=False))
            if int(known.get(key) or 0) == stamp:
                continue
            changed += 1
            payload = json.loads(path.read_text(encoding="utf-8"))
            session_id = str(payload.get("session_id") or path.stem)
            messages = list(payload.get("messages") or [])
            if settled_receipts is None:
                settled_receipts = {
                    str(row.get("message_sha256") or "")
                    for row in read_jsonl(_receipt_path(profile, config))
                    if row.get("message_sha256") and not _receipt_needs_retry(row)
                }
            file_failed = False
            for index, message in enumerate(messages):
                if not isinstance(message, dict) or message.get("role") != "user" or message.get(INTERNAL_CONTINUATION_KEY) or is_runtime_owned_session_summary(message):
                    continue
                text = message.get("content")
                if not isinstance(text, str) or not text.strip():
                    continue
                scanned += 1
                message_sha256 = _digest(text)
                if message_sha256 in settled_receipts:
                    continue
                assistant = ""
                if index + 1 < len(messages) and isinstance(messages[index + 1], dict) and messages[index + 1].get("role") == "assistant":
                    assistant = str(messages[index + 1].get("content") or "")
                events = process_operator_message(profile, text, assistant, source_id=f"session:{session_id}:{index}", config=config)
                processed += bool(events)
                event_failed = any(event.get("status") == "failed" for event in events)
                failed += event_failed
                file_failed = file_failed or event_failed
                if not event_failed:
                    settled_receipts.add(message_sha256)
            if not file_failed:
                updated[key] = stamp
        except Exception:
            failed += 1
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(checkpoint_path, {"version": 2, "snapshots": updated, "updated_at": time.time()}, indent=2)
    return {"snapshots": changed, "scanned": scanned, "processed": processed, "failed": failed}


def reconcile_saved_operator_messages(profile: Any, *, sessions_dir: str | Path = SESSION_ROOT_DIR, config: dict | None = None) -> dict[str, int]:
    """Serialize snapshot reconciliation across concurrent MO surfaces."""
    checkpoint = _state_path(profile, OPERATOR_MESSAGE_RECONCILE_PATH, config)
    with file_byte_lock(checkpoint.with_suffix(checkpoint.suffix + ".lock")):
        return _reconcile_saved_operator_messages(profile, sessions_dir=sessions_dir, config=config)


def learning_receipts_since(profile: Any, since: float, *, config: dict | None = None) -> list[str]:
    """Return bounded closeout summaries for automatic learning destinations."""
    rows = [row for row in read_jsonl(_receipt_path(profile, config)) if float(row.get("created_at") or 0) >= since]
    out: list[str] = []
    for row in rows[-12:]:
        for event in row.get("events") or []:
            item = f"{event.get('destination')}: {event.get('kind')} ({event.get('status')})"
            if item not in out:
                out.append(item)
    return out[:12]
