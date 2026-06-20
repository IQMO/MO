"""Sandbox — tool dispatch safety for MO.

This is the ONLY gate between the model's tool choice and actual execution.
Single source of truth for: path allowlisting, shell safety, network policy,
secret redaction, lane enforcement.

No tool profiles. No protocol routing. Just: should this tool call execute?
"""

import os
import re
import shlex
from pathlib import Path
from typing import Any
import traceback

from .tool_constants import ACTUATION_TOOLS, MUTATING_TOOLS, READ_ONLY_LANES
from .text_safety import SECRET_NAME_PATTERN, PROVIDER_TOKEN_PATTERN, contains_hardcoded_secret_literal


def _emit_sandbox_event(event_type: str, payload: dict[str, Any]) -> None:
    try:
        from .backend_monitor import get_monitor
        monitor = get_monitor()
        if monitor:
            monitor.emit(event_type, payload)
    except Exception:
        traceback.print_exc()


# ── Hard boundary patterns (from guard_policy) ─────────────────────
# These detect commands that touch deployment, production, git push,
# destructive operations, or credential exposure.

HARD_BOUNDARY_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bdeploy(?:ment|ing)?\b|\brelease\b|\bgo\s+live\b|\bvps\b|\bremote\b", re.IGNORECASE),
    re.compile(r"\bto\s+production\b|\bproduction\s+(deploy|release|server|database|site|account)\b|\bprod\b|\blive\s+(site|system|server|account|trading)\b", re.IGNORECASE),
    re.compile(r"\bpush\s+to\s+(?:origin|github|remote|main|prod|production)\b|\bforce[-\s]?push\b", re.IGNORECASE),
    re.compile(r"\brewrite\s+history\b|\breset\s+--hard\b|\bdelete\s+repo\b|\bdrop\s+table\b|\btruncate\b", re.IGNORECASE),
    re.compile(r"\bcredential(?:s)?\b|\bsecret(?:s)?\b|\boauth\b|\bprivate\s+key\b|\bbearer\b", re.IGNORECASE),
    re.compile(r"\b(?:api|access|auth|oauth|bearer)\s+token\b|\btoken\s+(?:rotation|value|secret|credential)\b", re.IGNORECASE),
    re.compile(r"\bwallet\b|\bpayment\b|\bbilling\b|\bdatabase\s+migration\b|\bexternal\s+account\b", re.IGNORECASE),
]

ABSOLUTE_PATH_PATTERN = re.compile(r"([a-z]:[\\/][^\s\"'`;|&<>]+|(?<![\w.~-])/[^\s\"'`;|&<>]+)", re.IGNORECASE)

# Shell variable / tilde expansion that resolves to a real path at execution time
# (e.g. `~/secret`, `$HOME/x`, `%USERPROFILE%\x`, `$env:APPDATA\x`). The static path
# scanner sees no path literal for these, so they would otherwise escape allowed
# roots. Match a var/tilde reference immediately followed by a path separator.
_SHELL_VAR_PATH_PATTERN = re.compile(
    r"(?:~|%(?P<pv>[^%\s]+)%|\$\{(?P<bv>[^}\s]+)\}|\$env:(?P<ev>\w+)|\$(?P<sv>\w+))[\\/]"
)
# Variables that resolve to the current working directory (inside the project
# root by construction), so expanding them does not escape scope.
_SHELL_CWD_VARS = {"pwd", "cd", "oldpwd", "cwd"}

_UNIX_ROOT_PATH_NAMES = {
    "bin", "boot", "dev", "etc", "home", "lib", "lib64", "media", "mnt",
    "opt", "proc", "root", "run", "sbin", "srv", "sys", "tmp", "usr", "var",
}


def _touches_hard_boundary(text: str) -> bool:
    """True if text matches any hard boundary pattern.

    Quoted strings are masked before matching so that
    `grep "credential"` is not blocked by the credential pattern.
    Same masking approach as shell_command_is_mutating.
    """
    masked = _mask_quoted_shell_text(text)
    return any(pattern.search(masked) for pattern in HARD_BOUNDARY_PATTERNS)


def _command_text_for_hard_boundary(command: str) -> str:
    """Return command text with SSH connection metadata removed from boundary scan."""
    parts = _split_shell_words(command)
    if not parts:
        return str(command or "")
    executable = Path(str(parts[0]).strip("'\"")).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable != "ssh":
        return str(command or "")
    value_options = {"-b", "-c", "-d", "-e", "-f", "-i", "-j", "-l", "-m", "-o", "-p", "-q", "-s", "-w"}
    idx = 1
    while idx < len(parts):
        part = str(parts[idx]).strip("'\"")
        if part == "--":
            idx += 1
            break
        if part.startswith("-"):
            lower = part.lower()
            if lower in value_options and idx + 1 < len(parts):
                idx += 2
                continue
            idx += 1
            continue
        break
    if idx < len(parts):
        idx += 1
    remote_parts: list[str] = []
    for part in parts[idx:]:
        s = str(part)
        # Strip all quote characters from remote-command parts so the
        # boundary scanner sees the real words regardless of quoting style.
        s = s.replace('"', '').replace("'", '')
        # Remote filesystem paths are not local sandbox paths and may contain
        # words such as prod/vps as directory names. Scan the command words,
        # not path literals, for hard-boundary intent.
        s = ABSOLUTE_PATH_PATTERN.sub("", s)
        remote_parts.append(s)
    return "ssh " + " ".join(remote_parts)


# ── Shell safety patterns ──────────────────────────────────────────

SHELL_MUTATION_PATTERNS = [
    r"\b(rm|del|erase|rmdir|mkdir|touch|mv|move|cp|copy|xcopy|robocopy)\b",
    r"\b(remove-item|set-content|add-content|out-file|new-item|move-item|copy-item|rename-item|clear-content)\b",
    r"\b(git\s+(add|commit|push|reset|checkout|clean|rm|mv|restore\s+--staged))\b",
    r"\b(pip|npm|pnpm|yarn)\s+(install|add|remove|uninstall|update)\b",
    r"(^|[^<])>>?\s*[^&\s]",
]

SHELL_ESCAPE_PATTERNS = [
    r"(?i)(^|[;&|]\s*)(cd|chdir|set-location|sl|pushd)\s+\.\.",
    r"(?i)\.\.[\\/]",
    r"(?i)\b(start-process|invoke-expression|iex|setx)\b",
    r"(?i)\b(powershell|pwsh|cmd|bash|wsl)\s+(-|/c)",
]

SHELL_NETWORK_PATTERNS = [
    r"(?i)\b(curl|wget|ssh|scp|sftp|ftp|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b",
    r"(?i)\bgit\s+(clone|fetch|pull|push|ls-remote)\b",
]

# ── Secret redaction patterns ──────────────────────────────────────

# Python type/keyword tokens that appear as the "value" in code like
# `token: str` or `secret: Optional[int]` — these are annotations, not secrets.
_CODE_VALUE_TOKENS = frozenset({
    "str", "int", "float", "bool", "bytes", "none", "any", "optional", "list",
    "dict", "set", "frozenset", "tuple", "callable", "sequence", "mapping",
    "iterable", "object", "type", "true", "false", "null", "self", "cls",
})


def _redact_named_secret_value(match: "re.Match") -> str:
    """Redact `name = value` only when *value* is a secret literal, not code.

    Skips Python type annotations (`token: str`) and function-call / attribute
    values (`secret = compute_value()`); the call form is excluded by the
    pattern's trailing negative lookahead. This stops the redactor from mangling
    code in session/compaction reads (which made MO chase phantom findings).
    """
    prefix, value = match.group(1), match.group(2)
    # Quoted value is a secret literal → redact (`api_key = "hunter2"`).
    if value[:1] in "\"'":
        return prefix + "[redacted]"
    # Function call or subscripted type → code, not a secret
    # (`secret = compute_value()`, `key = cfg.get_key()`, `token: Optional[str]`).
    if "(" in value or "[" in value:
        return match.group(0)
    # Bare Python type/keyword used as an annotation (`token: str`).
    if value.rstrip(".,:;)").lower() in _CODE_VALUE_TOKENS:
        return match.group(0)
    return prefix + "[redacted]"


SENSITIVE_VALUE_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+"), r"\1[redacted]"),
    (re.compile(r"(?i)([\"']?authorization[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?)[^\s\"',}]+"), r"\1[redacted]"),
    # Secret name = value. Only fires on secret-SHAPED values (quoted, or long
    # unquoted high-entropy strings) so it never mangles type annotations or code
    # like `token: str` / `secret = compute()` — which previously corrupted code
    # reads in session/compaction and made MO chase phantom `[redacted]` findings.
    (re.compile(r"(?i)([\"']?(?:" + SECRET_NAME_PATTERN + r")[\"']?\s*[:=]\s*)([^\s,;}]+)"), _redact_named_secret_value),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), "sk-[redacted]"),
    # High-confidence standalone provider tokens (single-sourced in text_safety).
    (re.compile(r"(?i)\b(?:" + PROVIDER_TOKEN_PATTERN + r")\b"), "[redacted-token]"),
    # SSH user@host patterns — redact target to avoid leaking server access details
    (re.compile(r"\b([a-z_][a-z0-9_-]{1,32})@(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z]{2,})"), r"\1@[redacted-host]"),
    # PEM private key blocks (same as critic PRIVATE_KEY_BLOCK_RE)
    (re.compile(r"-----BEGIN\s+[A-Z0-9 ]*PRIVATE\s+KEY-----.*?-----END\s+[A-Z0-9 ]*PRIVATE\s+KEY-----", re.IGNORECASE | re.DOTALL), "[redacted private key]"),
    # Bare IPv4 addresses — redact non-public server IPs in tool audit data
    (re.compile(r"\b(?<!\d)(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"), "[redacted-ip]"),
]

# ── Path safety ────────────────────────────────────────────────────

def path_allowed(path: str, allowed_roots: list[str] | None) -> bool:
    if not allowed_roots:
        return True
    if path is None:
        return False
    try:
        target = Path(path).expanduser().resolve()
    except Exception:
        return False

    for root in allowed_roots:
        try:
            if root is None or not str(root).strip():
                continue
            base = Path(root).expanduser().resolve()
            target.relative_to(base)
            return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


# ── Shell command analysis ─────────────────────────────────────────

def _mask_quoted_shell_text(command: str) -> str:
    """Mask shell-quoted literals so code strings don't look mutating."""
    chars = list(str(command or ""))
    quote = ""
    escaped = False
    for idx, ch in enumerate(chars):
        if escaped:
            if quote:
                chars[idx] = " "
            escaped = False
            continue
        if ch == "\\":
            if quote:
                chars[idx] = " "
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = ""
            else:
                chars[idx] = " "
            continue
        if ch in {"'", '"'}:
            quote = ch
    return "".join(chars)


def _split_shell_words(command: str) -> list[str]:
    try:
        return shlex.split(str(command or ""), posix=False)
    except ValueError:
        return str(command or "").split()


def _split_shell_words_posix(command: str) -> list[str]:
    try:
        return shlex.split(str(command or ""), posix=True)
    except ValueError:
        return str(command or "").split()


def _path_scan_command_text(command: str) -> str:
    """Return shell text to scan for real path arguments.

    DEVMODE05 often verifies sandbox behavior with inline `python -c` snippets
    that pass outside-root paths as strings into `guard_tool_call`. Those strings
    are test data, not shell path arguments, so remove only safe sandbox self-test
    code from the path scanner. General Python snippets are still scanned.
    """
    command = _ssh_command_text_for_local_path_scan(command)
    parts = _split_shell_words_posix(command)
    if len(parts) < 3:
        return str(command or "")
    executable = Path(str(parts[0]).strip("'\"")).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable not in {"python", "python3", "py"}:
        return str(command or "")
    try:
        code_idx = parts.index("-c") + 1
    except ValueError:
        return str(command or "")
    if code_idx >= len(parts):
        return str(command or "")
    code = str(parts[code_idx] or "")
    if not _is_safe_sandbox_inline_self_test(code):
        return str(command or "")
    return " ".join(part for idx, part in enumerate(parts) if idx != code_idx)


def _is_python_inline_command(command: str) -> bool:
    parts = _split_shell_words_posix(command)
    if len(parts) < 3:
        return False
    executable = Path(str(parts[0]).strip("'\"")).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    return executable in {"python", "python3", "py"} and "-c" in parts


def _is_quoted_python_slash_literal(command: str, start: int, end: int, candidate: str) -> bool:
    """Return True for inline Python slash-command literals, not paths."""
    if not _is_python_inline_command(command):
        return False
    if not re.fullmatch(r"/[A-Za-z][A-Za-z0-9_-]*", candidate or ""):
        return False
    name = candidate[1:].lower()
    if name in _UNIX_ROOT_PATH_NAMES:
        return False
    if start <= 0 or end >= len(command):
        return False
    return command[start - 1] in {"'", '"'} and command[end] == command[start - 1]


def _escape_scan_command_text(command: str) -> str:
    """Return shell text to scan for shell-level escapes.

    Inline Python code can legitimately contain string literals such as
    ``"stdout...\\n"``. The raw shell text then includes ``..\\`` inside a
    quoted Python string, which looks like path traversal to the shell escape
    regex even though it is not a shell argument. Remove only the Python ``-c``
    code argument; keep every surrounding shell argument such as redirection or
    real ``../`` paths visible to the scanner.
    """
    parts = _split_shell_words_posix(command)
    if len(parts) < 3:
        return str(command or "")
    executable = Path(str(parts[0]).strip("'\"")).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable not in {"python", "python3", "py"}:
        return str(command or "")
    try:
        code_idx = parts.index("-c") + 1
    except ValueError:
        return str(command or "")
    if code_idx >= len(parts):
        return str(command or "")
    return " ".join(part for idx, part in enumerate(parts) if idx != code_idx)


def _ssh_command_text_for_local_path_scan(command: str) -> str:
    parts = _split_shell_words_posix(command)
    if not parts:
        return str(command or "")
    executable = Path(str(parts[0]).strip("'\"")).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable != "ssh":
        return str(command or "")

    value_options = {"-b", "-c", "-d", "-e", "-f", "-i", "-j", "-l", "-m", "-o", "-p", "-q", "-s", "-w"}
    idx = 1
    while idx < len(parts):
        part = str(parts[idx]).strip("'\"")
        if part == "--":
            idx += 1
            break
        if part.startswith("-"):
            lower = part.lower()
            if lower in value_options and idx + 1 < len(parts):
                idx += 2
                continue
            idx += 1
            continue
        break
    if idx >= len(parts):
        return str(command or "")

    local_parts = list(parts[: idx + 1])
    remote_parts: list[str] = []
    preserve_next_local_path = False
    for part in parts[idx + 1:]:
        text = str(part)
        if preserve_next_local_path:
            remote_parts.append(text)
            preserve_next_local_path = False
            continue
        if text in {"<", ">", ">>", "2>", "2>>"}:
            remote_parts.append(text)
            preserve_next_local_path = True
            continue
        if re.match(r"^\d*[<>]", text):
            remote_parts.append(text)
            continue
        remote_parts.append(ABSOLUTE_PATH_PATTERN.sub("", text))
    return " ".join(local_parts + remote_parts)


def _is_safe_sandbox_inline_self_test(code: str) -> bool:
    text = str(code or "")
    if "core.sandbox" not in text:
        return False
    if not any(name in text for name in ("guard_tool_call", "shell_paths_allowed", "path_allowed")):
        return False
    unsafe = (
        "open(",
        ".read_text(",
        ".write_text(",
        "read_bytes(",
        "write_bytes(",
        "subprocess",
        "os.system",
        "popen",
        "exec(",
        "eval(",
        "unlink(",
        "rmtree(",
    )
    lowered = text.lower()
    return not any(token in lowered for token in unsafe)


def _shell_segment_executable_before(command: str, pos: int) -> str:
    """Return the executable for the shell segment containing ``pos``."""
    segment_start = -1
    for sep in ("|", "&", ";"):
        segment_start = max(segment_start, str(command or "").rfind(sep, 0, pos))
    segment = str(command or "")[segment_start + 1 : pos].strip()
    token = (segment.split(maxsplit=1) or [""])[0].strip("'\"").lower()
    if token.endswith(".exe"):
        token = token[:-4]
    return token.replace("\\", "/").rsplit("/", 1)[-1]


def _windows_builtin_before_slash_flag(command: str, pos: int, builtins: set[str]) -> str:
    """Return nearest Windows builtin before a slash flag in compound syntax.

    `if exist "path" (dir "path" /b)` belongs to the inner `dir` command, but
    simple segment splitting sees the executable as `if`.  This helper keeps
    path scanning strict while recognizing slash flags attached to a local
    Windows builtin inside parentheses.
    """
    segment_start = -1
    for sep in ("|", "&", ";"):
        segment_start = max(segment_start, str(command or "").rfind(sep, 0, pos))
    segment = str(command or "")[segment_start + 1 : pos]
    matches = list(re.finditer(r"(?i)(?:^|[\s(])([A-Za-z][\w.-]*)(?:\.exe)?(?=\s|$)", segment))
    for match in reversed(matches):
        token = match.group(1).lower()
        if token in builtins:
            return token
    return ""


def shell_command_is_mutating(command: str) -> bool:
    lowered = _mask_quoted_shell_text(command).lower()
    return any(re.search(pattern, lowered) for pattern in SHELL_MUTATION_PATTERNS)


def shell_command_escapes(command: str) -> bool:
    text = _escape_scan_command_text(command or "")
    return any(re.search(pattern, text) for pattern in SHELL_ESCAPE_PATTERNS)


def shell_command_uses_network(command: str) -> bool:
    return any(re.search(pattern, command or "") for pattern in SHELL_NETWORK_PATTERNS)


def shell_paths_allowed(command: str, allowed_roots: list[str] | None) -> bool:
    if not allowed_roots:
        return True
    raw_command = _path_scan_command_text(command or "")
    # Variable/tilde expansion into a path escapes the static scope check; block
    # it when roots are restricted unless it is a current-dir variable.
    for m in _SHELL_VAR_PATH_PATTERN.finditer(raw_command):
        var = (m.group("pv") or m.group("bv") or m.group("ev") or m.group("sv") or "").lower()
        if var in _SHELL_CWD_VARS:
            continue
        return False
    first_token = (raw_command.strip().split(maxsplit=1) or [""])[0].lower()
    windows_slash_flags = {
        "dir",
        "find",
        "findstr",
        "where",
        "type",
        "copy",
        "xcopy",
        "robocopy",
        "del",
        "erase",
        "move",
        "ren",
        "rename",
        "rmdir",
        "rd",
        "mkdir",
        "md",
    }
    # Match both Unix absolute paths and Windows drive-letter paths
    for match in ABSOLUTE_PATH_PATTERN.finditer(raw_command):
        candidate = match.group(1)
        start, end = match.span(1)
        # HTML/XML closing tags inside quoted code snippets look like `/html`
        # to the path regex. Skip only real tag syntax (`</tag>`), not shell
        # input redirection such as `</etc/shadow`.
        if (
            candidate.startswith("/")
            and start > 0
            and raw_command[start - 1] == "<"
            and end < len(raw_command)
            and raw_command[end] == ">"
            and re.fullmatch(r"/[A-Za-z][A-Za-z0-9:-]*", candidate)
        ):
            continue
        if candidate.startswith("/") and _is_quoted_python_slash_literal(raw_command, start, end, candidate):
            continue
        # Windows built-ins commonly use slash flags, e.g. `dir fun /b` or
        # `python tool.py | findstr /V pattern`. Do not mistake those flags
        # for Unix absolute paths.
        segment_token = _shell_segment_executable_before(raw_command, start)
        if (
            (
                first_token in windows_slash_flags
                or segment_token in windows_slash_flags
                or _windows_builtin_before_slash_flag(raw_command, start, windows_slash_flags) in windows_slash_flags
            )
            and re.fullmatch(r"/[A-Za-z][A-Za-z0-9:.-]*", candidate)
        ):
            continue
        # Windows drive-letter absolute paths (e.g. C:\..., d:/...) are real
        # filesystem paths and must be scope-checked exactly like Unix paths.
        # Without this they fell through to `return True`, letting shell reads
        # such as `type C:\Users\victim\secret.txt` escape the configured roots.
        if re.match(r"[A-Za-z]:[\\/]", candidate):
            if not path_allowed(candidate, allowed_roots):
                return False
            continue
        if candidate.startswith("/") and not candidate.startswith("//"):
            # Only treat as path if it looks like a filesystem path (has letters/dots)
            if re.search(r"[a-zA-Z.]", candidate):
                if not path_allowed(candidate, allowed_roots):
                    return False
    return True


# ── Secret redaction ───────────────────────────────────────────────

def redact_sensitive_text(text: str) -> str:
    redacted = str(text)
    for pattern, repl in SENSITIVE_VALUE_PATTERNS:
        redacted = pattern.sub(repl, redacted)
    return redacted


# Only unambiguous provider-token shapes (ghp_/xoxb-/AKIA…/Stripe/Google). Unlike
# redact_sensitive_text this does NOT touch `name = value` assignments, so it is safe
# to run over every tool result without corrupting source-code reads.
_PROVIDER_TOKEN_ONLY_RE = re.compile(r"(?i)\b(?:" + PROVIDER_TOKEN_PATTERN + r")\b")


def redact_provider_tokens(text: str) -> str:
    """Strip unambiguous secret tokens from text before it reaches the provider.

    Defense-in-depth for the case where a secret value slips into a tool result
    (e.g. a shell command that echoes a token). Conservative by design: only
    well-known token prefixes, never generic key=value, so code reads are intact.
    """
    try:
        return _PROVIDER_TOKEN_ONLY_RE.sub("[redacted-token]", str(text or ""))
    except Exception:
        return str(text or "")


# ── Safe environment ───────────────────────────────────────────────

SAFE_ENV_ALLOWLIST = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONUTF8",
}

SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "AUTH", "COOKIE", "CREDENTIAL")


def safe_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in SAFE_ENV_ALLOWLIST and not any(marker in upper for marker in SECRET_ENV_MARKERS):
            env[key] = value
    env.setdefault("NO_COLOR", "1")
    env.setdefault("CLICOLOR", "0")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    # Prepend Git usr/bin to PATH so Git's working OpenSSH is found before
    # the broken Windows System32 OpenSSH (which exits 255 silently).
    git_ssh_dir = os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Git", "usr", "bin")
    if os.path.isdir(git_ssh_dir):
        existing = env.get("PATH", "")
        env["PATH"] = git_ssh_dir + os.pathsep + existing
    return env


# ── Tool argument validation ───────────────────────────────────────

_REQUIRED_TOOL_ARGS: dict[str, tuple[str, ...]] = {
    "read_file": ("path",),
    "write_file": ("path", "content"),
    "edit_file": ("path", "old_text", "new_text"),
    "shell": ("command",),
    "grep": ("pattern",),
    "web_fetch": ("url",),
    "web_snapshot": ("url",),
    "web_search": ("query",),
}

_NONBLANK_TOOL_ARGS: dict[str, tuple[str, ...]] = {
    "read_file": ("path",),
    "write_file": ("path",),
    "edit_file": ("path", "old_text"),
    "shell": ("command",),
    "grep": ("pattern",),
    "web_fetch": ("url",),
    "web_snapshot": ("url",),
    "web_search": ("query",),
}

_OPTIONAL_NONBLANK_TOOL_ARGS: dict[str, tuple[str, ...]] = {
    "test_runner": ("command",),
}

_MCP_PATH_ARGUMENT_NAMES = {
    "path",
    "paths",
    "root",
    "roots",
    "workdir",
    "cwd",
    "dir",
    "directory",
    "file",
    "files",
    "file_path",
    "file_paths",
    "filepath",
    "source",
    "src",
    "destination",
    "dest",
    "target",
    "to",
    "from",
}

# Match a mutating verb at a word boundary, including camelCase (writeFile) and
# snake_case (write_file). The verb alternation is case-insensitive via (?i:...);
# the trailing camelCase boundary `(?=[A-Z])` stays case-sensitive on purpose.
_MCP_MUTATING_NAME_PATTERN = re.compile(
    r"(?:^|_)(?i:write|edit|create|delete|remove|move|rename|patch|apply|update|commit|push|"
    r"merge|deploy|run|overwrite|truncate|drop|clear|unlink|mkdir|set|put|append|insert|upload)"
    r"(?:_|$|(?=[A-Z]))",
)


def _iter_mcp_path_values(arguments: dict[str, Any]) -> "list[str]":
    """Collect candidate path strings from MCP tool arguments.

    Real MCP servers expose paths as scalars (``path``), lists
    (``paths``/``file_paths``), and nested option objects
    (``options.path``). A flat exact-key check misses the list/plural/nested
    forms, so scope-checking must look one level into list and dict values.
    """
    found: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            if value.strip():
                found.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item.strip():
                    found.append(item)

    for key, value in (arguments or {}).items():
        if str(key).lower() in _MCP_PATH_ARGUMENT_NAMES:
            add(value)
        elif isinstance(value, dict):
            for nkey, nval in value.items():
                if str(nkey).lower() in _MCP_PATH_ARGUMENT_NAMES:
                    add(nval)
    return found


def _validate_tool_arguments(name: str, arguments: dict[str, Any]) -> str | None:
    args = arguments or {}
    for key in _REQUIRED_TOOL_ARGS.get(name, ()):
        if key not in args:
            return f"[TOOL BLOCKED] {name} missing required argument: {key}."
        if key in _NONBLANK_TOOL_ARGS.get(name, ()) and not str(args.get(key) or "").strip():
            return f"[TOOL BLOCKED] {name} blank required argument: {key}."
    for key in _OPTIONAL_NONBLANK_TOOL_ARGS.get(name, ()):
        if key in args and not str(args.get(key) or "").strip():
            return f"[TOOL BLOCKED] {name} blank required argument: {key}."
    return None


def _large_existing_write_reason(arguments: dict[str, Any], *, max_lines: int = 250) -> str | None:
    """Block giant full rewrites of existing files; targeted edit_file chunks are safer."""
    if max_lines <= 0:
        return None
    content = str((arguments or {}).get("content") or "")
    lines = len(content.splitlines())
    if lines <= max_lines:
        return None
    path_text = str((arguments or {}).get("path") or "").strip()
    if not path_text:
        return None
    target = Path(path_text)
    if not target.is_absolute():
        target = Path.cwd() / target
    try:
        if not target.exists() or not target.is_file():
            return None
    except OSError:
        return None
    return (
        f"[TOOL BLOCKED] write_file large existing-file rewrite ({lines} lines > {max_lines}). "
        "Use edit_file exact replacements in smaller chunks instead of rewriting the whole existing file."
    )


# ── Tool-family guards (extracted from guard_tool_call) ─────────────

def _guard_web_tools(name: str, arguments: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """Check web/network tool restrictions. Return block reason or None."""
    if name not in {"web_fetch", "web_snapshot", "web_search"}:
        return None
    if cfg.get("enabled") and not cfg.get("web_fetch_enabled", True):
        return f"[SANDBOX BLOCKED] {name} network access disabled."
    if cfg.get("enabled") and cfg.get("web_fetch_allowed_hosts"):
        from urllib.parse import urlparse
        host = "api.duckduckgo.com" if name == "web_search" else (
            urlparse(str(arguments.get("url", ""))).hostname or ""
        ).lower()
        allowed = {str(h).lower() for h in (cfg.get("web_fetch_allowed_hosts") or [])}
        if host not in allowed:
            return f"[SANDBOX BLOCKED] {name} host not allowed: {host or '?'}"
    return None


def _guard_shell_tool(
    name: str,
    arguments: dict[str, Any],
    cfg: dict[str, Any],
    lane: str | None,
    allowed_roots: list[str] | None,
    operator_override: bool,
) -> str | None:
    """Check shell command safety. Return block reason or None."""
    if name != "shell":
        return None
    command = str(arguments.get("command", ""))
    boundary_text = _command_text_for_hard_boundary(command)
    if not operator_override and _touches_hard_boundary(boundary_text):
        return (
            "[HARD BOUNDARY] shell command touches deployment, credentials, "
            "or destructive operation. Use operator_override to bypass when "
            "explicitly approved."
        )
    if cfg.get("block_shell_escape", True) and shell_command_escapes(command):
        return "[SANDBOX BLOCKED] shell escape/path traversal blocked."
    if cfg.get("enabled") and not cfg.get("shell_network_enabled", True) and shell_command_uses_network(command):
        return "[SANDBOX BLOCKED] shell network command blocked."
    if lane in READ_ONLY_LANES and shell_command_is_mutating(command):
        return f"[LANE LOCKED] shell mutation blocked in {lane} lane."
    if not shell_paths_allowed(command, allowed_roots):
        return "[PATH BLOCKED] shell command references a path outside allowed roots."
    return None


def _guard_test_runner(
    name: str,
    arguments: dict[str, Any],
    cfg: dict[str, Any],
    lane: str | None,
    allowed_roots: list[str] | None,
) -> str | None:
    """Check test_runner safety. Return block reason or None."""
    if name != "test_runner":
        return None
    command = str(arguments.get("command", "python -m pytest -q"))
    if lane in READ_ONLY_LANES and shell_command_is_mutating(command):
        return f"[LANE LOCKED] test_runner mutation blocked in {lane} lane."
    if cfg.get("block_shell_escape", True) and shell_command_escapes(command):
        return "[SANDBOX BLOCKED] shell escape/path traversal blocked."
    if cfg.get("enabled") and not cfg.get("shell_network_enabled", True) and shell_command_uses_network(command):
        return "[SANDBOX BLOCKED] shell network command blocked."
    if not shell_paths_allowed(command, allowed_roots):
        return "[PATH BLOCKED] test_runner command references a path outside allowed roots."
    return None


def _profile_read_root() -> str | None:
    """The operator's profile dir (~/.mo/memory/profile) — read-allowed so MO can
    read its OWN profile on demand. Only this subdir, never the rest of ~/.mo
    (no .env/secrets), and never for write/edit tools."""
    try:
        from .path_defaults import mo_home
        return str(Path(mo_home({})) / "memory" / "profile")
    except Exception:
        return None


def _readable_under_profile(path: str | None) -> bool:
    if not path:
        return False
    root = _profile_read_root()
    return bool(root) and path_allowed(str(path), [root])


def _guard_path_scope(
    name: str,
    arguments: dict[str, Any],
    allowed_roots: list[str] | None,
) -> str | None:
    """Check file/path scope for find, grep, git, project_bridge tools.
    Also handles shell workdir path check.
    Return block reason or None."""
    # File tools: read_file, write_file, edit_file. read_file may ALSO reach the
    # operator's own profile dir (read-only); write/edit stay on allowed_roots.
    if name in {"read_file", "write_file", "edit_file"} and "path" in arguments:
        path = str(arguments["path"])
        readable_profile = name == "read_file" and _readable_under_profile(path)
        if not readable_profile and not path_allowed(path, allowed_roots):
            return f"[PATH BLOCKED] {name} outside allowed roots: {arguments['path']}"
    # Shell workdir
    if name == "shell" and arguments.get("workdir"):
        if not path_allowed(str(arguments["workdir"]), allowed_roots):
            return f"[PATH BLOCKED] shell workdir outside allowed roots: {arguments['workdir']}"
    # Find/grep/git/test_runner/project_bridge root/workdir
    if name in {"find_files", "grep", "git_status", "test_runner", "project_bridge"}:
        path_value = arguments.get("root") or arguments.get("workdir")
        if name == "project_bridge":
            path_value = path_value or arguments.get("path")
        readable_profile = name in {"find_files", "grep"} and _readable_under_profile(str(path_value) if path_value else None)
        if path_value and not readable_profile and not path_allowed(str(path_value), allowed_roots):
            return f"[PATH BLOCKED] {name} path outside allowed roots: {path_value}"
    return None


def _guard_mcp_tool(
    name: str,
    arguments: dict[str, Any],
    lane: str | None,
    allowed_roots: list[str] | None,
) -> str | None:
    if not str(name or "").startswith("mcp__"):
        return None
    if lane in READ_ONLY_LANES and _MCP_MUTATING_NAME_PATTERN.search(str(name or "")):
        return f"[LANE LOCKED] {name} blocked in {lane} lane."
    for value in _iter_mcp_path_values(arguments):
        if not path_allowed(value, allowed_roots):
            return f"[PATH BLOCKED] {name} path outside allowed roots: {value}"
    return None


def _guard_large_write(arguments: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """Check write_file for large existing-file rewrites. Return block reason or None."""
    if "path" not in arguments:
        return None
    try:
        max_lines = int(cfg.get("write_file_existing_max_lines", 250) or 0)
    except (TypeError, ValueError):
        max_lines = 250
    return _large_existing_write_reason(arguments, max_lines=max_lines)


# ── Main guard function ────────────────────────────────────────────

def _guard_write_secret(name: str, arguments: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """Block writing an unambiguous hardcoded secret literal into a file.

    Closes the gap where the turn-end security_check only *reported* secrets in
    written files (telemetry) — this stops the write at dispatch time. Uses the
    precise literal detector (not the over-broad redaction pattern) so ordinary
    code (env refs, placeholders, expression RHS) is never blocked.
    """
    if not cfg.get("block_write_secrets", True):
        return None
    if name == "write_file":
        content = str((arguments or {}).get("content") or "")
    elif name == "edit_file":
        content = str((arguments or {}).get("new_text") or "")
    else:
        return None
    if contains_hardcoded_secret_literal(content):
        return (
            "[TOOL BLOCKED] this write embeds a hardcoded secret value (API key/token/"
            "private key). Use an environment variable or config reference instead of a "
            "literal credential. If this is genuinely intended, the operator must approve it explicitly."
        )
    return None


def guard_tool_call(
    name: str,
    arguments: dict[str, Any],
    lane: str | None = None,
    allowed_roots: list[str] | None = None,
    sandbox_config: dict[str, Any] | None = None,
    operator_override: bool = False,
) -> str | None:
    """Return a block reason string if the tool call should be blocked, else None.

    This is the SINGLE gate. Called at dispatch time. The model asked for a tool;
    we either allow it or block it with a reason.

    operator_override=True skips hard boundary checks for commands the operator
    explicitly approved (e.g. "yes push it").
    """
    cfg = sandbox_config or {}

    def block(reason: str) -> str:
        _emit_sandbox_event("sandbox_blocked", {"tool": name, "lane": lane or "", "reason": reason[:240]})
        return reason

    _emit_sandbox_event("sandbox_guard", {"tool": name, "lane": lane or "", "enabled": bool(cfg.get("enabled"))})

    # Lane guard: block mutating + desktop-actuation tools in read-only lanes
    if lane in READ_ONLY_LANES and (name in MUTATING_TOOLS or name in ACTUATION_TOOLS):
        return block(f"[LANE LOCKED] {name} blocked in {lane} lane.")

    argument_error = _validate_tool_arguments(name, arguments)
    if argument_error:
        return block(argument_error)

    # Web/network tools
    if reason := _guard_web_tools(name, arguments, cfg):
        return block(reason)

    # Shell safety
    if reason := _guard_shell_tool(name, arguments, cfg, lane, allowed_roots, operator_override):
        return block(reason)

    # Path scope (file tools, shell workdir, find/grep/git/project_bridge)
    if reason := _guard_path_scope(name, arguments, allowed_roots):
        return block(reason)

    # MCP tools are dynamic; enforce generic path and read-only lane rules.
    if reason := _guard_mcp_tool(name, arguments, lane, allowed_roots):
        return block(reason)

    # write_file large existing-file guard
    if name == "write_file":
        if reason := _guard_large_write(arguments, cfg):
            return block(reason)

    # Write-time secret guard: block hardcoded secret literals in file content.
    # operator_override (explicit in-turn approval) bypasses, mirroring shell guards.
    if name in {"write_file", "edit_file"} and not operator_override:
        if reason := _guard_write_secret(name, arguments, cfg):
            return block(reason)

    # Test runner safety
    if reason := _guard_test_runner(name, arguments, cfg, lane, allowed_roots):
        return block(reason)

    return None
