"""MO first-run initialization helpers.

Initialization is deterministic and private by default: it creates MO-owned
runtime state under `~/.mo` (or a configured home) and never writes into the
caller project. Project instruction files are read only when they already exist.
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
import traceback

from .path_defaults import mo_home, repo_root
from .profile import Profile
from .project_context import discover_project_context_files
from .secrets import secret_status


@dataclass
class InitReport:
    home: Path
    config_path: Path
    project_path: Path
    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provider_status: list[tuple[str, str, bool, str]] = field(default_factory=list)
    project_context_files: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any("error" in item.lower() for item in self.warnings)


def initialize_mo(
    *,
    home: str | Path | None = None,
    project_path: str | Path | None = None,
    config_path: str | Path | None = None,
    create_config: bool = True,
) -> InitReport:
    """Create private MO runtime scaffolding without overwriting user data."""
    home_path = Path(home).expanduser().resolve(strict=False) if home else mo_home()
    project = Path(project_path or os.getcwd()).expanduser().resolve(strict=False)
    cfg_path = Path(config_path).expanduser().resolve(strict=False) if config_path else home_path / "config.yaml"
    report = InitReport(home=home_path, config_path=cfg_path, project_path=project)

    _ensure_dir(home_path, report, "home")
    for rel in (
        # Core structure
        "bin",
        "memory",
        "memory/profile",
        "memory/sessions",
        "memory/taskboards",
        "memory/heartbeat",
        "memory/scheduler",
        # Goal system
        "memory/goal-runs",
        # Session management
        "memory/session_closeouts",
        # Review/PRT system
        "memory/review_history",
        # Graph systems
        "memory/structural_graph",
        "memory/code_graph",
        # Logs
        "logs",
        "logs/monitor",
        # Cache
        "cache",
    ):
        _ensure_dir(home_path / rel, report, rel)

    _ensure_env_file(home_path / ".env", report)
    _ensure_command_shims(home_path / "bin", report)
    if create_config:
        _ensure_config(cfg_path, report)

    profile = Profile.load(str(home_path / "memory" / "mo.db"))
    profile.ensure_operator_profile()
    report.existing.append("memory/mo.db" if (home_path / "memory" / "mo.db").exists() else "profile database")
    for name in ("operator.md", "thinking_model.md", "behavior.md", "learning.md", "terms.md", "identity.md"):
        path = home_path / "memory" / "profile" / name
        if path.exists():
            report.existing.append(f"memory/profile/{name}")

    report.project_context_files = list(discover_project_context_files(project))
    if not report.project_context_files:
        report.warnings.append("No AGENTS.md or CLAUDE.md found for this project. MO will still work; create one only if you want project-specific instructions.")

    _provider_status(cfg_path, report)
    return report


def render_init_report(report: InitReport) -> str:
    """Render concise setup status with no secret values."""
    lines = [
        "MO init status:",
        f"  private home: {report.home}",
        f"  config:       {report.config_path}",
        f"  project:      {report.project_path}",
    ]
    if report.created:
        lines.append("  created:      " + ", ".join(_shorten(item) for item in report.created[:12]))
        if len(report.created) > 12:
            lines.append(f"                +{len(report.created) - 12} more")
    if report.existing:
        lines.append("  existing:     " + ", ".join(_shorten(item) for item in report.existing[:10]))
        if len(report.existing) > 10:
            lines.append(f"                +{len(report.existing) - 10} more")
    if report.project_context_files:
        lines.append("  project ctx:  " + ", ".join(str(p) for p in report.project_context_files[:4]))
    else:
        lines.append("  project ctx:  none found")
    if report.provider_status:
        missing = [f"{name}:{key}" for name, key, present, _source in report.provider_status if not present]
        present = [f"{name}:{key}" for name, key, present, _source in report.provider_status if present]
        if present:
            lines.append("  provider env: present " + ", ".join(present[:6]))
        if missing:
            lines.append("  provider env: missing " + ", ".join(missing[:8]))
    if report.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in report.warnings)
    lines.extend([
        "Next:",
        "1. Put provider keys in ~/.mo/.env or your shell environment; never paste key values into chat.",
        "2. Edit ~/.mo/config.yaml only if you need a different provider/model or runtime path.",
        "3. Add ~/.mo/bin to PATH if you want to call `mo` from any terminal.",
        "4. Run `mo` from a project folder. MO preserves that cwd, reads AGENTS.md/CLAUDE.md if present, and keeps private state under ~/.mo.",
    ])
    return "\n".join(lines)


def _ensure_dir(path: Path, report: InitReport, label: str) -> None:
    if path.exists():
        report.existing.append(label)
        return
    path.mkdir(parents=True, exist_ok=True)
    report.created.append(label)


def _ensure_env_file(path: Path, report: InitReport) -> None:
    if path.exists():
        report.existing.append(".env")
        return
    path.write_text(
        "# MO private environment file. Keep secret values here or in your shell env.\n"
        "# OPENCODE_API_KEY=\n"
        "# TELEGRAM_BOT_TOKEN=\n",
        encoding="utf-8",
    )
    _chmod_private(path)
    report.created.append(".env")


def _ensure_config(path: Path, report: InitReport) -> None:
    if path.exists():
        report.existing.append("config.yaml")
        return
    source = Path(repo_root()) / "config.example.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copyfile(source, path)
    else:
        path.write_text(_fallback_config(), encoding="utf-8")
    _pin_runtime_home(path, report.home)
    _chmod_private(path)
    report.created.append("config.yaml")


def _pin_runtime_home(path: Path, home: Path) -> None:
    """Make generated configs self-contained for explicit MO_HOME/init runs."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    home_text = str(home).replace("\\", "/")
    replaced = text.replace("  home: ~/.mo", f'  home: "{home_text}"')
    if replaced != text:
        path.write_text(replaced, encoding="utf-8")


def _ensure_command_shims(bin_dir: Path, report: InitReport) -> None:
    root = Path(repo_root()).resolve(strict=False)
    python = Path(sys.executable).resolve(strict=False)
    mo_py = root / "mo.py"
    posix = bin_dir / "mo"
    cmd = bin_dir / "mo.cmd"

    if posix.exists():
        report.existing.append("bin/mo")
    else:
        posix.write_text(_posix_shim(python, mo_py), encoding="utf-8")
        try:
            posix.chmod(0o755)
        except Exception:
            traceback.print_exc()
        report.created.append("bin/mo")

    if cmd.exists():
        report.existing.append("bin/mo.cmd")
    else:
        cmd.write_text(_cmd_shim(python, mo_py), encoding="utf-8")
        _chmod_private(cmd)
        report.created.append("bin/mo.cmd")


def _posix_shim(python: Path, mo_py: Path) -> str:
    return (
        "#!/usr/bin/env sh\n"
        "export MO_PROJECT_CWD=\"${MO_PROJECT_CWD:-$PWD}\"\n"
        "export MO_INVOKED_AS=\"${MO_INVOKED_AS:-mo}\"\n"
        f"exec {shlex.quote(str(python))} {shlex.quote(str(mo_py))} \"$@\"\n"
    )


def _cmd_shim(python: Path, mo_py: Path) -> str:
    return (
        "@echo off\r\n"
        "set \"MO_PROJECT_CWD=%CD%\"\r\n"
        "if not defined MO_INVOKED_AS set \"MO_INVOKED_AS=mo\"\r\n"
        f'"{python}" "{mo_py}" %*\r\n'
    )


def _provider_status(path: Path, report: InitReport) -> None:
    if not path.exists():
        return
    try:
        import yaml
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        report.warnings.append(f"Could not inspect provider env status: {type(exc).__name__}")
        return
    seen: set[tuple[str, str]] = set()
    secret_files = [str(report.home / ".env")]
    for provider in list(cfg.get("providers") or []):
        if not isinstance(provider, dict):
            continue
        key = str(provider.get("api_key_env") or "").strip()
        name = str(provider.get("name") or "provider").strip()
        if not key or (name, key) in seen:
            continue
        seen.add((name, key))
        status = secret_status(key, files=secret_files)
        report.provider_status.append((name, key, bool(status.present), status.source))


def _fallback_config() -> str:
    return """runtime:\n  home: ~/.mo\n  state: private\nproviders:\n  - name: mock-local\n    type: mock\n    model: mock-model\nmodel:\n  default: mock-model\naccess:\n  mode: project\npaths:\n  memory_file: memory/mo.db\n  critique_file: critique/ANSWER.md\nsandbox:\n  enabled: true\n  audit_log: logs/tool_audit.jsonl\n"""


def _chmod_private(path: Path) -> None:
    try:
        if os.name != "nt":
            path.chmod(0o600)
    except Exception:
        traceback.print_exc()


def _shorten(value: str, limit: int = 48) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
