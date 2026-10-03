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
import importlib.util
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..state.paths import mo_home
from ..state.secrets import secret_status

OK = "ok"
WARN = "warn"
FAIL = "fail"

# Representative modules: a broken import here is the telegram-/status bug class.
_CORE_MODULES = (
    "core.provider.provider",
    "core.tooling.sandbox",
    "core.gateway",
    "core.local_extensions",
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
        from ..provider.provider import load_config

        return load_config(str(config_path) if config_path else None) or {}
    except Exception:
        return {}


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _missing_modules(modules: list[tuple[str, str]]) -> list[str]:
    return [label for mod, label in modules if not _has_module(mod)]


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
                status = secret_status(str(key_env), config=config, service="providers")
                (present if status.present else missing).append(f"{name}:{key_env}")
            elif auth_path and Path(str(auth_path)).expanduser().is_file():
                present.append(f"{name}(auth)")
            else:
                kind = str(p.get("type") or p.get("api_mode") or "").lower()
                try:
                    host = (
                        urllib.parse.urlsplit(str(p.get("base_url") or "")).hostname
                        or ""
                    ).lower()
                except ValueError:
                    host = ""
                if kind == "mock" or host in {"127.0.0.1", "localhost", "::1"}:
                    present.append(f"{name}(local)")
                else:
                    missing.append(f"{name}:api_key_env")
        status = OK if (present and not missing) else (WARN if present else FAIL)
        detail = ""
        if present:
            detail = "credentials present " + ", ".join(present[:6])
        if missing:
            detail += ("; " if detail else "") + "missing " + ", ".join(missing[:6])
        if present and not missing:
            detail += "; live access/balance not checked"
        report.add("providers", status, detail)

    # 5. Default model selected
    default_model = (config.get("model") or {}).get("default")
    report.add("default_model", OK if default_model else WARN,
               str(default_model) if default_model else "model.default not set")

    # Canonical credentials are device-local and never part of state replication.
    credential_dir = home_path / "credentials"
    report.add(
        "credential_layout",
        OK if credential_dir.is_dir() else WARN,
        "canonical device-local directory present"
        if credential_dir.is_dir() else "not initialized; run /init",
    )
    extension_cfg = config.get("local_extensions") if isinstance(config.get("local_extensions"), dict) else {}
    report.add(
        "local_extensions",
        OK,
        "enabled by config" if extension_cfg.get("enabled") is True else "disabled",
    )

    # 6. Core module imports (a broken import is the telegram-/status bug class)
    broken: list[str] = []
    for mod in _CORE_MODULES:
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001 - report, do not raise
            broken.append(f"{mod}: {type(exc).__name__}")
    report.add("core_imports", OK if not broken else FAIL,
               "all core modules import" if not broken else "; ".join(broken))

    # 7. External MCP servers (config only — doctor is offline and does NOT spawn them)
    mcp_cfg = config.get("mcp") or {}
    if not mcp_cfg.get("enabled"):
        report.add("mcp", OK, "disabled")
    else:
        configured = mcp_cfg.get("servers") or []
        entries = configured if isinstance(configured, list) else [configured]
        active = [s for s in entries if not isinstance(s, dict) or s.get("enabled") is not False]
        servers = [s for s in active if isinstance(s, dict) and s.get("name") and s.get("command")]
        invalid = len(active) - len(servers)
        if invalid:
            report.add("mcp", WARN, f"{invalid} invalid active server entr{'y' if invalid == 1 else 'ies'}")
        elif servers:
            report.add("mcp", OK, f"enabled, {len(servers)} external server(s) configured")
        else:
            report.add("mcp", OK, "enabled; no active external servers configured (inert)")

    # 8. Computer-use optional packages (offline import probes only).
    computer_use_modules = [
        ("PIL", "Pillow"),
        ("websocket", "websocket-client"),
        ("pyautogui", "pyautogui"),
    ]
    missing = _missing_modules(computer_use_modules)
    report.add(
        "computer_use",
        OK if not missing else WARN,
        "screen/browser/desktop base extras installed" if not missing
        else "missing optional " + ", ".join(missing),
    )

    # 9. Windows semantic desktop eyes.
    if sys.platform == "win32":
        report.add(
            "desktop_uia",
            OK if _has_module("uiautomation") else WARN,
            "uiautomation installed" if _has_module("uiautomation")
            else "optional uiautomation missing; semantic desktop refs disabled",
        )
    else:
        report.add("desktop_uia", OK, "not a Windows host")

    # 10. MO Desktop surface and voice readiness. These checks do not start
    # windows, hotkeys, tray icons, audio devices, or models.
    from mo_desktop.desktop_launch import mo_desktop_config_block
    desktop_cfg = mo_desktop_config_block(config)  # reads the mo_desktop block
    desktop_enabled = bool(desktop_cfg.get("enabled", False))
    if not desktop_enabled:
        report.add("mo_desktop", OK, "disabled")
    else:
        modules = [("keyboard", "keyboard"), ("pystray", "pystray"), ("PIL", "Pillow")]
        if sys.platform == "win32":
            modules.extend([("win32com", "pywin32"), ("pythoncom", "pythoncom")])
        missing = _missing_modules(modules)
        report.add(
            "mo_desktop",
            OK if not missing else WARN,
            "enabled; desktop surface extras installed" if not missing
            else "enabled; missing optional " + ", ".join(missing),
        )

    voice_cfg = desktop_cfg.get("voice") if isinstance(desktop_cfg.get("voice"), dict) else {}
    stt_enabled = bool(voice_cfg.get("stt_enabled", False))
    tts_enabled = bool(voice_cfg.get("tts_enabled", False))
    if not stt_enabled and not tts_enabled:
        report.add("desktop_voice", OK, "disabled")
    else:
        try:
            from mo_desktop.voice.storage import activate_voice_dependencies

            activate_voice_dependencies(config)
        except Exception:
            pass
        modules: list[tuple[str, str]] = []
        if stt_enabled:
            from mo_desktop.voice.input import configured_stt_engine

            engine = configured_stt_engine(voice_cfg)
            modules.extend([("sounddevice", "sounddevice"), ("numpy", "numpy")])
            if engine == "windows":
                if sys.platform == "win32":
                    modules.extend([("win32com", "pywin32"), ("pythoncom", "pythoncom")])
                else:
                    modules.append(("__windows__", "Windows host required for windows STT"))
            else:
                modules.append(("faster_whisper", "faster-whisper"))
        if tts_enabled:
            modules.append(("sounddevice", "sounddevice"))
        missing = sorted(set(_missing_modules(modules)))
        detail_parts: list[str] = []
        if stt_enabled:
            detail_parts.append(f"STT enabled ({engine})")
        if tts_enabled:
            try:
                from mo_desktop.voice.storage import (
                    resolve_voice_install_root,
                    voice_runtime_ready,
                )

                root = resolve_voice_install_root(config)
                if not voice_runtime_ready(root):
                    missing.append("Piper Joe speech worker")
            except Exception:
                missing.append("valid voice install root")
            detail_parts.append("TTS enabled (Piper Joe English)")
        report.add(
            "desktop_voice",
            OK if not missing else WARN,
            "; ".join(detail_parts) + ("; missing optional " + ", ".join(sorted(set(missing))) if missing else "; ready"),
        )

    # 11. State layout — declared ~/.mo layout vs on-disk (report-only; no mutation).
    try:
        from ..state.layout import check_state_layout
        layout = check_state_layout(home=home_path, config=config)
        lc = layout["counts"]
        drift = any(f["category"] == "deprecated_present" and "drift" in f["detail"] for f in layout["findings"])
        report.add(
            "state_layout",
            WARN if (lc["missing"] or drift) else OK,
            f"ok:{lc['ok']} missing:{lc['missing']} legacy:{lc['deprecated_present']} "
            f"undeclared:{lc['undeclared']} empty:{lc['empty_declared']}",
        )
    except Exception as exc:  # noqa: BLE001 — a diagnostic must never break the report
        report.add("state_layout", WARN, f"layout check errored: {type(exc).__name__}")

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
    return "\n".join(lines)


def render_doctor_json(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), indent=2)
