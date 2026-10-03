"""Run the delivery-only maintainer suite through parallel and serial gates."""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..runtime.subprocess_flags import apply_windows_hidden_process_flags
from ..tooling.shell_processes import kill_process_tree
from ..utils.text_safety import configure_utf8_stdio
from .test_preflight import _pytest_subprocess_env, repo_root, run_preflight


DEFAULT_LANE_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class SuiteResult:
    exit_code: int
    message: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _serial_test_targets(root: Path) -> tuple[str, ...]:
    """Find directly marked serial test files without collecting the whole suite."""
    tests_root = root / "tests"
    if not tests_root.is_dir():
        return ()
    targets = []
    for path in sorted(tests_root.rglob("test_*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError):
            continue
        except SyntaxError:
            # Let pytest report invalid test syntax in the authoritative lane.
            continue
        if any(_is_serial_marker_expression(node) for node in ast.walk(tree)):
            targets.append(path.relative_to(root).as_posix())
    return tuple(targets)


def _is_serial_marker_expression(node: ast.AST) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    parts = []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return ".".join(reversed(parts)) == "pytest.mark.serial"


def suite_commands(
    workers: int = 2,
    *,
    serial_targets: tuple[str, ...] | None = None,
) -> tuple[list[str], list[str]]:
    bounded_workers = max(1, int(workers or 2))
    parallel = [
        sys.executable,
        "-u",
        "-m",
        "pytest",
        "-v",
        "--tb=short",
        "--durations=25",
        "--durations-min=1.0",
        "-n",
        str(bounded_workers),
        "--dist",
        "loadfile",
        "-m",
        "not serial",
    ]
    serial = [
        sys.executable,
        "-u",
        "-m",
        "pytest",
        "-v",
        "--tb=short",
        "--durations=25",
        "--durations-min=1.0",
        "-n",
        "0",
        *(serial_targets or ()),
        "-m",
        "serial",
    ]
    return parallel, serial


def _run_lane(command: list[str], *, root: Path, timeout: int) -> subprocess.CompletedProcess:
    env = _pytest_subprocess_env(skip_public_private_preflight=True)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Forward the caller's streams explicitly, including for hidden Windows
    # children. The shell already owns live output and its bounded receipt.
    kwargs = {
        "cwd": str(root),
        "stdout": sys.stdout,
        "stderr": sys.stderr,
        "env": env,
    }
    if sys.platform == "win32":
        apply_windows_hidden_process_flags(kwargs)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **kwargs)
    try:
        proc.wait(timeout=max(1, int(timeout)))
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        kill_process_tree(proc.pid)
        proc.wait()
        raise
    return subprocess.CompletedProcess(command, proc.returncode)


def run_suite(
    root: str | Path | None = None,
    *,
    workers: int = 2,
    timeout: int = DEFAULT_LANE_TIMEOUT_SECONDS,
) -> SuiteResult:
    root_path = repo_root(root)
    # The execution lanes perform authoritative collection. Repeating a full
    # collect-only pass here added about 30 seconds and substantial import work
    # before every delivery gate without exercising another behavior.
    print("[suite] preflight starting", flush=True)
    preflight = run_preflight(root_path, collect=False, timeout=min(timeout, 180))
    print(preflight.message, flush=True)
    if not preflight.ok:
        return SuiteResult(1, "[suite] preflight failed [exit code 1]")
    if not (root_path / "tests").is_dir():
        return SuiteResult(0, "[suite] skipped: no local tests overlay [exit code 0]")

    serial_targets = _serial_test_targets(root_path)
    commands = suite_commands(workers, serial_targets=serial_targets)
    for lane, command in zip(("parallel", "serial"), commands):
        if lane == "serial" and not serial_targets:
            print("[suite] serial lane skipped: no directly marked serial tests", flush=True)
            continue
        print(f"[suite] {lane} lane starting", flush=True)
        try:
            proc = _run_lane(command, root=root_path, timeout=timeout)
        except subprocess.TimeoutExpired:
            return SuiteResult(
                124, f"[suite] {lane} lane timed out after {timeout}s [exit code 124]",
            )
        except KeyboardInterrupt:
            return SuiteResult(130, f"[suite] {lane} lane interrupted [exit code 130]")
        if proc.returncode != 0:
            return SuiteResult(
                proc.returncode, f"[suite] {lane} lane failed [exit code {proc.returncode}]",
            )
        print(f"[suite] {lane} lane passed [exit code 0]", flush=True)
    return SuiteResult(0, "[suite] complete [exit code 0]")


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Run MO's delivery-only bounded parallel suite followed by required serial tests."
    )
    parser.add_argument("--root", default="", help="Project root; defaults to git top-level or cwd.")
    parser.add_argument("--workers", type=int, default=2, help="Bounded xdist worker count.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_LANE_TIMEOUT_SECONDS,
        help="Timeout for each execution lane.",
    )
    args = parser.parse_args(argv)
    result = run_suite(args.root or None, workers=args.workers, timeout=args.timeout)
    print(result.message, flush=True)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
