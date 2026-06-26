"""Deferred provider tool registry.

MO keeps the complete executable tool catalog locally, but provider requests
should not carry every schema on every round.  This registry exposes a small
always-on core plus ``tool_search``; search calls activate matching schemas for
the next provider request.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


TOOL_SEARCH_NAME = "tool_search"


CORE_TOOL_NAMES = frozenset({
    TOOL_SEARCH_NAME,
    "read_file",
    "find_files",
    "grep",
    "git_status",
    "project_bridge",
    "code_search",
    "find_callers",
    "find_callees",
    "complete_task",
})


def tool_definition_name(definition: dict[str, Any]) -> str:
    """Return a provider tool definition's function name."""
    if not isinstance(definition, dict):
        return ""
    fn = definition.get("function") if isinstance(definition.get("function"), dict) else {}
    return str(fn.get("name") or definition.get("name") or "").strip()


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", " ", str(text or "").lower()).strip()


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\W_]+", str(text or "").lower()) if t]


def _tool_description(definition: dict[str, Any]) -> str:
    fn = definition.get("function") if isinstance(definition.get("function"), dict) else {}
    return str(fn.get("description") or "")


def _parameter_names(definition: dict[str, Any]) -> list[str]:
    fn = definition.get("function") if isinstance(definition.get("function"), dict) else {}
    params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
    props = params.get("properties") if isinstance(params.get("properties"), dict) else {}
    return [str(name) for name in props.keys()]


@dataclass
class ToolActivationEvent:
    query: str
    requested: list[str] = field(default_factory=list)
    activated: list[str] = field(default_factory=list)
    already_active: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


class DeferredToolRegistry:
    """Local catalog with per-turn activation state."""

    def __init__(self, definitions: list[dict[str, Any]], *, core_names: set[str] | None = None):
        self.core_names = frozenset(core_names or CORE_TOOL_NAMES)
        self._definitions: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self.activated_names: set[str] = set()
        self.activation_ledger: list[ToolActivationEvent] = []
        self.set_definitions(definitions)

    def set_definitions(self, definitions: list[dict[str, Any]]) -> None:
        old_activated = set(self.activated_names)
        self._definitions = {}
        self._order = []
        for definition in definitions or []:
            name = tool_definition_name(definition)
            if not name or name in self._definitions:
                continue
            self._definitions[name] = definition
            self._order.append(name)
        self.activated_names = old_activated & set(self._definitions)

    def catalog_names(self) -> list[str]:
        return list(self._order)

    def matches_catalog(self, definitions: list[dict[str, Any]]) -> bool:
        return [tool_definition_name(d) for d in definitions or [] if tool_definition_name(d)] == self._order

    def reset_turn(self) -> None:
        self.activated_names.clear()
        self.activation_ledger.clear()

    def active_names(self) -> list[str]:
        active = self.core_names | self.activated_names
        return [name for name in self._order if name in active]

    def active_definitions(self) -> list[dict[str, Any]]:
        active = set(self.active_names())
        return [definition for name, definition in self._definitions.items() if name in active]

    def snapshot(self) -> dict[str, Any]:
        active = self.active_names()
        return {
            "total": len(self._definitions),
            "active": len(active),
            "active_tools": active,
            "activated_tools": [name for name in self._order if name in self.activated_names],
            "ledger": [
                {
                    "query": event.query,
                    "requested": event.requested,
                    "activated": event.activated,
                    "already_active": event.already_active,
                    "unknown": event.unknown,
                }
                for event in self.activation_ledger
            ],
        }

    def search(self, arguments: dict[str, Any]) -> str:
        args = arguments or {}
        query = str(args.get("query") or "").strip()
        requested = self._requested_tool_names(args)
        limit = self._bounded_int(args.get("max_results"), default=8, minimum=1, maximum=20)
        activate_limit = self._bounded_int(args.get("activate_limit"), default=4, minimum=1, maximum=8)

        explicit_matches: list[tuple[int, str]] = []
        unknown: list[str] = []
        for name in requested:
            if name in self._definitions:
                explicit_matches.append((10_000, name))
            else:
                unknown.append(name)

        ranked = self._rank(query)
        selected_names: list[str] = []
        for _, name in explicit_matches + ranked:
            if name not in selected_names:
                selected_names.append(name)
            if len(selected_names) >= activate_limit:
                break

        already_active: list[str] = []
        activated: list[str] = []
        for name in selected_names:
            if name in self.core_names or name in self.activated_names:
                already_active.append(name)
            else:
                self.activated_names.add(name)
                activated.append(name)

        event = ToolActivationEvent(
            query=query,
            requested=requested,
            activated=activated,
            already_active=already_active,
            unknown=unknown,
        )
        self.activation_ledger.append(event)

        result_names: list[str] = []
        for _, name in explicit_matches + ranked:
            if name not in result_names:
                result_names.append(name)
            if len(result_names) >= limit:
                break

        payload = {
            "query": query,
            "activated": activated,
            "already_active": already_active,
            "unknown": unknown,
            "active_tools_next_request": self.active_names(),
            "results": [self._result_row(name) for name in result_names],
        }
        if not query and not requested:
            payload["hint"] = "Provide a query like 'edit files' or exact tools such as ['edit_file', 'test_runner']."
        elif not result_names and not activated:
            payload["hint"] = "No matching tools found. Try exact tool names or broader capability terms."
        else:
            payload["hint"] = "Activated tool schemas are available on the next provider request."
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _requested_tool_names(self, args: dict[str, Any]) -> list[str]:
        raw = args.get("tools")
        if raw is None:
            raw = args.get("names")
        if raw is None:
            raw = args.get("tool")
        if isinstance(raw, str):
            parts = re.split(r"[\s,]+", raw)
        elif isinstance(raw, list):
            parts = [str(item) for item in raw]
        else:
            parts = []
        names: list[str] = []
        for part in parts:
            name = str(part or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    def _rank(self, query: str) -> list[tuple[int, str]]:
        query_norm = _normalise(query)
        query_tokens = _tokens(query)
        if not query_norm and not query_tokens:
            return []
        ranked: list[tuple[int, str]] = []
        for name in self._order:
            definition = self._definitions[name]
            desc = _tool_description(definition)
            params = _parameter_names(definition)
            name_norm = _normalise(name)
            haystack = _normalise(" ".join([name, desc, " ".join(params)]))
            score = 0
            if query_norm == name_norm:
                score += 1000
            if query_norm and query_norm in name_norm:
                score += 200
            if query_norm and query_norm in haystack:
                score += 50
            name_tokens = set(_tokens(name))
            hay_tokens = set(_tokens(" ".join([name, desc, " ".join(params)])))
            for token in query_tokens:
                if token in name_tokens:
                    score += 25
                if token in hay_tokens:
                    score += 5
            if score:
                ranked.append((score, name))
        ranked.sort(key=lambda item: (-item[0], self._order.index(item[1])))
        return ranked

    def _result_row(self, name: str) -> dict[str, Any]:
        definition = self._definitions[name]
        return {
            "name": name,
            "active": name in self.core_names or name in self.activated_names,
            "description": _tool_description(definition)[:240],
            "parameters": _parameter_names(definition),
        }

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))
