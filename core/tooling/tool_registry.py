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

from core.runtime.capability_routing import (
    CAP_CODE_GRAPH,
    CAP_COMPUTER_CONTROL,
    CAP_CURRENT_FACTS,
    CAP_DESIGN,
    CAP_FILES,
    CAP_FILE_ORGANIZATION,
    CAP_GITHUB_REPO,
    CAP_LIFE,
    CAP_MCP,
    CAP_MIGRATION,
    CAP_PERCEPTION,
    CAP_PHONE,
    CAP_PROFILE,
    CAP_SCHEDULING,
    CAP_SCREEN_OBSERVATION,
    CAP_SYSTEMCARE,
    CAP_TRANSFER,
    CAP_VISUALIZATION,
    CAP_WEB,
)
from core.utils.number_utils import as_bounded_int


TOOL_SEARCH_NAME = "tool_search"
CATALOG_MODE_FULL_STATIC = "full_static"
CATALOG_MODE_DEFERRED = "deferred"
CATALOG_MODE_CAPABILITY_ROUTED = "capability_routed"
CATALOG_MODES = frozenset({
    CATALOG_MODE_FULL_STATIC,
    CATALOG_MODE_DEFERRED,
    CATALOG_MODE_CAPABILITY_ROUTED,
})

# The provider receives a task-sized schema set by default. Explicit per-provider
# or per-API configuration may still opt into deferred or full-static catalogs.


CORE_TOOL_NAMES = frozenset({
    TOOL_SEARCH_NAME,
    "read_file",
    "edit_file",
    "find_files",
    "grep",
    "git_status",
    "project_bridge",
    "code_search",
    "project_history",
    "find_callers",
    "find_callees",
    # Graph readers above tell MO to call build_graph when no graph exists at this root.
    # A deferred remedy would sit one tool_search hop from the instruction that names it.
    "build_graph",
    "system_health",
    "set_plan",
    "complete_task",
})

ROUTED_CORE_TOOL_NAMES = frozenset({TOOL_SEARCH_NAME, "system_health", "set_plan", "complete_task"})

# Deep code investigations may need topology diagnostics that
# remain lazy for routine code questions. Whole-project mapping stays explicit.
DEEP_CODE_INVESTIGATION_TOOL_NAMES = frozenset({
    "graph_explain", "graph_neighbors", "graph_path", "graph_stats", "redundancy_scan",
})

CAPABILITY_TOOL_NAMES: dict[str, frozenset[str]] = {
    CAP_FILES: frozenset({
        "read_file", "write_file", "edit_file", "find_files", "grep", "git_status",
    }),
    CAP_FILE_ORGANIZATION: frozenset({
        "find_files", "read_file", "write_file", "shell",
    }),
    CAP_TRANSFER: frozenset({
        "file_transfer", "everywhere_readiness", "everywhere_pair_android",
    }),
    CAP_SCREEN_OBSERVATION: frozenset({
        "computer_targets", "computer_observe", "point_on_screen",
    }),
    CAP_CURRENT_FACTS: frozenset({"web_search", "web_fetch", "shell"}),
    CAP_SCHEDULING: frozenset({"schedule_job"}),
    # MCP-like connected-data wording must retain discovery even when no MCP
    # schema has been loaded yet; the visible catalog is not absence evidence.
    CAP_MCP: frozenset({TOOL_SEARCH_NAME}),
    CAP_CODE_GRAPH: frozenset({
        "read_file", "find_files", "grep", "build_graph", "code_search",
        "project_history", "find_callers", "find_callees",
    }),
    CAP_PERCEPTION: frozenset({"perceive", "show_image"}),
    CAP_DESIGN: frozenset({
        "mo_design", "show_viz", "generate_image", "edit_image", "show_image",
    }),
    CAP_COMPUTER_CONTROL: frozenset({
        "computer_targets", "computer_observe", "computer_act", "point_on_screen",
    }),
    CAP_PHONE: frozenset({
        "phone_context", "phone_click", "phone_set_text", "phone_scroll", "phone_key",
        "phone_files", "phone_storage_report", "phone_file_read", "phone_file_delete",
        "phone_capabilities", "phone_system_status", "phone_cache_report", "phone_cache_trim",
        "phone_packages", "phone_package_action", "phone_shell",
    }),
    CAP_SYSTEMCARE: frozenset({
        "systemcare_inspect",
        "systemcare_status", "systemcare_calibrate", "systemcare_scan", "systemcare_plan",
        "systemcare_apply", "systemcare_rollback", "systemcare_cancel",
    }),
    CAP_WEB: frozenset({"web_search", "web_fetch"}),
    CAP_GITHUB_REPO: frozenset({"inspect_repo", "use_repo"}),
    CAP_PROFILE: frozenset({"record_profile_fact", "read_file", "grep"}),
    CAP_LIFE: frozenset({"life_item", "life_money"}),
    CAP_MIGRATION: frozenset({"migrate"}),
    CAP_VISUALIZATION: frozenset({
        "show_viz", "show_image", "generate_image", "edit_image",
    }),
}


def configured_catalog_modes(agent_config: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize the catalog-mode configuration."""
    config = agent_config if isinstance(agent_config, dict) else {}
    if "deferred_tool_registry_enabled" in config:
        raise ValueError(
            "agent.deferred_tool_registry_enabled is no longer supported; "
            "use agent.tool_catalog_modes"
        )
    raw = config.get("tool_catalog_modes")
    if "tool_catalog_modes" not in config:
        return {"default": CATALOG_MODE_CAPABILITY_ROUTED, "providers": {}, "api_modes": {}}
    if not isinstance(raw, dict):
        raise ValueError("agent.tool_catalog_modes must be a mapping")
    unknown = set(raw) - {"default", "providers", "api_modes"}
    if unknown:
        raise ValueError(f"unknown agent.tool_catalog_modes keys: {', '.join(sorted(unknown))}")
    default = _validated_catalog_mode(raw.get("default", CATALOG_MODE_CAPABILITY_ROUTED), "default")
    providers = _validated_mode_map(raw.get("providers", {}), "providers")
    api_modes = _validated_mode_map(raw.get("api_modes", {}), "api_modes")
    return {"default": default, "providers": providers, "api_modes": api_modes}


def resolve_catalog_mode(
    modes: dict[str, Any] | None,
    *,
    provider_name: object = "",
    api_mode: object = "",
) -> str:
    """Resolve provider-name, API-mode, then default precedence."""
    normalized = configured_catalog_modes({"tool_catalog_modes": modes or {}})
    provider_key = str(provider_name or "").strip().lower()
    api_key = str(api_mode or "").strip().lower()
    if provider_key and provider_key in normalized["providers"]:
        return normalized["providers"][provider_key]
    if api_key and api_key in normalized["api_modes"]:
        return normalized["api_modes"][api_key]
    return normalized["default"]


def _validated_catalog_mode(value: object, location: str) -> str:
    mode = str(value or "").strip().lower()
    if mode not in CATALOG_MODES:
        raise ValueError(f"unknown tool catalog mode at {location}: {value!r}")
    return mode


def _validated_mode_map(value: object, location: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"agent.tool_catalog_modes.{location} must be a mapping")
    normalized: dict[str, str] = {}
    for raw_key, mode in value.items():
        key = str(raw_key).strip().lower()
        if not key:
            raise ValueError(f"agent.tool_catalog_modes.{location} contains an empty key")
        if key in normalized:
            raise ValueError(
                f"agent.tool_catalog_modes.{location} contains duplicate normalized key {key!r}"
            )
        normalized[key] = _validated_catalog_mode(mode, f"{location}.{raw_key}")
    return normalized


def tool_definition_name(definition: dict[str, Any]) -> str:
    """Return a provider tool definition's function name."""
    if not isinstance(definition, dict):
        return ""
    fn = definition.get("function") if isinstance(definition.get("function"), dict) else {}
    return str(fn.get("name") or definition.get("name") or "").strip()


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", " ", str(text or "").lower()).strip()


def _tokens(text: str) -> list[str]:
    return [
        token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token
        for token in re.split(r"[\W_]+", str(text or "").lower()) if token
    ]


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

    def active_names(
        self,
        *,
        mode: str = CATALOG_MODE_DEFERRED,
    ) -> list[str]:
        clean_mode = _validated_catalog_mode(mode, "runtime")
        if clean_mode == CATALOG_MODE_FULL_STATIC:
            active = set(self._order)
        elif clean_mode == CATALOG_MODE_CAPABILITY_ROUTED:
            active = ROUTED_CORE_TOOL_NAMES | self.activated_names
        else:
            active = self.core_names | self.activated_names
        return [name for name in self._order if name in active]

    def active_definitions(
        self,
        *,
        mode: str = CATALOG_MODE_DEFERRED,
    ) -> list[dict[str, Any]]:
        active = set(self.active_names(mode=mode))
        return [definition for name, definition in self._definitions.items() if name in active]

    def capability_names(self, hints: object) -> list[str]:
        hint_values = (
            tuple(str(hint) for hint in hints)
            if isinstance(hints, (set, frozenset, list, tuple))
            else ()
        )
        wanted: set[str] = set()
        for hint in hint_values:
            wanted.update(CAPABILITY_TOOL_NAMES.get(hint, ()))
        if CAP_MCP in hint_values:
            wanted.update(name for name in self._order if name.startswith("mcp__"))
        return [name for name in self._order if name in wanted]

    def definitions_for_mode(
        self,
        mode: str,
        hints: object = (),
        *,
        required_names: object = (),
    ) -> list[dict[str, Any]]:
        clean_mode = _validated_catalog_mode(mode, "runtime")
        if clean_mode == CATALOG_MODE_FULL_STATIC:
            return [self._definitions[name] for name in self._order]
        capability_names = set(self.capability_names(hints))
        required = self._known_names(required_names)
        if clean_mode == CATALOG_MODE_DEFERRED:
            self.activated_names.update(capability_names | required)
            active = self.core_names | self.activated_names
        else:
            active = ROUTED_CORE_TOOL_NAMES | self.activated_names | capability_names | required
        return [self._definitions[name] for name in self._order if name in active]

    def snapshot(
        self,
        *,
        mode: str = CATALOG_MODE_DEFERRED,
    ) -> dict[str, Any]:
        clean_mode = _validated_catalog_mode(mode, "runtime")
        active = self.active_names(mode=clean_mode)
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

    def search(
        self,
        arguments: dict[str, Any],
        *,
        mode: str = CATALOG_MODE_DEFERRED,
    ) -> str:
        args = arguments or {}
        clean_mode = _validated_catalog_mode(mode, "runtime")
        query = str(args.get("query") or "").strip()
        action = str(args.get("action") or "search").strip().lower()
        if action == "list":
            return json.dumps({
                "total": len(self._order),
                "tools": self.catalog_names(),
                "active_tools_next_request": self.active_names(mode=clean_mode),
                "hint": "Complete runtime catalog; listing does not activate schemas. Search exact names for details or activation.",
            }, ensure_ascii=False, indent=2)
        if action != "search":
            return "Error: tool_search action must be search or list."
        requested = self._requested_tool_names(args)
        limit = as_bounded_int(args.get("max_results"), default=8, minimum=1, maximum=20)
        activate_limit = as_bounded_int(args.get("activate_limit"), default=4, minimum=1, maximum=8)

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

        # In the routed catalog, graph readers must carry the existing refresh
        # remedy they name; otherwise a stale result points to an unavailable tool.
        if (
            clean_mode == CATALOG_MODE_CAPABILITY_ROUTED
            and "build_graph" in self._definitions
            and "build_graph" not in self.activated_names
            and "build_graph" not in selected_names
            and any(name in {"code_search", "find_callers", "find_callees"} for name in selected_names)
        ):
            selected_names.append("build_graph")

        active_before = set(self.active_names(mode=clean_mode))
        already_active: list[str] = []
        activated: list[str] = []
        for name in selected_names:
            if name in active_before:
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

        active_next = self.active_names(mode=clean_mode)
        active_next_set = set(active_next)
        payload = {
            "query": query,
            "activated": activated,
            "already_active": already_active,
            "unknown": unknown,
            "active_tools_next_request": active_next,
            "results": [
                self._result_row(name, offered=name in active_next_set)
                for name in result_names
            ],
        }
        if not query and not requested:
            payload["hint"] = "Provide a query like 'edit files' or exact tools such as ['edit_file', 'test_runner']."
        elif not result_names and not activated:
            payload["hint"] = "No matching tools found. Try exact tool names or broader capability terms."
        elif already_active and not activated:
            payload["hint"] = (
                "Matching tool schemas are already active. Call those tools directly; "
                "do not search again for this capability."
            )
        elif clean_mode == CATALOG_MODE_FULL_STATIC:
            payload["hint"] = "Matching tool schemas were already offered in this request."
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

    def _known_names(self, values: object) -> set[str]:
        if not isinstance(values, (set, frozenset, list, tuple)):
            return set()
        return {
            str(name) for name in values
            if str(name) in self._definitions
        }

    def _rank(self, query: str) -> list[tuple[int, str]]:
        query_norm = _normalise(query)
        query_tokens = set(_tokens(query)) - {
            "a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
            "is", "it", "of", "on", "or", "the", "to", "with",
        }
        if not query_tokens:
            return []
        minimum_matches = min(2, len(query_tokens))
        ranked: list[tuple[int, str]] = []
        fallback: list[tuple[int, int, str]] = []
        for name in self._order:
            description = _tool_description(self._definitions[name])
            name_tokens = set(_tokens(name))
            # Prefer the advertised capability to incidental words in usage
            # instructions or generic parameter names.
            summary = description.split(". ", 1)[0]
            summary_tokens = set(_tokens(summary))
            matches = query_tokens & summary_tokens
            exact = _normalise(name) in query_norm.split()
            complete_name = name_tokens <= query_tokens
            name_category = len(query_tokens) == 1 and bool(name_tokens & query_tokens)
            # A long capability list mentioning a couple of query words is not
            # a focused match. Detailed discovery remains available below.
            summary_score = 100 * len(matches) // max(1, len(summary_tokens))
            if exact or complete_name or name_category or (
                len(matches) >= minimum_matches and summary_score >= 10
            ):
                score = (
                    1000 * exact + 200 * complete_name + 25 * name_category
                    + summary_score
                )
                ranked.append((score, name))
            else:
                full_matches = query_tokens & (name_tokens | set(_tokens(description)))
                if full_matches:
                    fallback.append((
                        len(full_matches),
                        25 * len(query_tokens & name_tokens) + 5 * len(full_matches),
                        name,
                    ))
        # Detailed descriptions retain operations absent from their opening
        # summary, but never pad a focused result with weak incidental matches.
        if not ranked and fallback:
            best_overlap = max(item[0] for item in fallback)
            ranked = [(score, name) for overlap, score, name in fallback if overlap == best_overlap]
        ranked.sort(key=lambda item: (-item[0], self._order.index(item[1])))
        return ranked

    def _result_row(self, name: str, *, offered: bool = False) -> dict[str, Any]:
        definition = self._definitions[name]
        return {
            "name": name,
            "active": offered,
            "description": _tool_description(definition)[:240],
            "parameters": _parameter_names(definition),
        }
