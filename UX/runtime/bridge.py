"""Lazy MO runtime bridge for the isolated UX surface."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from UX.runtime.adapters import snapshot_from_runtime
from UX.state.controller import UxCallbacks
from UX.state.models import SessionSnapshot


class RuntimeUnavailable(RuntimeError):
    """Raised when the MO runtime cannot be created for UX live/read-only mode."""


@dataclass
class RuntimeHandle:
    agent: Any
    gateway: Any

    def snapshot(self) -> SessionSnapshot:
        return snapshot_from_runtime(self.agent, self.gateway)

    def run_turn(self, text: str, *, callbacks: UxCallbacks | None = None) -> str:
        kwargs: dict[str, Any] = {}
        if callbacks is not None:
            kwargs.update(
                on_activity=callbacks.on_activity,
                on_token=callbacks.on_token,
                on_assistant_text=callbacks.on_assistant_text,
                on_board_event=lambda _event: callbacks.changed(),
                on_board_update=lambda _rich: callbacks.changed(),
            )
        return str(self.gateway.run_turn(text, **kwargs) or "")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def create_runtime() -> RuntimeHandle:
    """Create MO's Agent/Gateway pair without starting the production interface."""
    root = repo_root()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    os.environ.setdefault("MO_PROJECT_CWD", os.getcwd())
    os.environ.setdefault("MO_INVOKED_AS", "mo-ux")

    try:
        from core.agent.agent import create_agent
        from core.gateway import Gateway
        from core.path_defaults import default_config_path
        from core.provider.provider import ConfigLoadError, ProviderError, clean_provider_error
        from core.text_safety import configure_utf8_stdio
    except Exception as exc:
        raise RuntimeUnavailable(f"MO runtime imports failed: {type(exc).__name__}: {exc}") from exc

    configure_utf8_stdio()
    config_path = default_config_path(agent_root=str(root), caller_cwd=os.environ.get("MO_PROJECT_CWD") or os.getcwd())
    if not os.path.exists(config_path):
        raise RuntimeUnavailable(f"MO config not found: {config_path}. Run `python mo.py --init` first.")
    try:
        agent = create_agent(config_path)
    except ConfigLoadError as exc:
        raise RuntimeUnavailable(f"MO config error: {exc.message} ({exc.path})") from exc
    except ProviderError as exc:
        raise RuntimeUnavailable(f"MO provider error: {clean_provider_error(str(exc))}") from exc
    return RuntimeHandle(agent=agent, gateway=Gateway(agent))
