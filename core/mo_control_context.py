"""MO control-workspace context for cross-repo/server operations.

The control workspace is the active operator-owned instruction/memory bridge,
resolved ONLY from the operator's private config (`mo_control.workspace_path`)
or the MO_CONTROL_WORKSPACE env var — never from hardcoded paths. This module
gives the provider a compact authority/context block when requests touch
repos, deployments, servers, secrets, or MO runtime ownership.

It is orientation and policy context, not proof: live checks still win.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

ENV_MO_CONTROL_WORKSPACE = "MO_CONTROL_WORKSPACE"

# Generic, operator-agnostic triggers only. Operator project/codename terms
# come from private config: mo_control.trigger_terms (list of strings).
_TRIGGER_RE = re.compile(
    r"\b(github|repo|commit|push|pull|deploy|"
    r"production|server|vps|telegram|service|systemd|scheduler|cron|secret|secrets|credential|"
    r"credentials|vault|key|keys|owner|ownership|responsib|responsab|overwrite|conflict|agent|agents|"
    r"\.mo|mo agent)\b",
    re.I,
)


def _operator_trigger_terms(config: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Operator-specific trigger vocabulary from private config (never hardcoded)."""
    cfg = config or {}
    try:
        raw = (cfg.get("mo_control") or {}).get("trigger_terms") or []
    except Exception:
        raw = []
    terms = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        term = str(item or "").strip().lower()
        if term:
            terms.append(term)
    return tuple(dict.fromkeys(terms))

_RELEVANT_DOCS = (
    "SOURCE_OF_TRUTH.md",
    "SECURITY.md",
    "SYNC.md",
    "FACTS.md",
    "TOOLS.md",
    "CREDENTIALS_MAP.md",
    "AGENTS.md",
)

_DOC_PATTERNS = (
    "authority", "live checks", "owner", "operator", "secret", "credential", "vault",
    "force-push", "deploy", "rsync", "delete", "overwrite", "stage specific",
    "github", "telegram", "poller", "workspace",
    "no-overwrite", "production", "live-money", "workflow_dispatch", "systemd",
)


def _active_rules(operator_label: str) -> tuple[str, ...]:
    owner = operator_label or "the configured operator"
    return (
        f"{owner} is the owner/operator for this runtime. MO Agent is the delegated operator; it must not act above the owner or bypass explicit approval boundaries.",
        "Live checks beat docs. Verify files, repo state, services, logs, and server state before factual claims or deploy/commit decisions.",
        "Never print secrets, .env values, tokens, private keys, OAuth material, wallet keys, or credential JSON values. Use credential file paths/status only.",
        "Keep unrelated repos/products separated. Stage exact reviewed files only; do not broad-stage unrelated work.",
        f"Never overwrite, stash, reset, clean, force-push, rsync-delete, or deploy over dirty/unknown/other-agent work unless {owner} explicitly approves the exact target.",
        "Production deploys must use reviewed changed paths only; do not use broad deploy helpers over unknown work.",
        "Live-money or production-risk code requires small scoped changes, real tests, and no restart/deploy/runtime change without explicit approval.",
        "Telegram has one active poller. Do not start a second polling service for the same bot token.",
        "External/old-agent skills or docs are reference only until curated into MO workflow candidates and explicitly approved.",
    )


def should_include_mo_control_context(user_input: str, config: dict[str, Any] | None = None) -> bool:
    """Return True when MO control/workspace policy should be injected."""
    text = str(user_input or "")
    if _TRIGGER_RE.search(text):
        return True
    lowered = text.lower()
    return any(
        re.search(rf"\b{re.escape(term)}\b", lowered)
        for term in _operator_trigger_terms(config)
    )


def build_mo_control_context(
    *,
    user_input: str = "",
    config: dict[str, Any] | None = None,
    max_chars: int = 2600,
) -> str:
    """Build a compact active-control context block.

    The block deliberately includes a few hard-coded active rules so MO remains
    safe even when the workspace checkout is missing. When workspace docs exist,
    it appends relevant line snippets as source pointers.
    """
    workspace = resolve_mo_control_workspace(config)
    operator_label = _operator_label(config)
    lines = [
        "### MO Operator / Cross-Repo & Server Authority",
        "Purpose: keep MO Agent aligned with the active operator/repo/server rules. This is policy/orientation; live checks still win.",
        "Active rules:",
    ]
    lines.extend(f"- {rule}" for rule in _active_rules(operator_label))
    # Optional external workspace bridge (disabled when no path is configured).
    snippets = _workspace_snippets(workspace, user_input=user_input, max_lines=18, config=config) if workspace else []
    if workspace and snippets:
        lines.append(f"Operator policy anchors (`{workspace}`):")
        lines.extend(snippets)
    return _cap_text("\n".join(lines), max_chars)


def _operator_label(config: dict[str, Any] | None = None) -> str:
    cfg = config or {}
    for section, key in (("mo_control", "owner_label"), ("operator", "name"), ("user", "name")):
        try:
            value = str(((cfg.get(section) or {}).get(key)) or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return "the configured operator"


def resolve_mo_control_workspace(config: dict[str, Any] | None = None) -> Path | None:
    """Resolve the operator's control workspace from config or env ONLY.

    The workspace is runtime-private operator data (`mo_control.workspace_path`
    in the private config, or MO_CONTROL_WORKSPACE). Product code must never
    hardcode operator-specific paths — every user gets their own workspace via
    their own config, per the AGENTS.md operator-profile rule.
    """
    cfg = config or {}
    configured = ""
    try:
        configured = str(((cfg.get("mo_control") or {}).get("workspace_path")) or "").strip()
    except Exception:
        configured = ""
    candidates = [
        configured,
        os.getenv(ENV_MO_CONTROL_WORKSPACE, ""),
    ]
    for item in candidates:
        if not item:
            continue
        path = Path(item)
        if path.exists() and path.is_dir():
            return path
    return None


def _workspace_snippets(workspace: Path, *, user_input: str, max_lines: int, config: dict[str, Any] | None = None) -> list[str]:
    terms = _query_terms(user_input, config=config)
    out: list[str] = []
    for name in _RELEVANT_DOCS:
        path = workspace / name
        if not path.exists() or not path.is_file():
            continue
        matches = _extract_lines(path, terms=terms, limit=max(1, max_lines // 2))
        if not matches:
            continue
        out.append(f"- `{name}`:")
        out.extend(f"  - L{line_no}: {text}" for line_no, text in matches[: max(1, max_lines - len(out))])
        if len(out) >= max_lines:
            break
    return out[:max_lines]


def _query_terms(text: str, config: dict[str, Any] | None = None) -> tuple[str, ...]:
    raw = {m.group(0).lower() for m in re.finditer(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(text or ""))}
    generic = {"telegram", "github", "deploy", "commit", "push", "secret", "secrets", "credential", "vps", "server", "agent", "agents"}
    operator_terms = set(_operator_trigger_terms(config))
    terms = [t for t in raw if t in generic or t in operator_terms]
    return tuple(dict.fromkeys([*terms, *operator_terms, *_DOC_PATTERNS]))


def _extract_lines(path: Path, *, terms: Iterable[str], limit: int) -> list[tuple[int, str]]:
    wanted = tuple(str(t).lower() for t in terms if t)
    matches: list[tuple[int, str]] = []
    try:
        for idx, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            line = " ".join(raw.strip().split())
            if not line or len(line) < 4:
                continue
            low = line.lower()
            if any(term in low for term in wanted):
                if len(line) > 220:
                    line = line[:219] + "…"
                matches.append((idx, line))
                if len(matches) >= limit:
                    break
    except Exception:
        return []
    return matches


def _cap_text(text: str, max_chars: int) -> str:
    value = str(text or "").strip()
    if not max_chars or len(value) <= max_chars:
        return value
    marker = "\n[MO control context truncated]"
    return value[: max(0, max_chars - len(marker))].rstrip() + marker
