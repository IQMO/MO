"""Light internal terminal — run a real command on MO's own TTY, then return.

No PTY, no emulator, no new dependencies. The caller uses prompt_toolkit's
``run_in_terminal`` to suspend MO's render and hand the real terminal to the
child, so codex/claude/shells run at full fidelity on the actual console. One
foreground terminal at a time by design — the point is to stay light and never
leave background processes running.
"""
from __future__ import annotations

import os
import shutil
import subprocess


def _default_shell() -> list[str]:
    """The interactive shell to open when no command is given."""
    if os.name == "nt":
        for candidate in ("pwsh", "powershell"):
            if shutil.which(candidate):
                return [candidate]
        return [os.environ.get("COMSPEC", "cmd.exe")]
    return [os.environ.get("SHELL") or "/bin/sh"]


def resolve_command(command: str) -> tuple[object, str]:
    """Return ``(spec, label)`` for a terminal request.

    A non-empty command runs as typed (shell-resolved, so ``codex`` / ``claude`` /
    ``git log`` find PATH); empty opens the default interactive shell.
    """
    cmd = str(command or "").strip()
    if cmd:
        return cmd, cmd.split()[0]
    return _default_shell(), "shell"


def run_terminal_command(spec: object) -> int:
    """Run ``spec`` attached to the current real terminal; return its exit code.

    Must be called with MO's renderer suspended (``run_in_terminal``) so the child
    owns the console. Never raises — a missing program returns 127.
    """
    try:
        if isinstance(spec, str):
            completed = subprocess.run(spec, shell=True)
        else:
            completed = subprocess.run(list(spec))
        return int(completed.returncode or 0)
    except FileNotFoundError:
        return 127
    except KeyboardInterrupt:
        return 130
    except Exception:
        return 1
