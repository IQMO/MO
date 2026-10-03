"""One small private record for commitments the operator chose to track."""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import bounded_text as _text
from ..runtime.lock import file_byte_lock
from ..state.paths import resolve_state_path
from ..utils.atomic_write import atomic_write_json


ITEMS_PATH = "memory/life/items.json"
LOCK_PATH = "memory/life/items.lock"
CATEGORIES = frozenset({"payment", "subscription", "appointment", "issue", "case", "other"})
CASE_UPDATE_KINDS = frozenset({"conversation", "paperwork", "step", "note"})
STATUSES = frozenset({"open", "done"})
SOURCE_PROVIDERS = frozenset({"operator", "gmail", "outlook"})
FREQUENCIES = frozenset({"once", "weekly", "monthly", "quarterly", "yearly"})
_LOCK = threading.Lock()


def _paths(config: dict | None = None) -> tuple[Path, Path]:
    return Path(resolve_state_path(ITEMS_PATH, config)), Path(resolve_state_path(LOCK_PATH, config))


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Life items are unavailable; the saved record was not changed") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("items"), list):
        raise ValueError("Life items are unavailable; the saved record was not changed")
    items = data["items"]
    if any(not isinstance(item, dict) for item in items):
        raise ValueError("Life items are unavailable; the saved record was not changed")
    return items


def _due(value: Any) -> str:
    raw = _text(value, label="Due date", limit=10)
    if raw:
        try:
            if date.fromisoformat(raw).isoformat() != raw:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Due date must be YYYY-MM-DD") from exc
    return raw


def _category(value: Any) -> str:
    category = str(value or "other").strip().lower()
    if category not in CATEGORIES:
        raise ValueError("Choose payment, subscription, appointment, issue, case, or other")
    return category


def _plan_amount(value: Any) -> str:
    if value in (None, ""):
        return ""
    from .money import _amount

    return _amount(value)


def _plan_currency(value: Any, amount: str) -> str:
    from .money import _currency

    raw = str(value or "").strip()
    if bool(raw) != bool(amount):
        raise ValueError("Expected amount and currency must be supplied together")
    return _currency(raw) if raw else ""


def _frequency(value: Any) -> str:
    result = str(value or "once").strip().lower()
    if result not in FREQUENCIES:
        raise ValueError("Choose once, weekly, monthly, quarterly, or yearly")
    return result


def _installments(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= 240:
        raise ValueError("Total installments must be from 1 to 240")
    return int(value)


def _plan(fields: dict[str, Any], *, category: str) -> dict[str, Any]:
    amount = _plan_amount(fields.get("expected_amount"))
    currency = _plan_currency(fields.get("currency"), amount)
    frequency = _frequency(fields.get("frequency"))
    installments = _installments(fields.get("installments_total"))
    if category not in {"payment", "subscription"} and (amount or frequency != "once" or installments):
        raise ValueError("Payment plan details require a payment or subscription group")
    if installments and frequency == "once" and installments > 1:
        raise ValueError("Choose a repeating interval for multiple installments")
    return {"expected_amount": amount, "currency": currency,
            "frequency": frequency, "installments_total": installments}


def list_items(*, config: dict | None = None) -> list[dict[str, Any]]:
    path, lock = _paths(config)
    # An absent store is an empty snapshot; reading it must not create state.
    if not path.exists():
        return []
    with file_byte_lock(lock, _LOCK):
        items = [dict(item) for item in _read(path)]
    return sorted(items, key=lambda item: (
        item.get("status") == "done", item.get("due_date") or "9999-12-31",
        -float(item.get("updated_at") or 0),
    ))


def find_item_by_title(title: str, *, config: dict | None = None) -> dict[str, Any]:
    match = " ".join(str(title or "").split()).casefold()
    if not match:
        raise ValueError("Give an exact Life item title")
    matches = [item for item in list_items(config=config)
               if str(item.get("title") or "").casefold() == match]
    if len(matches) != 1:
        raise ValueError("No unique item matched; review the connected Dashboard Life view")
    return matches[0]


def summary(*, config: dict | None = None, today: date | None = None) -> dict[str, int]:
    current = today or date.today()
    upcoming = current + timedelta(days=7)
    items = list_items(config=config)
    open_items = [item for item in items if item.get("status") == "open"]
    return {
        "total": len(items),
        "open": len(open_items),
        "overdue": sum(bool(item.get("due_date")) and item["due_date"] < current.isoformat() for item in open_items),
        "due_soon": sum(bool(item.get("due_date")) and current.isoformat() <= item["due_date"] <= upcoming.isoformat() for item in open_items),
        "done": len(items) - len(open_items),
    }


def create_item(
    *, title: str, category: str = "other", due_date: str = "", notes: str = "",
    expected_amount: Any = "", currency: str = "", frequency: str = "once",
    installments_total: Any = None,
    case_area: str = "", reference: str = "",
    source_provider: str = "operator", source_id: str = "", config: dict | None = None,
) -> dict[str, Any]:
    title = _text(title, label="Title", limit=180, required=True)
    category = _category(category)
    due_date = _due(due_date)
    notes = _text(notes, label="Notes", limit=500)
    plan = _plan({"expected_amount": expected_amount, "currency": currency,
                  "frequency": frequency, "installments_total": installments_total}, category=category)
    case_area = _text(case_area, label="Case area", limit=80)
    reference = _text(reference, label="Case reference", limit=180)
    if category != "case" and (case_area or reference):
        raise ValueError("Case details require the case group")
    source_provider = str(source_provider or "operator").strip().lower()
    if source_provider not in SOURCE_PROVIDERS:
        raise ValueError("Unknown item source")
    source_id = _text(source_id, label="Mail ID", limit=120)
    if source_provider != "gmail" and source_id:
        raise ValueError("Only Gmail supplies a durable message ID here")
    path, lock = _paths(config)
    with file_byte_lock(lock, _LOCK):
        items = _read(path)
        if source_id and any(
            item.get("source_provider") == "gmail" and item.get("source_id") == source_id
            for item in items
        ):
            raise ValueError("This Gmail message is already tracked")
        if len(items) >= 1000:
            raise ValueError("Life item limit reached; review existing items first")
        now = time.time()
        item = {
            "id": "life-" + uuid.uuid4().hex[:12],
            "title": title,
            "category": category,
            "status": "open",
            "due_date": due_date,
            "notes": notes,
            "case_area": case_area,
            "reference": reference,
            "updates": [],
            **plan,
            "source_provider": source_provider,
            "source_id": source_id,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"version": 1, "items": [*items, item]}, indent=2, ensure_ascii=False)
    return dict(item)


def update_item(
    item_id: str, *, expected_revision: int, changes: dict[str, Any],
    config: dict | None = None,
) -> dict[str, Any]:
    allowed = {"title", "category", "status", "due_date", "notes", "expected_amount",
               "currency", "frequency", "installments_total", "case_area", "reference"}
    if not isinstance(changes, dict) or not changes or set(changes) - allowed:
        raise ValueError("Choose only Life item fields to update")
    path, lock = _paths(config)
    with file_byte_lock(lock, _LOCK):
        items = _read(path)
        item = next((item for item in items if item.get("id") == item_id), None)
        if item is None:
            raise ValueError("Life item not found")
        if int(item.get("revision") or 0) != expected_revision:
            raise ValueError("Life item changed; refresh before editing")
        if "title" in changes:
            item["title"] = _text(changes["title"], label="Title", limit=180, required=True)
        if "category" in changes:
            item["category"] = _category(changes["category"])
        if set(changes) & {"category", "expected_amount", "currency", "frequency", "installments_total"}:
            plan_fields = {key: changes.get(key, item.get(key))
                           for key in ("expected_amount", "currency", "frequency", "installments_total")}
            item.update(_plan(plan_fields, category=item.get("category", "other")))
        if "due_date" in changes:
            item["due_date"] = _due(changes["due_date"])
        if "notes" in changes:
            item["notes"] = _text(changes["notes"], label="Notes", limit=500)
        for key, label, limit in (("case_area", "Case area", 80), ("reference", "Case reference", 180)):
            if key in changes:
                item[key] = _text(changes[key], label=label, limit=limit)
        if item.get("category") != "case" and (item.get("case_area") or item.get("reference") or item.get("updates")):
            raise ValueError("Remove case details before changing its group")
        if "status" in changes:
            status = str(changes["status"] or "").strip().lower()
            if status not in STATUSES:
                raise ValueError("Choose open or done")
            item["status"] = status
        item["revision"] = expected_revision + 1
        item["updated_at"] = time.time()
        atomic_write_json(path, {"version": 1, "items": items}, indent=2, ensure_ascii=False)
    return dict(item)


def add_case_update(
    item_id: str, *, expected_revision: int, date_value: str, kind: str,
    summary_text: str, reference: str = "", config: dict | None = None,
) -> dict[str, Any]:
    """Append one operator-confirmed event to an existing case."""
    event_date = _due(date_value)
    if not event_date:
        raise ValueError("Case update date is required")
    kind = str(kind or "note").strip().lower()
    if kind not in CASE_UPDATE_KINDS:
        raise ValueError("Choose conversation, paperwork, step, or note")
    event = {"date": event_date, "kind": kind,
             "summary": _text(summary_text, label="Case update", limit=500, required=True),
             "reference": _text(reference, label="Update reference", limit=180)}
    path, lock = _paths(config)
    with file_byte_lock(lock, _LOCK):
        items = _read(path)
        item = next((entry for entry in items if entry.get("id") == item_id), None)
        if item is None or item.get("category") != "case":
            raise ValueError("Case not found")
        if int(item.get("revision") or 0) != expected_revision:
            raise ValueError("Case changed; refresh before adding an update")
        updates = item.get("updates") or []
        if not isinstance(updates, list) or len(updates) >= 100:
            raise ValueError("Case update limit reached; review this case first")
        item["updates"] = [*updates, event]
        item["revision"] = expected_revision + 1
        item["updated_at"] = time.time()
        atomic_write_json(path, {"version": 1, "items": items}, indent=2, ensure_ascii=False)
    return dict(item)


def forget_item(item_id: str, *, expected_revision: int, config: dict | None = None) -> bool:
    path, lock = _paths(config)
    with file_byte_lock(lock, _LOCK):
        items = _read(path)
        item = next((item for item in items if item.get("id") == item_id), None)
        if item is None:
            raise ValueError("Life item not found")
        if int(item.get("revision") or 0) != expected_revision:
            raise ValueError("Life item changed; refresh before forgetting")
        atomic_write_json(path, {"version": 1, "items": [entry for entry in items if entry is not item]}, indent=2, ensure_ascii=False)
    return True


def operator_result(action: str, result: dict[str, Any], *, config: dict | None = None) -> str:
    """Show confirmed details locally after the model receives only opaque state."""
    if action == "forget":
        return "Life item forgotten."
    items = list_items(config=config)
    from .money import linked_counts

    counts_by_item = linked_counts(config=config)

    def detail_for(item: dict[str, Any]) -> str:
        plan = (item.get("expected_amount") or "") + (
            " " + item.get("currency", "") + " expected per payment" if item.get("expected_amount") else "")
        recorded = counts_by_item.get(item.get("id", ""), 0)
        total = item.get("installments_total")
        progress = f"{recorded}/{total} recorded outgoings" if total else (
            f"{recorded} recorded outgoings" if recorded else "")
        return " · ".join(part for part in (
            item.get("category") or "other", item.get("status") or "open",
            "due " + item["due_date"] if item.get("due_date") else "",
            plan, item.get("frequency") if item.get("frequency") not in (None, "once") else "",
            progress, item.get("notes") or "",
            item.get("case_area") or "", item.get("reference") or "",
            f"{len(item.get('updates') or [])} case updates" if item.get("category") == "case" else "",
        ) if part)

    if action == "list":
        counts = summary(config=config)
        lines = [f"**Life** · {counts['open']} open · {counts['overdue']} overdue · "
                 f"{counts['due_soon']} due within 7 days · {counts['done']} done"]
        if not items:
            lines.append("Nothing confirmed yet.")
        for item in items[:20]:
            lines.append(f"- {item.get('title') or '(untitled)'} — {detail_for(item)}")
        if len(items) > 20:
            lines.append(f"Showing 20 of {len(items)}. Open Dashboard → Life for the full list.")
        return "\n".join(lines)
    selected = next((item for item in items if item.get("id") == result.get("id")), None)
    if selected is None:
        return "Life item saved. Open Dashboard → Life for its details."
    if action == "show":
        lines = [f"**{selected.get('title') or '(untitled)'}** · {detail_for(selected)}"]
        if selected.get("category") == "case":
            updates = selected.get("updates") or []
            if not updates:
                lines.append("No case updates recorded yet.")
            for event in updates:
                lines.append("- " + " · ".join(part for part in (
                    event.get("date"), event.get("kind"), event.get("summary"),
                    "ref " + event["reference"] if event.get("reference") else "",
                ) if part))
        return "\n".join(lines)
    return f"Life item saved: **{selected.get('title') or '(untitled)'}** · {detail_for(selected)}"
