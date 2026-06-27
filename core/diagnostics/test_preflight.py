"""Fast preflight for broad maintainer pytest runs.

The full suite is still the authority. This module only fails cheap checks first:
public/private boundary guards and a bounded collect-only pass.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str


def repo_root(cwd: str | Path | None = None) -> Path:
    path = Path(cwd or os.getcwd()).resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            text=True,
            capture_output=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except Exception:
        pass
    return path


def _privacy_guard_path() -> Path:
    configured = os.environ.get("MO_PRIVACY_GUARD", "").strip()
    if configured:
        return Path(configured).expanduser()
    pack = os.environ.get("MO_OPERATOR_PACK", "").strip()
    if pack:
        return Path(pack).expanduser() / "privacy_guard.py"
    return Path.home() / ".mo" / "operator" / "privacy_guard.py"


def _is_mo_checkout(root: Path) -> bool:
    return (
        (root / "mo.py").is_file()
        and (root / "AGENTS.md").is_file()
        and (root / "core" / "local_extensions.py").is_file()
    )


def _tail(text: str, limit: int = 4000) -> str:
    clean = str(text or "").strip()
    return clean[-limit:] if len(clean) > limit else clean


def run_public_private_guards(root: str | Path | None = None) -> CheckResult:
    root_path = repo_root(root)
    if not _is_mo_checkout(root_path):
        return CheckResult(True, "[preflight] MO boundary guards skipped: not an MO checkout")

    try:
        tracked = subprocess.run(
            ["git", "ls-files", "tests"],
            cwd=str(root_path),
            text=True,
            capture_output=True,
            timeout=20,
        )
    except Exception as exc:
        return CheckResult(False, f"[preflight] git ls-files tests failed: {type(exc).__name__}: {exc}")
    if tracked.returncode != 0:
        return CheckResult(False, "[preflight] git ls-files tests failed:\n" + _tail(tracked.stderr or tracked.stdout))
    if tracked.stdout.strip():
        examples = "\n".join(tracked.stdout.splitlines()[:20])
        return CheckResult(False, "[preflight] tests/ is tracked and would ship:\n" + examples)

    guard = _privacy_guard_path()
    if not guard.is_file():
        if (root_path / "tests").exists():
            return CheckResult(False, f"[preflight] missing privacy guard with local tests overlay present: {guard}")
        return CheckResult(True, "[preflight] privacy guard skipped: no local tests overlay")

    try:
        proc = subprocess.run(
            [sys.executable, str(guard)],
            cwd=str(root_path),
            text=True,
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(False, "[preflight] privacy guard timed out before collection")
    except Exception as exc:
        return CheckResult(False, f"[preflight] privacy guard failed to start: {type(exc).__name__}: {exc}")
    if proc.returncode != 0:
        return CheckResult(False, "[preflight] privacy guard failed:\n" + _tail(proc.stderr or proc.stdout))
    return CheckResult(True, "[preflight] public/private guards: ok")


def _count_collected_items(stdout: str) -> int:
    count = 0
    for line in str(stdout or "").splitlines():
        stripped = line.strip()
        if "::" in stripped and not stripped.startswith(("<", "=", "[")):
            count += 1
    return count


def run_collect_only(root: str | Path | None = None, *, timeout: int = 180) -> CheckResult:
    root_path = repo_root(root)
    if not (root_path / "tests").exists():
        return CheckResult(True, "[preflight] collect-only skipped: no local tests overlay")

    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root_path),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout or 180)),
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
        return CheckResult(False, "[preflight] collect-only timed out:\n" + _tail(output))
    elapsed = time.perf_counter() - start
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        return CheckResult(False, "[preflight] collect-only failed:\n" + _tail(output))
    count = _count_collected_items(proc.stdout or "")
    return CheckResult(True, f"[preflight] collect-only: ok ({count} item(s), {elapsed:.1f}s)")


def run_preflight(root: str | Path | None = None, *, collect: bool = True, timeout: int = 180) -> CheckResult:
    guards = run_public_private_guards(root)
    if not guards.ok:
        return guards
    if not collect:
        return guards
    collected = run_collect_only(root, timeout=timeout)
    if not collected.ok:
        return CheckResult(False, guards.message + "\n" + collected.message)
    return CheckResult(True, guards.message + "\n" + collected.message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run fast MO test preflight checks before broad pytest suites.")
    parser.add_argument("--root", default="", help="Project root; defaults to git top-level or cwd.")
    parser.add_argument("--guards-only", action="store_true", help="Run boundary guards only; skip collect-only.")
    parser.add_argument("--collect", action="store_true", help="Run boundary guards and collect-only.")
    parser.add_argument("--timeout", type=int, default=180, help="Collect-only timeout in seconds.")
    args = parser.parse_args(argv)

    result = run_preflight(args.root or None, collect=args.collect or not args.guards_only, timeout=args.timeout)
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
