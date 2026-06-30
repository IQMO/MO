"""Internal system-prompt loader for MO runtime.

The default MO model-instruction layer is an internal source artifact, not a
project-root `system.md`. Legacy/default `system.md` config values resolve to the
internal prompt so a workspace file cannot accidentally become the active runtime
contract. Custom prompt-path overrides are developer-only and require the
MO_ALLOW_SYSTEM_PROMPT_OVERRIDE=1 environment gate.
"""
from __future__ import annotations

import os
from pathlib import Path

_LEGACY_DEFAULT_ALIASES = {"", "internal", ":internal:", "system.md"}
_FALLBACK_SYSTEM_PROMPT = "You are MO. Evidence-first. Provider-first. You have full local tools."
_OVERRIDE_ENV = "MO_ALLOW_SYSTEM_PROMPT_OVERRIDE"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def internal_system_prompt_path() -> Path:
    return Path(__file__).resolve().with_name("system.md")


def load_internal_system_prompt() -> str:
    path = internal_system_prompt_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
        return text or _FALLBACK_SYSTEM_PROMPT
    except Exception:
        return _FALLBACK_SYSTEM_PROMPT


def system_prompt_override_enabled() -> bool:
    return str(os.environ.get(_OVERRIDE_ENV, "")).strip().lower() in _TRUE_VALUES


def load_system_prompt(prompt_path: str | None) -> tuple[str, str]:
    """Return (prompt_text, source_label) for the runtime system prompt.

    Blank/`internal`/legacy `system.md` values use MO's internal prompt. Any
    other configured path is ignored unless MO_ALLOW_SYSTEM_PROMPT_OVERRIDE=1 is
    set. Missing enabled override paths fail closed to the internal prompt instead
    of silently using a project-root prompt or a tiny generic fallback.
    """
    raw = str(prompt_path or "").strip()
    if raw.lower() in _LEGACY_DEFAULT_ALIASES:
        return load_internal_system_prompt(), "internal"

    if not system_prompt_override_enabled():
        return load_internal_system_prompt(), f"internal (override disabled: {raw})"

    path = Path(raw).expanduser()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        return (text or load_internal_system_prompt()), str(path)

    return load_internal_system_prompt(), f"internal (missing override: {raw})"
