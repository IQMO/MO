"""MO health check — a one-shot, offline-safe diagnostic (`/doctor`).

Consolidates the env/config/provider/runtime checks that were otherwise split
across `/init`, `/status`, and `/usage` into a single report, with a
machine-readable `--json` mode for scripting. Unlike `/init`, this is
**read-only and offline**: it creates no files and makes no network calls, so it
is safe to run anywhere without billing a provider. It reuses the existing config
loader and provider config — it does not duplicate provider logic.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .path_defaults import mo_home

OK = "ok"
WARN = "warn"
FAIL = "fail"

# Representative modules: a broken import here is the telegram-/status bug class.
_CORE_MODULES = (
    "core.provider.provider",
    "core.sandbox",
    "core.gateway",
    "core.self_capability_preflight",
    "tools",
)


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str = ""


@dataclass
class DoctorReport:
    home: Path | None = None
    config_path: Path | None = None
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(name, status, detail))

    @property
    def worst(self) -> str:
        if any(c.status == FAIL for c in self.checks):
            return FAIL
        if any(c.status == WARN for c in self.checks):
            return WARN
        return OK

    @property
    def ok(self) -> bool:
        return self.worst != FAIL

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "worst": self.worst,
            "home": str(self.home) if self.home else None,
            "config_path": str(self.config_path) if self.config_path else None,
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in self.checks],
        }


def _load_config(config_path: str | Path | None) -> dict[str, Any]:
    try:
        from .provider.provider import load_config

        return load_config(str(config_path) if config_path else None) or {}
    except Exception:
        return {}


def build_doctor_report(
    *,
    home: str | Path | None = None,
    config_path: str | Path | None = None,
    project_path: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> DoctorReport:
    """Build an offline health report. No files created, no network calls.

    Pass an already-loaded *config* to avoid re-reading it; otherwise it is
    loaded from *config_path* (or the default location).
    """
    if config is None:
        config = _load_config(config_path)
    home_path = Path(home).expanduser() if home else mo_home(config)
    cfg = Path(config_path).expanduser() if config_path else home_path / "config.yaml"
    report = DoctorReport(home=home_path, config_path=cfg)

    # 1. Python version
    v = sys.version_info
    if v >= (3, 10):
        report.add("python", OK, f"{v.major}.{v.minor}.{v.micro}")
    else:
        report.add("python", FAIL, f"{v.major}.{v.minor} is below the required 3.10")

    # 2. Private MO home
    if home_path.is_dir():
        report.add("mo_home", OK, str(home_path))
    else:
        report.add("mo_home", WARN, f"{home_path} not found — run /init")

    # 3. Config file
    if cfg.is_file():
        report.add("config", OK if config else WARN,
                   str(cfg) if config else f"{cfg} present but did not parse")
    else:
        report.add("config", WARN, f"{cfg} not found — defaults in use; run /init")

    # 4. Providers configured + key env present (never prints key values)
    providers = config.get("providers") or []
    if not providers:
        report.add("providers", WARN, "none configured")
    else:
        present: list[str] = []
        missing: list[str] = []
        for p in providers:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name") or "?")
            key_env = p.get("api_key_env")
            auth_path = p.get("auth_path")
            if key_env:
                (present if os.environ.get(str(key_env)) else missing).append(f"{name}:{key_env}")
            elif auth_path and Path(str(auth_path)).expanduser().is_file():
                present.append(f"{name}(auth)")
            elif p.get("api_key"):
                present.append(f"{name}(inline)")
            else:
                present.append(name)
        status = OK if (present and not missing) else (WARN if present else FAIL)
        detail = ""
        if present:
            detail = "present " + ", ".join(present[:6])
        if missing:
            detail += ("; " if detail else "") + "missing " + ", ".join(missing[:6])
        report.add("providers", status, detail)

    # 5. Default model selected
    default_model = (config.get("model") or {}).get("default")
    report.add("default_model", OK if default_model else WARN,
               str(default_model) if default_model else "model.default not set")

    # 6. Core module imports (a broken import is the telegram-/status bug class)
    broken: list[str] = []
    for mod in _CORE_MODULES:
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001 - report, do not raise
            broken.append(f"{mod}: {type(exc).__name__}")
    report.add("core_imports", OK if not broken else FAIL,
               "all core modules import" if not broken else "; ".join(broken))

    return report


def render_doctor_report(report: DoctorReport) -> str:
    glyph = {OK: "ok  ", WARN: "WARN", FAIL: "FAIL"}
    lines = [
        f"MO doctor: {report.worst.upper()}",
        f"  home:   {report.home}",
        f"  config: {report.config_path}",
        "",
    ]
    for c in report.checks:
        suffix = f"  {c.detail}" if c.detail else ""
        lines.append(f"  [{glyph.get(c.status, c.status)}] {c.name}{suffix}")
    if report.worst != OK:
        lines.append("")
        lines.append("Tips: run /init to scaffold ~/.mo and config; put provider keys in ~/.mo/.env.")
    return "\n".join(lines)


def render_doctor_json(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), indent=2)
