"""Shell execution and pytest command policy for MO's tool facade."""
from __future__ import annotations

import os
import codecs
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags
from core.tooling.sandbox import safe_env
from core.tooling.shell_processes import (
    _register_shell_process,
    _record_shell_output,
    _unregister_shell_process,
    kill_process_tree,
)
from core.utils.text_utils import cap_text_evidence


_CANONICAL_SUITE_TIMEOUT_SECONDS = 1800


def _pytest_reuse_has_selection_modifiers(args: list[str]) -> bool:
    """Return whether pytest arguments narrow or otherwise alter collection."""
    exact = {
        "-k", "-m",
        "--last-failed", "--lf", "-lf",
        "--failed-first", "--ff", "-ff",
        "--new-first", "--nf", "-nf",
        "--stepwise", "--sw", "-sw",
        "--stepwise-skip", "--sw-skip",
        "--last-failed-no-failures", "--lfnf",
        "--collect-only", "--co",
        "--ignore", "--ignore-glob", "--deselect", "--pyargs",
        "--fixtures", "--fixtures-per-test", "--help", "--version",
    }
    prefixes = (
        "--last-failed=", "--lf=", "--failed-first=", "--ff=",
        "--new-first=", "--nf=", "--stepwise=", "--sw=",
        "--stepwise-skip=", "--sw-skip=",
        "--last-failed-no-failures=", "--lfnf=",
        "--collect-only=", "--co=",
        "--ignore=", "--ignore-glob=", "--deselect=", "--pyargs=",
        "--fixtures=", "--fixtures-per-test=", "--help=", "--version=",
    )
    for raw in args:
        arg = str(raw or "").lower()
        if arg in exact or arg.startswith(prefixes):
            return True
        if arg.startswith(("-k=", "-m=")):
            return True
        if arg.startswith("-k") and not arg.startswith("--") and len(arg) > 2:
            return True
        if arg.startswith("-m") and not arg.startswith("--") and len(arg) > 2:
            return True
    return False


def _verification_command_reuse_family(command: object) -> str:
    """Return the same-turn reuse family for broad verification commands."""
    if re.search(r"[;&|<>\r\n]", str(command or "")):
        # A compound shell command has no single test exit status or scope.
        return ""
    if _looks_like_canonical_test_suite(command):
        if any(arg in {"-h", "--help"} for arg in _command_tokens(command)):
            return ""
        return "mo-test-suite:broad"
    if not _looks_like_pytest_command(command):
        return ""
    text = str(command or "").strip()
    low = text.lower()
    pytest_args = [str(arg).lower() for arg in _pytest_argument_tokens(text)]
    if any("::" in arg for arg in pytest_args) or _pytest_reuse_has_selection_modifiers(pytest_args):
        return ""
    if re.search(r'\.py[\s"\'&|]|\.py$', low):
        return ""
    targets = _pytest_positional_targets(text)
    full_roots = {".", "./tests", ".\\tests", "tests", "tests/", "tests\\", "test", "test/", "test\\"}
    if targets and any(str(target).strip("\"'") not in full_roots for target in targets):
        return ""
    return "pytest:broad"


def _looks_like_backgroundable(command: str, timeout: int) -> bool:
    """Background ONLY full pytest suites — nothing else.

    Per operator intent, backgrounding exists solely so a full test-suite run
    doesn't freeze the TUI. Individual tests (file / :: / -k) and every
    non-pytest command run FOREGROUND, so ordinary commands can never spawn
    stray "shell N ◕" background workers.
    """
    if timeout < 120:
        return False
    # Non-test commands and scoped pytest commands always run foreground.
    return bool(_verification_command_reuse_family(command))


def _configured_shell() -> str:
    """Return the operator's configured/ambient shell without forcing one globally."""
    explicit = os.environ.get("MO_TOOL_SHELL") or os.environ.get("MO_SHELL")
    if explicit and explicit.strip():
        return explicit.strip()
    if sys.platform == "win32":
        return os.environ.get("COMSPEC") or "cmd.exe"
    return os.environ.get("SHELL") or "/bin/sh"


def _repair_python_c_literal_newlines(source: str) -> str:
    """Repair encoded line separators in an otherwise valid ``python -c`` program."""
    if "\\n" not in source:
        return source
    try:
        compile(source, "<mo-shell-python-c>", "exec")
    except (SyntaxError, ValueError, TypeError):
        pass
    else:
        return source

    import io
    import tokenize

    line_offsets = [0]
    for line in source.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    protected = [False] * len(source)
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.STRING:
                continue
            start = line_offsets[token.start[0] - 1] + token.start[1]
            end = line_offsets[token.end[0] - 1] + token.end[1]
            protected[start:end] = [True] * (end - start)
    except (IndentationError, tokenize.TokenError):
        return source

    repaired: list[str] = []
    index = 0
    while index < len(source):
        unescaped = index == 0 or source[index - 1] != "\\"
        if not protected[index] and unescaped and source.startswith("\\r\\n", index):
            repaired.append("\n")
            index += 4
            continue
        if not protected[index] and unescaped and source.startswith("\\n", index):
            repaired.append("\n")
            index += 2
            continue
        repaired.append(source[index])
        index += 1

    candidate = "".join(repaired)
    if candidate == source:
        return source
    try:
        compile(candidate, "<mo-shell-python-c>", "exec")
    except (SyntaxError, ValueError, TypeError):
        return source
    return candidate


def _direct_windows_command(command: str) -> list[str] | None:
    """Return argv for simple Python/Pytest commands that do not need cmd.exe.

    The timeout path must own the real child process. Launching ``python -c`` via
    ``cmd.exe`` makes quoting brittle and can leave the Python child alive after
    the shell root is killed. Shell syntax still routes through cmd.exe.
    """
    text = str(command or "").strip()
    if not text or re.search(r"[|&<>]", text):
        return None
    try:
        # POSIX parsing treats every backslash as an escape and silently turns
        # ``E:\\project`` into ``E:project``. Windows parsing preserves path
        # separators but retains matching outer quotes, so remove only those.
        tokens = [
            token[1:-1]
            if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}
            else token
            for token in shlex.split(text, posix=False)
        ]
    except ValueError:
        return None
    if not tokens:
        return None
    exe = Path(tokens[0]).name.lower()
    if exe in {"python", "python.exe"}:
        if "-c" in tokens:
            code_index = tokens.index("-c") + 1
            if code_index < len(tokens):
                tokens[code_index] = _repair_python_c_literal_newlines(tokens[code_index])
        return [sys.executable, *tokens[1:]]
    if exe in {"py", "py.exe", "pytest", "pytest.exe"}:
        return tokens
    return None


def _shell_command(command: str) -> tuple[list[str] | str, bool, str]:
    """Build a subprocess command for the active shell family."""
    shell_exe = _configured_shell()
    shell_name = Path(shell_exe).name.lower()
    if sys.platform == "win32":
        if shell_name in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
            ps_command = (
                "$PSStyle.OutputRendering='PlainText'; "
                + command
                + "; if ($global:LASTEXITCODE -ne $null) { exit $global:LASTEXITCODE }"
            )
            return [shell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command], False, ps_command
        direct = _direct_windows_command(command)
        if direct is not None:
            return direct, False, command
        # On Windows, ``subprocess`` serializes argv lists with the C runtime's
        # quoting rules.  ``cmd.exe`` uses different rules, so embedded quotes in
        # commands such as ``findstr /C:\"two words\"`` arrive with literal
        # backslashes and change meaning.  Give CreateProcess the command line
        # cmd expects instead; the shell remains the real child (``shell=False``).
        return f'"{shell_exe}" /d /s /c "{command}"', False, command
    return [shell_exe, "-c", command], False, command


def _looks_like_pytest_command(command: object) -> bool:
    return _test_invocation(command)[0] == "pytest"


def _looks_like_canonical_test_suite(command: object) -> bool:
    return _test_invocation(command)[0] == "core.diagnostics.test_suite"


def _pytest_preflight_needed(command: object) -> bool:
    if os.environ.get("MO_TEST_RUNNER_PREFLIGHT") == "0":
        return False
    text = str(command or "").strip()
    low = text.lower()
    if not _looks_like_pytest_command(text):
        return False
    if "core.diagnostics.test_preflight" in low or "--collect-only" in low or " --co" in low:
        return False
    pytest_args = _pytest_argument_tokens(text)
    if any(
        arg in {"-k", "-m"} or arg.startswith(("-k=", "-m="))
        for arg in pytest_args
    ):
        return False
    if any(
        flag in pytest_args
        for flag in ("--last-failed", "--lf", "--failed-first", "--ff", "--pyargs")
    ):
        return False
    if any(arg.startswith("--ignore") for arg in pytest_args):
        return False
    if "::" in low:
        return False
    targets = _pytest_positional_targets(text)
    if targets and not all(target.replace("\\", "/").rstrip("/") in {".", "test", "tests"} for target in targets):
        return False
    return True


def _command_tokens(command: object) -> list[str]:
    text = str(command or "").strip()
    if not text:
        return []
    try:
        tokens = shlex.split(text, posix=sys.platform != "win32")
    except ValueError:
        tokens = text.split()
    return [part.strip("\"'") for part in tokens if str(part or "").strip("\"'")]


def _test_invocation(command: object) -> tuple[str, list[str]]:
    """Read the invoked executable/module, never names inside command data."""
    tokens = _command_tokens(command)
    if not tokens:
        return "", []
    executable = Path(tokens[0]).name.lower()
    if executable in {"pytest", "pytest.exe"}:
        return "pytest", tokens[1:]
    if not re.fullmatch(r"(?:python(?:\d+(?:\.\d+)?)?|py)(?:\.exe)?", executable):
        return "", []
    idx = 1
    while idx < len(tokens):
        token = tokens[idx]
        if token == "-m" and idx + 1 < len(tokens):
            module = tokens[idx + 1]
            if module in {"pytest", "core.diagnostics.test_suite", "core.diagnostics.test_preflight"}:
                return module, tokens[idx + 2:]
            break
        if token not in {"-u", "-B", "-E", "-I", "-s", "-S", "-O", "-OO"} and not (
            executable in {"py", "py.exe"} and re.fullmatch(r"-\d+(?:\.\d+)?", token)
        ):
            break
        idx += 1
    return "", []


def _pytest_argument_tokens(command: object) -> list[str]:
    module, arguments = _test_invocation(command)
    return arguments if module == "pytest" else []


def _pytest_positional_targets(command: object) -> list[str]:
    args = _pytest_argument_tokens(command)
    if "--pyargs" in args:
        return []
    option_values = {
        "-k", "-m", "-n", "-p", "-c", "-o",
        "--numprocesses", "--dist", "--tb", "--rootdir", "--confcutdir",
        "--basetemp", "--junitxml", "--cov", "--cov-report", "--maxfail",
        "--timeout", "--durations", "--log-cli-level", "--log-level",
    }
    targets: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            continue
        if arg.startswith("-"):
            if arg in option_values:
                skip_next = True
            continue
        targets.append(arg)
    return targets


def _pytest_explicit_file_targets(
    command: object,
    *,
    workdir: object = None,
) -> tuple[str, ...]:
    """Return a canonical selector-free pytest file set, or no reusable set."""
    if not _looks_like_pytest_command(command):
        return ()
    args = _pytest_argument_tokens(command)
    if not args or any("::" in str(arg) for arg in args):
        return ()
    if _pytest_reuse_has_selection_modifiers(args):
        return ()
    raw_targets = _pytest_positional_targets(command)
    if not raw_targets:
        return ()
    try:
        base = Path(str(workdir or os.getcwd())).expanduser()
        if not base.is_absolute():
            base = Path.cwd() / base
        base = base.resolve(strict=False)
        targets: set[str] = set()
        for raw in raw_targets:
            target = str(raw or "").strip().strip("\"'")
            if (
                not target
                or "::" in target
                or any(char in target for char in "*?[]")
                or Path(target).suffix.lower() != ".py"
            ):
                return ()
            path = Path(target).expanduser()
            if not path.is_absolute():
                path = base / path
            if path.is_dir():
                return ()
            targets.add(os.path.normcase(str(path.resolve(strict=False))))
        return tuple(sorted(targets))
    except (OSError, RuntimeError, ValueError):
        return ()


def _pytest_missing_targets(command: object, workdir: object = None) -> list[str]:
    if not _looks_like_pytest_command(command):
        return []
    base = Path(str(workdir or os.getcwd())).expanduser()
    missing: list[str] = []
    for raw in _pytest_positional_targets(command):
        target = str(raw or "").strip().strip("\"'")
        if not target or target.startswith("@"):
            continue
        path_text = target.split("::", 1)[0]
        if not path_text:
            continue
        looks_like_path = (
            "/" in path_text
            or "\\" in path_text
            or path_text.endswith(".py")
            or path_text in {".", "tests", "test"}
            or Path(path_text).is_absolute()
        )
        if not looks_like_path:
            continue
        if any(ch in path_text for ch in "*?[]"):
            raw_path = Path(path_text)
            if raw_path.is_absolute():
                glob_base = Path(raw_path.anchor)
                glob_pattern = str(raw_path.relative_to(raw_path.anchor))
            else:
                glob_base = base
                glob_pattern = path_text
            matches = list(glob_base.glob(glob_pattern))
            if not matches:
                missing.append(path_text)
            continue
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = base / path
        if not path.exists():
            missing.append(path_text)
    return missing


def _pytest_missing_targets_message(command: object, workdir: object = None) -> str | None:
    missing = _pytest_missing_targets(command, workdir)
    if not missing:
        return None
    lines = ["Error: [pytest not run — missing explicit target path(s)]"]
    lines.extend(f"- {path}" for path in missing[:20])
    if len(missing) > 20:
        lines.append(f"- ... and {len(missing) - 20} more")
    lines.append("Locate the real test file or directory with find_files/grep, then call test_runner again.")
    return "\n".join(lines)


def _shell_quote(value: object) -> str:
    text = str(value)
    if sys.platform == "win32":
        return '"' + text.replace('"', '\\"') + '"'
    return shlex.quote(text)


def _preflight_command(timeout: int = 180) -> str:
    script = Path(__file__).resolve().parents[1] / "core" / "diagnostics" / "test_preflight.py"
    return (
        f"{_shell_quote(sys.executable)} {_shell_quote(str(script))} "
        f"--collect --timeout {int(timeout or 180)}"
    )


def _pytest_worker_count() -> int:
    raw = os.environ.get("MO_PYTEST_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    cpu_count = os.cpu_count() or 2
    # The full suite already runs as one background job. Keep its internal
    # parallelism deliberately small so MO stays responsive on the operator's
    # workstation; higher counts remain an explicit MO_PYTEST_WORKERS choice.
    return max(1, min(2, max(1, cpu_count // 2)))


def _bounded_pytest_command(command: object) -> str:
    text = str(command or "")
    if not _looks_like_pytest_command(text):
        return text
    workers = _pytest_worker_count()
    text = re.sub(r"(?i)(^|\s)-n\s+auto(?=\s|$)", rf"\1-n {workers}", text)
    text = re.sub(r"(?i)(^|\s)-n=auto(?=\s|$)", rf"\1-n={workers}", text)
    text = re.sub(r"(?i)(^|\s)--numprocesses\s+auto(?=\s|$)", rf"\1--numprocesses {workers}", text)
    text = re.sub(r"(?i)(^|\s)--numprocesses=auto(?=\s|$)", rf"\1--numprocesses={workers}", text)
    return text


def _output_exit_code(output: str) -> int | None:
    match = re.search(r"\[exit code (-?\d+)\]\s*$", str(output or "").strip(), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _empty_output_note(command: object, returncode: int) -> str:
    """Message for a command that finished with no stdout/stderr.

    Keeps the exact ``[Command completed with exit code N]`` first line so
    downstream substring checks (e.g. the SSH-255 detector) still match. When
    the command is an inline ``python -c`` that exited 0, append a diagnostic:
    an empty result there almost always means a real mistake (a ``#`` in a
    one-liner comments out the rest of the physical line, or the script never
    called ``print()``). Without this the model can't tell *why* nothing showed
    and flails — re-running variants or redirecting to files, burning turns.
    Non-python and non-zero exits keep the bare message (many commands such as
    ``git add`` / ``mkdir`` are legitimately silent — no noise added there)."""
    base = f"[Command completed with exit code {returncode}]"
    text = str(command or "")
    is_inline_py = bool(re.search(r"(^|\s)(python3?|py)\s+-c\b", text, re.IGNORECASE))
    if returncode == 0 and is_inline_py:
        return (
            base + "\n"
            "This `python -c` command exited cleanly but printed nothing. Common causes: "
            "a `#` in a one-line -c comments out the rest of that physical line (including "
            "the print()); the script computed values but never called print(); or output "
            "was written to a file. Fix: put statements before any `#`, use real newlines "
            "or a temp .py file, and confirm you actually call print()."
        )
    return base


def _output_succeeded(output: str) -> bool:
    code = _output_exit_code(output)
    if code is not None:
        return code == 0
    lowered = str(output or "").lower()
    return "timed out" not in lowered and "error executing command" not in lowered


def _tool_timeout(command: object, requested: object, default: int) -> int:
    try:
        timeout = int(requested if requested is not None else default)
    except (TypeError, ValueError):
        timeout = default
    if _looks_like_canonical_test_suite(command):
        return max(timeout, _CANONICAL_SUITE_TIMEOUT_SECONDS)
    if _looks_like_pytest_command(command):
        return max(timeout, 420)
    return timeout


def _cancel_requested(cancel_event: object) -> bool:
    return bool(getattr(cancel_event, "is_set", lambda: False)())


def _wait_for_shell_process(
    proc: subprocess.Popen,
    *,
    timeout: int,
    cancel_event: object = None,
) -> str:
    """Wait cooperatively so an operator stop cannot look like exit code 0."""
    deadline = time.monotonic() + max(1, int(timeout or 1))
    while True:
        if _cancel_requested(cancel_event):
            kill_process_tree(proc.pid)
            return "cancelled"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            kill_process_tree(proc.pid)
            return "timed_out"
        try:
            proc.wait(timeout=min(0.10, remaining))
        except subprocess.TimeoutExpired:
            continue
        # The UI sets the event before its best-effort process-tree cleanup. If
        # that cleanup wakes proc.wait first, cancellation still wins over the
        # platform-specific return code.
        return "cancelled" if _cancel_requested(cancel_event) else "completed"


def _test_runner_timeout(command: object, requested: object = None) -> int:
    return _tool_timeout(command, requested, 420)


def _drain_shell_pipe(stream: Any, chunks: list[str], pid: int | None = None) -> None:
    """Drain without blocking the child; retain a bounded head and tail."""
    try:
        read_available = getattr(getattr(stream, "buffer", None), "read1", None)
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            if callable(read_available):
                raw = read_available(1024)
                chunk = decoder.decode(raw, final=not raw)
                if raw and not chunk:
                    continue
            else:
                chunk = stream.read(1024)
            if not chunk:
                break
            chunks.append(chunk)
            if pid is not None:
                _record_shell_output(pid, chunk)
            if len(chunks) > 50:
                chunks[24:26] = ["\n[...shell output omitted...]\n"]
    except (OSError, ValueError):
        pass
    finally:
        stream.close()


def _execute_shell_background(
    run_command: Callable[[], tuple[str, str]],
    command: str,
    worker_callback: Any = None,
) -> str:
    """Schedule the same process owner without blocking the foreground turn."""
    shell_id = f"shell-{uuid.uuid4().hex[:8]}"

    create_cb = worker_callback
    if callable(create_cb):
        create_cb("create", shell_id, " ".join((command or "").split())[:160])

    def _run() -> None:
        output, final_state = run_command()
        if callable(create_cb):
            create_cb("update", shell_id, final_state, cap_text_evidence(output, 2000))

    thread = threading.Thread(target=_run, name=f"mo-shell-{shell_id}", daemon=True)
    try:
        thread.start()
    except RuntimeError as exc:
        output = f"Error starting background command: {exc}"
        if callable(create_cb):
            create_cb("update", shell_id, "failed", output)
        return output

    return f"[BACKGROUND_ACTIVE|{shell_id}|{command[:100]}]"


def _shell_popen_kwargs(
    arguments: dict[str, Any],
    *,
    use_shell: bool,
    cwd: object,
) -> dict[str, Any]:
    """Build the one process policy shared by foreground and background shells."""
    env = safe_env() if bool(arguments.get("_clean_env", True)) else os.environ.copy()
    env.update({
        str(key): str(value)
        for key, value in dict(arguments.get("_env_overrides") or {}).items()
    })
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    popen_kwargs: dict[str, Any] = {
        "shell": use_shell,
        "cwd": cwd,
        # This tool has no interactive input channel. Inheriting the TUI's
        # keyboard lets a hidden child steal input and wait indefinitely.
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }
    if sys.platform == "win32":
        apply_windows_hidden_process_flags(popen_kwargs)
    else:
        popen_kwargs["start_new_session"] = True
    return popen_kwargs


def execute_shell(arguments: dict[str, Any]) -> str:
    command = str(arguments.get("command", "")).strip()
    workdir = arguments.get("workdir") or os.getcwd()
    timeout = _tool_timeout(command, arguments.get("timeout"), 60)
    cancel_event = arguments.get("_cancel_event")
    cwd = workdir

    if _cancel_requested(cancel_event):
        return "Error: Command cancelled by operator before it started."

    shell_cmd, use_shell, registered_command = _shell_command(command)
    popen_kwargs = _shell_popen_kwargs(arguments, use_shell=use_shell, cwd=cwd)

    def run_command(*, background: bool = False) -> tuple[str, str]:
        return _run_shell_process(
            shell_cmd, popen_kwargs, registered_command, command, timeout, cancel_event,
            background=background,
        )

    # Background long-running commands (unless explicitly suppressed)
    allow_background = bool(arguments.get("_allow_background", True))
    if allow_background and _looks_like_backgroundable(command, timeout):
        return _execute_shell_background(
            run_command=lambda: run_command(background=True),
            command=command,
            worker_callback=arguments.get("_worker_callback"),
        )
    return run_command()[0]


def _provider_shell_stderr(stderr: str) -> str:
    """Keep diagnostic evidence while omitting repeated live-only progress."""
    if "explainer_progress" not in stderr:
        return stderr
    import json

    from interface.formatting import explainer_activity_lines

    kept: list[str] = []
    omitted = 0
    for line in stderr.splitlines(keepends=True):
        if len(line) <= 4096 and line.endswith(("\n", "\r")) and explainer_activity_lines(line):
            # Recognition and validation stay with the existing projection owner.
            # Reading its validated state does not turn presentation into task truth.
            if json.loads(line).get("state", "running") in {"running", "completed"}:
                omitted += 1
                continue
        kept.append(line)
    if not omitted:
        return stderr
    retained = "".join(kept)
    separator = "\n" if retained and not retained.endswith(("\n", "\r")) else ""
    return f"{retained}{separator}[Omitted {omitted} explainer progress updates from model output.]"


def _run_shell_process(
    shell_cmd: list[str] | str,
    popen_kwargs: dict[str, Any],
    registered_command: str,
    command: str,
    timeout: int,
    cancel_event: object,
    *,
    background: bool = False,
) -> tuple[str, str]:
    """Own process execution, bounded output, cancellation and terminal truth."""
    proc = None
    try:
        proc = subprocess.Popen(shell_cmd, **popen_kwargs)
        _register_shell_process(
            proc, registered_command, popen_kwargs.get("cwd"), timeout,
            background=background,
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        stdout_thread = threading.Thread(target=_drain_shell_pipe, args=(proc.stdout, stdout_chunks, proc.pid), daemon=True)
        stderr_thread = threading.Thread(target=_drain_shell_pipe, args=(proc.stderr, stderr_chunks, proc.pid), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        wait_state = _wait_for_shell_process(
            proc,
            timeout=timeout,
            cancel_event=cancel_event,
        )
        stdout_thread.join(timeout=2 if wait_state == "completed" else 0.2)
        stderr_thread.join(timeout=2 if wait_state == "completed" else 0.2)
        stdout_partial = "".join(stdout_chunks)
        stderr_partial = "".join(stderr_chunks)
        partial = (
            stdout_partial
            + ("\n[stderr]\n" + stderr_partial if stderr_partial else "")
        ).strip()
        if wait_state == "cancelled":
            detail = f"\n[Partial output before cancellation]\n{partial[-6000:]}" if partial else ""
            return (
                "Error: Command cancelled by operator; the process tree was killed and "
                f"the command did not complete.{detail}"
            ), "cancelled"
        if wait_state == "timed_out":
            # Capture whatever the process printed BEFORE the kill, so a slow run
            # still yields actionable progress instead of nothing — this is what
            # stops the model from backgrounding+poll-looping (or blindly re-running)
            # a long job, which leaves it parked with no real output.
            guidance = (
                f"Error: Command timed out after {timeout}s and was killed. Do NOT re-run the "
                "same long command on a loop — that burns turns and never finishes. "
                "Inspect the partial evidence and command scope before deciding whether a "
                "narrower check or a higher `timeout` is justified."
            )
            if partial:
                return (
                    f"{guidance}\n\n[Partial output captured before the {timeout}s timeout — "
                    f"process killed, likely incomplete]\n{partial[-6000:]}"
                ), "timed_out"
            return guidance, "timed_out"

        output = "".join(stdout_chunks)
        stderr = _provider_shell_stderr("".join(stderr_chunks))
        if stderr:
            output += "\n[stderr]\n" + stderr
        if not output.strip():
            output = _empty_output_note(command, proc.returncode)
        output = output.rstrip() + f"\n[exit code {proc.returncode}]"
        state = "completed" if proc.returncode == 0 else "failed"
        return cap_text_evidence(output, 50_000), state
    except Exception as e:
        return f"Error executing command: {e}", "failed"
    finally:
        if proc is not None:
            if proc.poll() is None:
                kill_process_tree(proc.pid)
            _unregister_shell_process(proc.pid)
