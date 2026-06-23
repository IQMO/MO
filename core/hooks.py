"""User-facing lifecycle hooks on MO's monitor event stream.

A small operator-owned YAML file maps monitor event types to shell commands.
The monitor event stream is the only trigger source, so hooks observe exactly
what the operator can already see in the monitor; they never gain runtime
authority.

Config lives in the personalization lane (``~/.mo/hooks.yaml``), never in the
product repo:

    enabled: true
    hooks:
      - event: turn_end          # monitor event type; fnmatch patterns allowed
        match: ""                # optional substring filter on the payload JSON
        run: "notify-send MO done"   # shell command (operator-trusted)

Hook commands receive ``MO_HOOK_EVENT`` and ``MO_HOOK_PAYLOAD`` (redacted,
truncated JSON) in their environment, run detached fire-and-forget, and can
never raise into — or block — MO's turn loop. The hooks file has the same
trust level as ``config.yaml``: operator-owned local configuration.

SECURITY — full-trust code execution. Hook ``run`` strings are executed with
``shell=True`` and inherit the full process environment (``os.environ``), so a
hook can read any secret the MO process can (``.env`` values, tokens). Only the
*payload* text is redacted — the command and its environment are not sandboxed.
Treat ``~/.mo/hooks.yaml`` exactly like a shell script you run as yourself:
anyone who can write it (or your environment) can run arbitrary code with your
privileges. This is by design (like git hooks), not a sandbox boundary.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PAYLOAD_ENV_LIMIT = 2000


def hooks_path() -> Path:
    """Default hooks config location in MO's private runtime home."""
    from .path_defaults import mo_home
    return mo_home() / "hooks.yaml"


_cache: dict[str, Any] = {"path": None, "mtime": None, "hooks": []}


def load_hooks(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load enabled hook entries from the YAML config; [] on any problem."""
    target = Path(path) if path else hooks_path()
    try:
        mtime = target.stat().st_mtime if target.exists() else None
    except Exception:
        mtime = None
    if _cache["path"] == str(target) and _cache["mtime"] == mtime:
        return list(_cache["hooks"])
    hooks: list[dict[str, Any]] = []
    try:
        if mtime is not None:
            import yaml
            data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and bool(data.get("enabled")):
                for entry in data.get("hooks") or []:
                    if not isinstance(entry, dict):
                        continue
                    event = str(entry.get("event") or "").strip()
                    run = str(entry.get("run") or "").strip()
                    if not event or not run:
                        continue
                    hooks.append({
                        "event": event,
                        "match": str(entry.get("match") or ""),
                        "run": run,
                    })
    except Exception:
        hooks = []
    _cache.update({"path": str(target), "mtime": mtime, "hooks": hooks})
    return list(hooks)


def matching_hooks(event_type: str, payload: dict[str, Any], hooks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hooks whose event pattern matches and optional substring hits the payload."""
    if not hooks:
        return []
    try:
        payload_text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        payload_text = str(payload)
    matched = []
    for hook in hooks:
        if not fnmatch.fnmatch(str(event_type), hook["event"]):
            continue
        if hook["match"] and hook["match"] not in payload_text:
            continue
        matched.append(hook)
    return matched


def dispatch_hooks(event_type: str, payload: dict[str, Any], *, path: str | Path | None = None) -> int:
    """Fire matching hooks detached; return how many were launched.

    Best-effort only, mirroring the monitor contract: failures are swallowed so
    a broken hook never changes MO's provider/tool/session behavior. Suppressed
    under pytest unless an explicit ``path`` is given (test isolation, matching
    the audit-writer guards).
    """
    if path is None and os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("MO_HOOKS_FORCE") != "1":
        return 0
    try:
        hooks = load_hooks(path)
        if not hooks:
            return 0
        matched = matching_hooks(event_type, payload if isinstance(payload, dict) else {}, hooks)
        if not matched:
            return 0
        from .backend_monitor import redact_monitor_text
        try:
            payload_text = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            payload_text = str(payload)
        env = {
            **os.environ,
            "MO_HOOK_EVENT": str(event_type),
            "MO_HOOK_PAYLOAD": redact_monitor_text(payload_text, PAYLOAD_ENV_LIMIT),
        }
        launched = 0
        for hook in matched:
            try:
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
                subprocess.Popen(
                    hook["run"],
                    shell=True,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                launched += 1
            except Exception:
                continue
        return launched
    except Exception:
        return 0
