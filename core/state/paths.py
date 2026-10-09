"""Centralized local path defaults for MO."""

import hashlib
import os
from pathlib import Path
from typing import Any


ENV_DEFAULT_ROOTS = "MO_DEFAULT_ROOTS"
ENV_CODEX_AUTH_PATH = "MO_CODEX_AUTH_PATH"
ENV_CODEX_AUTH_PATH_COMPAT = "CODEX_AUTH_PATH"
ENV_MO_CONFIG = "MO_CONFIG"
ENV_MO_PROJECT_CWD = "MO_PROJECT_CWD"
ENV_MO_STATE_HOME = "MO_STATE_HOME"
ENV_MO_STATE_LOCAL = "MO_STATE_LOCAL"  # opt OUT of private-by-default → project-relative state
ENV_MO_LOCAL_EXTENSION_ROOT = "MO_LOCAL_EXTENSION_ROOT"
ENV_TASKBOARD_LEDGER_PATH = "MO_TASKBOARD_LEDGER_PATH"
ENV_TASKBOARD_LEDGER_DISABLE = "MO_TASKBOARD_LEDGER_DISABLE"
ENV_HEARTBEAT_LEDGER_PATH = "MO_HEARTBEAT_LEDGER_PATH"
ENV_HEARTBEAT_LEDGER_DISABLE = "MO_HEARTBEAT_LEDGER_DISABLE"


def repo_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def mo_home(config: dict[str, Any] | None = None) -> Path:
    """Return MO's private runtime home.

    This is where user state belongs for installed runtimes.
    Relative runtime state resolves here when private state is enabled.

    Precedence: explicit process environment (``MO_STATE_HOME``) >
    config ``runtime.home`` > ``~/.mo`` — matching ``local_extension_root``.
    An env override is per-process intent (isolated E2E homes depend on it);
    a config value pinning the home must not defeat it, or an isolated probe
    resolves config-aware state to the live profile while config-less writers
    honor the env, splitting one process across two homes.
    """
    cfg = config or {}
    configured = ""
    try:
        configured = str(((cfg.get("runtime") or {}).get("home")) or "").strip()
    except Exception:
        configured = ""
    raw = os.getenv(ENV_MO_STATE_HOME) or configured or "~/.mo"
    return Path(raw).expanduser().resolve(strict=False)


def local_extension_root(config: dict[str, Any] | None = None) -> Path:
    """Resolve the profile-owned local extension root.

    Resolution order: ``MO_LOCAL_EXTENSION_ROOT`` env > ``~/.mo/operator`` under
    the user's MO profile.
    Local extension files never ship, so the product checkout is never a valid
    implicit source for them. Returns the home location by default even when
    absent; an empty profile has no extension module or local token, so extension
    behavior stays off.
    """
    env = os.getenv(ENV_MO_LOCAL_EXTENSION_ROOT, "").strip()
    if env:
        return Path(env).expanduser().resolve(strict=False)
    return mo_home(config) / "operator"


def private_state_enabled(config: dict[str, Any] | None = None) -> bool:
    """True when relative runtime state resolves under `mo_home()` (``~/.mo``).

    Private-by-default: MO keeps its profile, memory, sessions, logs, and caches in
    the user's private home regardless of the cwd it is launched from — like
    ``~/.claude`` — instead of scattering state into whatever project folder it runs
    in. This is the only safe default for a tool users run inside their own repos.

    Explicit opt-out to project-relative state (for a dev checkout that wants state
    in the tree): ``runtime.state: project`` (or ``local``/``cwd``/``relative``) in
    config, or ``MO_STATE_LOCAL=1`` in the environment.
    """
    cfg = config or {}
    runtime = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
    state = str(runtime.get("state", "")).strip().lower()
    # 1. Explicit config state is the most specific signal — it wins over ambient env.
    if state in {"private", "home", "mo_home"}:
        return True
    if state in {"project", "local", "cwd", "relative"}:
        return False
    # 2. Explicit config home implies private.
    if runtime.get("home"):
        return True
    # 3. Ambient env opt-out to project-local.
    if str(os.getenv(ENV_MO_STATE_LOCAL, "")).strip().lower() in {"1", "true", "yes", "on"}:
        return False
    # 4. Default: private home, never the project cwd.
    return True


def resolve_state_path(path: str | Path | None, config: dict[str, Any] | None = None, *, default: str = "") -> str:
    """Resolve a runtime-state path without polluting arbitrary project folders.

    Absolute paths are preserved. Relative paths remain project-relative only
    when private state is explicitly disabled; otherwise they resolve under
    `~/.mo` (or configured `runtime.home`).
    """
    value = str(path or default or "").strip()
    if not value:
        return ""
    p = Path(value).expanduser()
    if p.is_absolute() or not private_state_enabled(config):
        return str(p)
    return str((mo_home(config) / p).resolve(strict=False))


def project_cwd(default: str | Path | None = None) -> Path:
    """Return the user/project working directory MO should operate on."""
    raw = os.getenv(ENV_MO_PROJECT_CWD) or str(default or os.getcwd())
    return Path(raw).expanduser().resolve(strict=False)


def default_config_path(*, agent_root: str | Path | None = None, caller_cwd: str | Path | None = None) -> str:
    """Resolve the default config for called-from-anywhere entrypoints.

    Runtime defaults are private by default: `~/.mo/config.yaml` is the active
    implicit config. A checkout-local `config.yaml` is treated as an explicit
    developer override only when selected with `MO_CONFIG` or a CLI `--config`.
    This keeps ignored root files from becoming hidden runtime contracts.
    """
    env = os.getenv(ENV_MO_CONFIG, "").strip()
    if env:
        configured = Path(env).expanduser()
        if configured.is_absolute():
            return str(configured.resolve(strict=False))
        base = Path(caller_cwd or os.getenv(ENV_MO_PROJECT_CWD) or os.getcwd()).expanduser()
        return str((base / configured).resolve(strict=False))

    return str((mo_home() / "config.yaml").resolve(strict=False))


def runtime_config_path(
    config: dict[str, Any] | None = None,
    *,
    fallback_to_default: bool = False,
) -> str:
    """Return the config file that produced ``config``.

    ``load_config`` stamps the resolved source as ``_config_path``. Consumers
    that launch another MO surface or reopen the active config must reuse that
    stamp instead of independently resolving the default profile file.
    """
    value = ""
    if isinstance(config, dict):
        value = str(config.get("_config_path") or "").strip()
    if value:
        return value
    return default_config_path() if fallback_to_default else ""


def project_cache_dir(kind: str, root: str | Path, config: dict[str, Any] | None = None) -> Path:
    """Private per-project cache directory for graph/index artifacts."""
    base = Path(resolve_state_path("cache", config)).resolve(strict=False)
    root_text = str(Path(root).expanduser().resolve(strict=False)).replace("\\", "/").lower()
    digest = hashlib.sha256(root_text.encode("utf-8", errors="replace")).hexdigest()[:16]
    safe_kind = "".join(ch for ch in str(kind or "project") if ch.isalnum() or ch in "-_") or "project"
    return base / safe_kind / digest


RUNTIME_PREFERENCES_PATH = "preferences.json"
RUNTIME_PREFERENCES_LOCK_PATH = f"{RUNTIME_PREFERENCES_PATH}.lock"
PROFILE_DB_PATH = "memory/mo.db"
PROFILE_PROSE_ROLES = {
    "operator.md": "curated identity, projects, and operating context",
    "thinking_model.md": "reasoning and decision preferences",
    "terms.md": "operator-defined vocabulary and shorthand",
    "learning.md": "historical learning-event ledger",
    "behavior.md": "active accepted rules projected from learning",
    "facts.md": "exact durable operational facts and preferences",
}
PROFILE_PROSE_FILES = tuple(PROFILE_PROSE_ROLES)
LEARNING_DB_PATH = "memory/learning/episodes.sqlite"
LEARNING_SUGGESTIONS_PATH = "memory/learning/suggestions.jsonl"
WORKFLOW_CANDIDATES_PATH = "memory/learning/workflows/candidates.jsonl"
OPERATOR_MESSAGE_RECEIPTS_PATH = "memory/learning/operator-message-receipts.jsonl"
OPERATOR_MESSAGE_RECONCILE_PATH = "memory/learning/operator-message-reconcile.json"
PRODUCT_INTENT_CANDIDATES_PATH = "memory/work/product-intent/candidates.jsonl"
TASKBOARD_LEDGER_DIR = "memory/work/taskboards"
TASKBOARD_LEDGER_PATH = f"{TASKBOARD_LEDGER_DIR}/taskboards.jsonl"
GOAL_RUNS_DIR = "memory/work/goals"
REVIEW_HISTORY_DIR = "memory/work/reviews"
GAME_COLLABORATION_DIR = "memory/work/game-collaboration"
# Conversation/handoff subdirectories are derived by their owner,
# core/session/sessions.py (conversation_sessions_dir / handoff_sessions_dir).
SESSION_ROOT_DIR = "memory/sessions"
SESSION_CLOSEOUT_DIR = f"{SESSION_ROOT_DIR}/closeouts"
TELEGRAM_DB_PATH = "memory/surfaces/telegram.sqlite"
EVERYWHERE_HUB_DB_PATH = "memory/surfaces/everywhere.sqlite"
EVERYWHERE_DEVICE_DB_PATH = "memory/surfaces/everywhere-device.sqlite"
FILE_TRANSFER_DB_PATH = "memory/transfers.sqlite"
FILE_TRANSFER_SPOOL_DIR = "memory/transfers"
HEARTBEAT_LEDGER_DIR = "run/heartbeat"
HEARTBEAT_LEDGER_PATH = f"{HEARTBEAT_LEDGER_DIR}/heartbeats.jsonl"
TRACE_DIR = "logs/traces"
FILE_OPERATIONS_PATH = "logs/file_operations.jsonl"
DASHBOARD_DIR = "cache/dashboard"
DASHBOARD_HTML_PATH = f"{DASHBOARD_DIR}/mo_dashboard.html"
DESKTOP_ISSUE_REPORT_DIR = "logs/desktop_issue_reports"
MEDIA_GENERATED_DIR = "media/generated"
MO_DESIGNS_DIR = "media/designs"
MO_EXPLAINERS_DIR = "media/explainers"
MEDIA_ATTACHMENTS_DIR = "media/attachments"
MO_DESIGN_RUNTIME_DIR = "run/design-preview"
MO_DESIGN_HANDOFF_DIR = "run/design-handoffs"
VOICE_RUNTIME_DIR = "models/voice"


def default_project_roots(config: dict[str, Any] | None = None) -> list[str]:
    """Return guarded default roots for local tool access.

    Returns an empty list when access.mode is ``"full"``, which disables path
    gating entirely in the sandbox.  An empty list makes ``path_allowed()``
    return ``True`` for every path while still enforcing hard boundaries
    (credentials, destructive ops, escapes, etc.).
    """
    env = os.getenv(ENV_DEFAULT_ROOTS, "")
    if env:
        parts = [p.strip() for p in env.replace(";", "\n").splitlines() if p.strip()]
        if parts:
            return _dedupe_roots([_resolve_project_root_value(p) for p in parts])

    cfg = config or {}
    access = cfg.get("access") or {}
    if isinstance(access, dict) and str(access.get("mode", "")).lower() == "full":
        return []  # full access — path gating disabled

    configured = access.get("default_roots") if isinstance(access, dict) else None
    if isinstance(configured, list) and configured:
        return _dedupe_roots([_resolve_project_root_value(str(p)) for p in configured])

    if isinstance(access, dict) and str(access.get("mode", "")).lower() in {"project", "cwd", "safe"}:
        return _dedupe_roots([str(project_cwd())])

    return _dedupe_roots([repo_root()])


def _resolve_project_root_value(value: str) -> str:
    p = Path(str(value or "").strip()).expanduser()
    if p.is_absolute():
        return str(p)
    return str((project_cwd() / p).resolve(strict=False))


def _dedupe_roots(roots: list[str]) -> list[str]:
    """Resolve and deduplicate model-visible project roots."""
    result: list[str] = []
    seen: set[str] = set()
    for value in roots:
        resolved = str(Path(value).expanduser().resolve(strict=False))
        key = resolved.replace("\\", "/").lower()
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def codex_auth_path(configured: str | None = None) -> str:
    if configured:
        return str(configured)
    env = os.getenv(ENV_CODEX_AUTH_PATH) or os.getenv(ENV_CODEX_AUTH_PATH_COMPAT)
    if env:
        return env
    return str(Path.home() / ".codex" / "auth.json")
