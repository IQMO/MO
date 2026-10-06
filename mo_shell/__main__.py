"""Launch the built MO Shell native host with the current Python runtime."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from core.runtime.subprocess_flags import console_python_executable
from core.state.paths import ENV_MO_CONFIG, project_cwd as resolve_project_cwd


def native_executable() -> Path | None:
    native = Path(__file__).resolve().parent / "native" / "bin"
    for configuration in ("Release", "Debug"):
        candidate = native / configuration / "net8.0-windows" / "MoShell.Native.exe"
        if candidate.is_file():
            return candidate
    return None


SHELL_STARTUP_ENV = "MO_SHELL_STARTUP_FILE"
SHELL_STARTUP_FLAGS = frozenset({"--startup-goal-file", "--startup-turn-file", "--startup-panes"})


def write_shell_startup(args: tuple[str, ...]) -> Path:
    """One private, one-shot file of flag/value pairs for the next Shell's first terminal."""
    import secrets

    from core.state.paths import MO_DESIGN_HANDOFF_DIR, resolve_state_path
    from core.utils.atomic_write import atomic_write_text

    pairs = list(args)
    if len(pairs) % 2 or any(flag not in SHELL_STARTUP_FLAGS for flag in pairs[::2]):
        raise ValueError("MO Shell startup accepts only known flag/value pairs")
    directory = Path(resolve_state_path(MO_DESIGN_HANDOFF_DIR))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"shell-startup-{secrets.token_hex(8)}.json"
    atomic_write_text(path, json.dumps(pairs), encoding="utf-8")
    return path


def launch_native(
    *,
    config_path: str = "",
    project_cwd: str = "",
    on_source: Any = None,
    on_started: Any = None,
    on_ready: Any = None,
    startup_args: tuple[str, ...] = (),
) -> subprocess.Popen[Any]:
    """Launch one native Shell instance with the canonical Python/project context.

    ``startup_args`` (from ``SHELL_STARTUP_FLAGS`` only) start this Shell's first terminal,
    e.g. with a handed-over goal or a pane count; later Shells open normally."""
    executable = native_executable()
    if executable is None:
        raise FileNotFoundError(
            "MO Shell is not built. Run: "
            "dotnet build mo_shell/native/MoShell.Native.csproj -c Release"
        )
    assembly = executable.with_suffix(".dll")
    sources = Path(__file__).resolve().parent / "native"
    newest_source = max((path.stat().st_mtime_ns for pattern in ("*.cs", "*.csproj")
                         for path in sources.glob(pattern)), default=0)
    if not assembly.is_file() or assembly.stat().st_mtime_ns < newest_source:
        raise FileNotFoundError(
            "MO Shell native build is older than its source. Run: "
            "dotnet build mo_shell/native/MoShell.Native.csproj -c Release"
        )
    project = (
        Path(project_cwd).expanduser().resolve(strict=False)
        if project_cwd
        else resolve_project_cwd()
    )
    environment = os.environ.copy()
    environment["MO_PYTHON"] = console_python_executable()
    environment["MO_PROJECT_CWD"] = str(project)
    environment.pop("MO_SHELL_LAUNCH_ORIGIN", None)
    environment.pop(SHELL_STARTUP_ENV, None)
    if startup_args:
        environment[SHELL_STARTUP_ENV] = str(write_shell_startup(startup_args))
    if on_source:
        environment["MO_SHELL_LAUNCH_ORIGIN"] = json.dumps({"deferred": True})
    if config_path:
        environment[ENV_MO_CONFIG] = str(
            Path(config_path).expanduser().resolve(strict=False)
        )
    options = {"stdout": subprocess.PIPE, "text": True} if on_source or on_ready else {}
    if on_source:
        options["stdin"] = subprocess.PIPE
    process = subprocess.Popen([str(executable)], env=environment, cwd=str(project), **options)
    if on_source or on_ready:
        from mo_desktop.mo_renderer import _receive_launch_ready
        _receive_launch_ready(process, on_ready, on_source=on_source, on_started=on_started)
    return process


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch MO Shell")
    parser.add_argument("--print-native-path", action="store_true")
    parser.add_argument("--config", default="", help="use an explicit MO config path")
    args = parser.parse_args(argv)
    if args.print_native_path:
        executable = native_executable()
        print(str(executable or ""))
        return 0 if executable is not None else 2
    try:
        process = launch_native(config_path=args.config)
    except FileNotFoundError as error:
        print(str(error), file=sys.stderr)
        return 2
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["launch_native", "main", "native_executable"]
