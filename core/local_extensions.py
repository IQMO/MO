"""Neutral bridge for profile-owned local extensions.

The product checkout owns this loader only. Extension commands, activation
phrases, board rows, runtime loops, and closeout behavior live in the user's
private MO profile and are absent in a fresh profile.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import traceback
from types import ModuleType
from typing import Any

from .state.paths import local_extension_root, mo_home

_HOOK_FILENAMES = ("local_extension.py",)
_CACHE_KEY: tuple[str, str, bool] | None = None
_CACHE_MODULE: ModuleType | None = None
_CACHE_ATTEMPTED = False


def _token_present() -> bool:
    try:
        token = mo_home() / "operator.token"
        return token.is_file() and bool(token.read_text(encoding="utf-8").strip())
    except Exception:
        return False


def _hook_path() -> Path | None:
    root = local_extension_root()
    for rel in _HOOK_FILENAMES:
        path = root / rel
        try:
            if path.is_file():
                return path
        except Exception:
            continue
    return None


def _load_hook() -> ModuleType | None:
    global _CACHE_ATTEMPTED, _CACHE_KEY, _CACHE_MODULE
    root = local_extension_root()
    token = _token_present()
    key = (str(root), str(mo_home()), token)
    if _CACHE_ATTEMPTED and _CACHE_KEY == key:
        return _CACHE_MODULE
    _CACHE_ATTEMPTED = True
    _CACHE_KEY = key
    _CACHE_MODULE = None
    if not token:
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
    except Exception:
        traceback.print_exc()
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
    except Exception:
        traceback.print_exc()
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


def should_skip_ghost_proposal(user_input: str) -> bool:
    return bool(_call("should_skip_ghost_proposal", user_input, default=False))


def ghost_skip_event(user_input: str) -> dict[str, Any]:
    result = _call("ghost_skip_event", user_input, default={})
    return result if isinstance(result, dict) else {}


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


def context_blocks(agent: object, user_input: str, *, cwd: str | None = None) -> dict[str, str]:
    result = _call("context_blocks", agent, user_input, cwd=cwd, default={})
    if not isinstance(result, dict):
        return {}
    return {str(k): str(v) for k, v in result.items() if str(v)}


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


def normalize_tool_arguments(agent: object, user_input: str, tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = dict(arguments or {})
    result = _call("normalize_tool_arguments", agent, user_input, tool_name, args, default=None)
    return dict(result) if isinstance(result, dict) else args


def self_change_approved(user_input: str) -> bool:
    return bool(_call("self_change_approved", user_input, default=False))


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
