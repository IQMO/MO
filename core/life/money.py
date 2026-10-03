"""Private, operator-recorded money movements shown in Life."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import bounded_text as _text
from ..runtime.lock import file_byte_lock
from ..state.paths import resolve_state_path
from ..utils.atomic_write import atomic_write_json


ENTRIES_PATH = "memory/life/money.json"
LOCK_PATH = "memory/life/money.lock"
KINDS = frozenset({"income", "expense"})
_LOCK = threading.Lock()


def _life_link(item_id: Any, *, kind: str, config: dict | None) -> str:
    item_id = _text(item_id, label="Life item ID", limit=32)
    if not item_id:
        return ""
    if kind != "expense":
        raise ValueError("Only an outgoing entry can be linked to a commitment")
    from .items import list_items

    item = next((row for row in list_items(config=config) if row.get("id") == item_id), None)
    if item is None or item.get("category") not in {"payment", "subscription"}:
        raise ValueError("Choose an existing payment or subscription commitment")
    return item_id


def _paths(config: dict | None = None) -> tuple[Path, Path]:
    return Path(resolve_state_path(ENTRIES_PATH, config)), Path(resolve_state_path(LOCK_PATH, config))


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Money entries are unavailable; the saved record was not changed") from exc
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("entries"), list)
            or any(not isinstance(entry, dict) for entry in data["entries"])):
        raise ValueError("Money entries are unavailable; the saved record was not changed")
    return data["entries"]


def _date(value: Any) -> str:
    raw = _text(value, label="Date", limit=10, required=True)
    try:
        if date.fromisoformat(raw).isoformat() != raw:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Date must be YYYY-MM-DD") from exc
    return raw


def _amount(value: Any) -> str:
    raw = str(value).strip()
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d{1,3})?", raw):
        raise ValueError("Amount must be positive, with at most three decimal places")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Amount must be a positive decimal number") from exc
    if (not amount.is_finite() or amount <= 0 or amount > Decimal("999999999999.999")
            or amount.as_tuple().exponent < -3):
        raise ValueError("Amount must be positive, at most three decimal places")
    return format(amount, "f")


def _currency(value: Any) -> str:
    currency = _text(value, label="Currency", limit=3, required=True).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Currency must be a three-letter code")
    return currency


def _kind(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("Choose income or expense")
    return kind


def _month(value: Any) -> str:
    month = str(value or date.today().strftime("%Y-%m")).strip()
    if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month):
        raise ValueError("Month must be YYYY-MM")
    return month


def list_entries(*, month: str = "", config: dict | None = None) -> list[dict[str, Any]]:
    selected = _month(month) if month else ""
    path, lock = _paths(config)
    with file_byte_lock(lock, _LOCK):
        entries = [dict(entry) for entry in _read(path)]
    if selected:
        entries = [entry for entry in entries if str(entry.get("date") or "").startswith(selected + "-")]
    return sorted(entries, key=lambda entry: (entry.get("date") or "", entry.get("created_at") or 0), reverse=True)


def _summary_from_entries(selected: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, dict[str, Any]] = {}
    for entry in entries:
        currency = entry["currency"]
        group = totals.setdefault(currency, {"income": Decimal(0), "expense": Decimal(0), "categories": {}})
        value = Decimal(entry["amount"])
        group[entry["kind"]] += value
        category_key = (entry["kind"], entry["category"].casefold())
        category = group["categories"].setdefault(category_key, {"kind": entry["kind"],
            "category": entry["category"], "total": Decimal(0)})
        category["total"] += value
    currencies = []
    for currency, group in sorted(totals.items()):
        categories = sorted(group["categories"].values(),
                            key=lambda row: (row["kind"], -row["total"], row["category"].casefold()))
        currencies.append({"currency": currency, "income": format(group["income"], "f"),
                           "expense": format(group["expense"], "f"),
                           "net": format(group["income"] - group["expense"], "f"),
                           "categories": [{**row, "total": format(row["total"], "f")} for row in categories]})
    return {"month": selected, "count": len(entries), "currencies": currencies}


def _timeline_from_entries(selected: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    end = date.fromisoformat(selected + "-01")
    months = []
    for offset in range(5, -1, -1):
        year, index = divmod(end.year * 12 + end.month - 1 - offset, 12)
        months.append(f"{year:04d}-{index + 1:02d}")
    totals: dict[str, dict[str, dict[str, Decimal]]] = {}
    for entry in entries:
        month = str(entry.get("date") or "")[:7]
        if month not in months:
            continue
        currency = entry["currency"]
        point = totals.setdefault(currency, {}).setdefault(month, {"income": Decimal(0), "expense": Decimal(0)})
        point[entry["kind"]] += Decimal(entry["amount"])
    return {"months": months, "currencies": [
        {"currency": currency, "points": [
            {"month": month, "income": format(totals[currency].get(month, {}).get("income", Decimal(0)), "f"),
             "expense": format(totals[currency].get(month, {}).get("expense", Decimal(0)), "f")}
            for month in months]}
        for currency in sorted(totals)]}


def month_view(*, month: str = "", config: dict | None = None) -> dict[str, Any]:
    """Return rows and totals from one locked read for a consistent local view."""
    selected = _month(month)
    all_entries = list_entries(config=config)
    entries = [entry for entry in all_entries if str(entry.get("date") or "").startswith(selected + "-")]
    return {"entries": entries, "summary": _summary_from_entries(selected, entries),
            "timeline": _timeline_from_entries(selected, all_entries)}


def monthly_summary(*, month: str = "", config: dict | None = None) -> dict[str, Any]:
    return month_view(month=month, config=config)["summary"]


def linked_counts(*, config: dict | None = None) -> dict[str, int]:
    """Count recorded outgoings per commitment without duplicating the ledger."""
    counts: dict[str, int] = {}
    for entry in list_entries(config=config):
        item_id = str(entry.get("life_item_id") or "")
        if item_id and entry.get("kind") == "expense":
            counts[item_id] = counts.get(item_id, 0) + 1
    return counts


def create_entry(*, title: str, kind: str, amount: Any, currency: str,
                 date: str, category: str, notes: str = "", life_item_id: str = "",
                 config: dict | None = None) -> dict[str, Any]:
    fields = {"title": _text(title, label="Title", limit=180, required=True),
              "kind": _kind(kind), "amount": _amount(amount), "currency": _currency(currency),
              "date": _date(date), "category": _text(category, label="Category", limit=48, required=True),
              "notes": _text(notes, label="Notes", limit=500)}
    fields["life_item_id"] = _life_link(life_item_id, kind=fields["kind"], config=config)
    path, lock = _paths(config)
    with file_byte_lock(lock, _LOCK):
        entries = _read(path)
        if len(entries) >= 5000:
            raise ValueError("Money entry limit reached; review existing entries first")
        now = time.time()
        entry = {"id": "money-" + uuid.uuid4().hex[:12], **fields,
                 "source_provider": "operator", "created_at": now, "updated_at": now, "revision": 1}
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"version": 1, "entries": [*entries, entry]}, indent=2, ensure_ascii=False)
    return dict(entry)


def update_entry(entry_id: str, *, expected_revision: int, changes: dict[str, Any],
                 config: dict | None = None) -> dict[str, Any]:
    validators = {"title": lambda value: _text(value, label="Title", limit=180, required=True),
                  "kind": _kind, "amount": _amount, "currency": _currency, "date": _date,
                  "category": lambda value: _text(value, label="Category", limit=48, required=True),
                  "notes": lambda value: _text(value, label="Notes", limit=500)}
    if not isinstance(changes, dict) or not changes or set(changes) - (validators.keys() | {"life_item_id"}):
        raise ValueError("Choose only money entry fields to update")
    cleaned = {key: validators[key](value) for key, value in changes.items() if key in validators}
    path, lock = _paths(config)
    with file_byte_lock(lock, _LOCK):
        entries = _read(path)
        entry = next((row for row in entries if row.get("id") == entry_id), None)
        if entry is None:
            raise ValueError("Money entry not found")
        if int(entry.get("revision") or 0) != expected_revision:
            raise ValueError("Money entry changed; refresh before editing")
        if "life_item_id" in changes or "kind" in changes:
            cleaned["life_item_id"] = _life_link(changes.get("life_item_id", entry.get("life_item_id")),
                                                    kind=cleaned.get("kind", entry["kind"]), config=config)
        entry.update(cleaned)
        entry["revision"] = expected_revision + 1
        entry["updated_at"] = time.time()
        atomic_write_json(path, {"version": 1, "entries": entries}, indent=2, ensure_ascii=False)
    return dict(entry)


def forget_entry(entry_id: str, *, expected_revision: int, config: dict | None = None) -> bool:
    path, lock = _paths(config)
    with file_byte_lock(lock, _LOCK):
        entries = _read(path)
        entry = next((row for row in entries if row.get("id") == entry_id), None)
        if entry is None:
            raise ValueError("Money entry not found")
        if int(entry.get("revision") or 0) != expected_revision:
            raise ValueError("Money entry changed; refresh before forgetting")
        atomic_write_json(path, {"version": 1, "entries": [row for row in entries if row is not entry]},
                          indent=2, ensure_ascii=False)
    return True


def operator_result(action: str, result: dict[str, Any], *, config: dict | None = None) -> str:
    """Render exact private totals only for the live operator, never the model."""
    if action == "forget":
        return "Money entry forgotten."
    from .items import list_items

    item_titles = {item["id"]: item["title"] for item in list_items(config=config)}

    def link_text(entry: dict[str, Any]) -> str:
        item_id = entry.get("life_item_id")
        return " · for " + item_titles.get(item_id, "former commitment") if item_id else ""

    if action == "list":
        view = month_view(month=str(result.get("month") or ""), config=config)
        summary, entries = view["summary"], view["entries"]
        lines = [f"**Life / Money · {summary['month']}** · {summary['count']} recorded entries"]
        if not entries:
            lines.append("No recorded income or outgoings this month.")
        for group in summary["currencies"][:5]:
            currency = group["currency"]
            lines.append(f"{currency}: income {group['income']} · outgoing {group['expense']} · "
                         f"net recorded {group['net']}")
            outgoing = [row for row in group["categories"] if row["kind"] == "expense"]
            for category in outgoing[:12]:
                lines.append(f"  - {category['category']}: {category['total']} {currency}")
            if len(outgoing) > 12:
                lines.append(f"  Showing 12 of {len(outgoing)} outgoing categories in Dashboard → Life.")
        if len(summary["currencies"]) > 5:
            lines.append(f"Showing 5 of {len(summary['currencies'])} currencies in Dashboard → Life.")
        for entry in entries[:20]:
            lines.append(f"- {entry['date']} · {entry['title']} · {entry['kind']} "
                         f"{entry['amount']} {entry['currency']} · {entry['category']}{link_text(entry)}")
        if len(entries) > 20:
            lines.append(f"Showing 20 of {len(entries)} entries. Open Dashboard → Life for the full month.")
        lines.append("Recorded entries only; not an account balance.")
        return "\n".join(lines)
    selected = next((entry for entry in list_entries(config=config)
                     if entry.get("id") == result.get("id")), None)
    if selected is None:
        return "Money entry saved. Open Dashboard → Life for its details."
    return (f"Money entry saved: **{selected['title']}** · {selected['date']} · "
            f"{selected['kind']} {selected['amount']} {selected['currency']} · {selected['category']}"
            f"{link_text(selected)}")
