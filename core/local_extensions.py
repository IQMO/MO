"""Neutral bridge for profile-owned local extensions.

The product checkout owns this loader only. Extension commands, activation
phrases, board rows, runtime loops, and closeout behavior live in the user's
private MO profile and are absent in a fresh profile.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import importlib.util
from pathlib import Path
import re
from types import ModuleType
from typing import Any

from .state.paths import local_extension_root, mo_home

_HOOK_FILENAMES = ("local_extension.py",)
_CACHE_KEY: tuple[str, str, bool] | None = None
_CACHE_MODULE: ModuleType | None = None
_CACHE_ATTEMPTED = False
_CONFIG_ENABLED: bool | None = None
_CONFIG_HOME: str | None = None
_CONFIGURATION_REVISION = 0
_PATH_BOUNDARY_CONTEXT_KEY = "profile_extension_path_boundary"
_DESKTOP_APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_DESKTOP_APP_HOST_KEYS = frozenset({"root", "visual_state", "monitor_anchor", "notify"})
_SAFE_EXCEPTION_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")


def configure(config: dict[str, Any] | None) -> None:
    """Apply the runtime-owned local-extension admission setting.

    ``local_extensions.enabled`` in config is the only admission source; a
    profile without the setting keeps extensions off.
    """
    global _CONFIG_ENABLED, _CONFIG_HOME, _CACHE_ATTEMPTED, _CONFIGURATION_REVISION
    section = config.get("local_extensions") if isinstance(config, dict) else None
    _CONFIG_ENABLED = bool(section.get("enabled")) if isinstance(section, dict) and "enabled" in section else None
    _CONFIG_HOME = str(mo_home(config)) if isinstance(config, dict) else None
    _CACHE_ATTEMPTED = False
    _CONFIGURATION_REVISION += 1


def configuration_revision() -> int:
    """Cheap invalidation token for cached projections of admitted extensions."""
    return _CONFIGURATION_REVISION


def _runtime_home() -> Path:
    return Path(_CONFIG_HOME) if _CONFIG_HOME else mo_home()


def _extension_root() -> Path:
    config = {"runtime": {"home": _CONFIG_HOME}} if _CONFIG_HOME else None
    return local_extension_root(config)


def _extensions_enabled() -> bool:
    return bool(_CONFIG_ENABLED if _CONFIG_ENABLED is not None else DEFAULT_PREFERENCES["local_extensions.enabled"])


def _hook_path() -> Path | None:
    root = _extension_root()
    for rel in _HOOK_FILENAMES:
        path = root / rel
        try:
            if path.is_file():
                return path
        except Exception:
            continue
    return None


def _exception_type_name(exc: BaseException) -> str:
    if type(exc).__module__ != "builtins":
        return "Exception"
    name = type(exc).__name__
    return name if _SAFE_EXCEPTION_TYPE_RE.fullmatch(name) else "Exception"


def _emit_extension_error(stage: str, hook: str, exc: BaseException) -> None:
    try:
        from .runtime.backend_monitor import get_monitor

        monitor = get_monitor()
        if monitor is None:
            return
        monitor.emit(
            "backend_status",
            {
                "component": "local_extension",
                "stage": stage,
                "hook": hook,
                "exception_type": _exception_type_name(exc),
            },
        )
    except Exception:
        return


def _load_hook() -> ModuleType | None:
    global _CACHE_ATTEMPTED, _CACHE_KEY, _CACHE_MODULE
    root = _extension_root()
    enabled = _extensions_enabled()
    key = (str(root), str(_runtime_home()), enabled)
    if _CACHE_ATTEMPTED and _CACHE_KEY == key:
        return _CACHE_MODULE
    _CACHE_ATTEMPTED = True
    _CACHE_KEY = key
    _CACHE_MODULE = None
    if not enabled:
        return None
    path = _hook_path()
    if path is None:
        return None
    try:
        spec = importlib.util.spec_from_file_location("_mo_local_extension", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CACHE_MODULE = module
        return module
    except Exception as exc:
        _emit_extension_error("load", "local_extension", exc)
        return None


def _call(name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    module = _load_hook()
    if module is None:
        return default
    fn = getattr(module, name, None)
    if not callable(fn):
        return default
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        _emit_extension_error("call", name, exc)
        return default


def extensions_available() -> bool:
    """Return True only when a private profile extension is present and admitted."""
    if _load_hook() is None:
        return False
    return bool(_call("installed", default=True))


def match(user_input: str) -> dict[str, Any]:
    result = _call("match", user_input, default={})
    return result if isinstance(result, dict) else {}


def is_active(user_input: str) -> bool:
    return bool(match(user_input))


def command_specs() -> list[dict[str, Any]]:
    if not extensions_available():
        return []
    specs = _call("command_specs", default=[])
    return [dict(item) for item in specs] if isinstance(specs, list) else []


def desktop_app_specs() -> list[dict[str, Any]]:
    """Return bounded private Desktop app metadata from an admitted profile.

    Product code sees only an opaque id, a display label, and ordering. The
    implementation stays in the profile and is loaded only when the operator
    opens that exact app from the resident Desktop.
    """
    if not extensions_available():
        return []
    raw = _call("desktop_app_specs", default=[])
    if not isinstance(raw, (list, tuple)):
        return []
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        app_id = str(item.get("id") or "").strip().casefold()
        label = " ".join(str(item.get("label") or "").split())[:48]
        if not _DESKTOP_APP_ID_RE.fullmatch(app_id) or not label or app_id in seen:
            continue
        try:
            order = max(-1000, min(1000, int(item.get("order", 0) or 0)))
        except (TypeError, ValueError):
            order = 0
        seen.add(app_id)
        specs.append({"id": app_id, "label": label, "order": order})
    return sorted(specs, key=lambda item: (item["order"], item["label"].casefold(), item["id"]))


def create_desktop_app(app_id: str, host: dict[str, Any]) -> Any:
    """Create one admitted profile-owned Desktop app on explicit open.

    The host mapping contains Desktop presentation seams only. A private app
    receives no conversational agent, Gateway, model, or tool authority.
    """
    normalized = str(app_id or "").strip().casefold()
    if normalized not in {item["id"] for item in desktop_app_specs()}:
        return None
    if not isinstance(host, dict):
        return None
    presentation_host = {
        key: value for key, value in host.items() if key in _DESKTOP_APP_HOST_KEYS
    }
    return _call("create_desktop_app", normalized, presentation_host, default=None)


def dispatch_slash(agent: object, command: str, rest: str) -> str | None:
    if not extensions_available():
        return None
    result = _call("dispatch_slash", agent, command, rest, default=None)
    return result if isinstance(result, str) else None


def run_turn_override(gateway: object, route_source: str, user_input: str, callbacks: dict[str, Any]) -> str | None:
    if not extensions_available():
        return None
    result = _call("run_turn_override", gateway, route_source, user_input, callbacks, default=None)
    return result if isinstance(result, str) else None


def should_show_task_board(user_input: str) -> bool | None:
    result = _call("should_show_task_board", user_input, default=None)
    return result if isinstance(result, bool) else None


def should_skip_task_board(user_input: str) -> bool:
    return bool(_call("should_skip_task_board", user_input, default=False))


def board_rows(user_input: str) -> list[dict[str, Any]] | None:
    rows = _call("board_rows", user_input, default=None)
    if not isinstance(rows, list):
        return None
    return [dict(row) for row in rows if isinstance(row, dict)]


def open_board_block_text(agent: object, user_input: str, result_text: str, board: object) -> str | None:
    result = _call("open_board_block_text", agent, user_input, result_text, board, default=None)
    return result if isinstance(result, str) else None


def runtime_boundary_policy(user_input: str) -> dict[str, Any] | None:
    result = _call("runtime_boundary_policy", user_input, default=None)
    return result if isinstance(result, dict) else None


def _profile_extension_path_boundary(*, cwd: str | None = None) -> str:
    """Describe the distinct path authorities for an active profile extension."""
    extension_root = _extension_root().expanduser().resolve(strict=False)
    lines = [
        "Profile-owned local extension path boundary (runtime authority):",
        f"- Authoritative extension root: {extension_root}",
        "- Extension manifests, activation modules, and workflow instructions named "
        "by the active extension must be located and read under that root. Never "
        "resolve those names against the project cwd or probe the product checkout "
        "for them.",
        "- Use absolute paths under the extension root for extension-owned files. "
        "When the extension supplies only a filename or partial relative path, "
        "search inside that root first and use the unique matching path.",
    ]
    if str(cwd or "").strip():
        project_root = Path(str(cwd)).expanduser().resolve(strict=False)
        lines.append(
            f"- Project source remains rooted at {project_root}; use that root for "
            "product/project files, not profile-extension instructions."
        )
    return "\n".join(lines)


def context_blocks(agent: object, user_input: str, *, cwd: str | None = None) -> dict[str, str]:
    result = _call("context_blocks", agent, user_input, cwd=cwd, default={})
    blocks = (
        {str(k): str(v) for k, v in result.items() if str(v)}
        if isinstance(result, dict)
        else {}
    )
    if not is_active(user_input):
        return blocks
    # The product bridge owns this reserved block. Private hooks own workflow
    # content, but cannot redirect their instruction paths into the checkout.
    blocks.pop(_PATH_BOUNDARY_CONTEXT_KEY, None)
    return {
        _PATH_BOUNDARY_CONTEXT_KEY: _profile_extension_path_boundary(cwd=cwd),
        **blocks,
    }


def extra_allowed_roots(
    agent: object,
    user_input: str,
    tool_name: str,
    arguments: dict[str, Any] | None,
    *,
    read_like: bool = False,
) -> list[str]:
    result = _call(
        "extra_allowed_roots",
        agent,
        user_input,
        tool_name,
        arguments or {},
        read_like=read_like,
        default=[],
    )
    if not isinstance(result, list):
        return []
    return [str(item) for item in result if str(item).strip()]


def read_like_tool(tool_name: str, arguments: dict[str, Any] | None) -> bool | None:
    result = _call("read_like_tool", tool_name, arguments or {}, default=None)
    return result if isinstance(result, bool) else None


def tool_allowlist(agent: object, user_input: str) -> set[str] | None:
    """Return an optional extension-owned restriction for provider-visible tools."""
    result = _call("tool_allowlist", agent, user_input, default=None)
    if not isinstance(result, (list, tuple, set, frozenset)):
        return None
    return {str(item).strip() for item in result if str(item).strip()}


def normalize_tool_arguments(agent: object, user_input: str, tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = dict(arguments or {})
    result = _call("normalize_tool_arguments", agent, user_input, tool_name, args, default=None)
    return dict(result) if isinstance(result, dict) else args


def operator_override_approved(agent: object, user_input: str, tool_name: str, arguments: dict[str, Any] | None) -> bool:
    return bool(_call("operator_override_approved", agent, user_input, tool_name, arguments or {}, default=False))


def tool_block_reason(agent: object, user_input: str, tool_name: str, arguments: dict[str, Any] | None) -> str | None:
    result = _call("tool_block_reason", agent, user_input, tool_name, arguments or {}, default=None)
    return result if isinstance(result, str) and result.strip() else None


def on_tool_arguments(agent: object, tool_name: str, arguments: dict[str, Any] | None) -> None:
    _call("on_tool_arguments", agent, tool_name, arguments or {}, default=None)


def final_allows_task_close(agent: object, user_input: str, final_text: str) -> dict[str, Any] | None:
    result = _call("final_allows_task_close", agent, user_input, final_text, default=None)
    return result if isinstance(result, dict) else None


def after_task_board_close(agent: object, user_input: str, task_board: object, final_text: str) -> None:
    _call("after_task_board_close", agent, user_input, task_board, final_text, default=None)


def post_provider(agent: object, context: object) -> Any:
    return _call("post_provider", agent, context, default=None)


def final_gate(agent: object, context: object) -> Any:
    return _call("final_gate", agent, context, default=None)


def task_truth_continuation(agent: object, user_input: str, final_text: str, task_board: object) -> str | None:
    result = _call("task_truth_continuation", agent, user_input, final_text, task_board, default=None)
    return result if isinstance(result, str) and result.strip() else None


def completion_boundary(agent: object, user_input: str, final_text: str) -> str | None:
    result = _call("completion_boundary", agent, user_input, final_text, default=None)
    return result if isinstance(result, str) and result.strip() else None
