"""Sandbox — tool dispatch safety for MO.

This is the ONLY gate between the model's tool choice and actual execution.
Single source of truth for: path allowlisting, shell safety, network policy,
secret redaction, lane enforcement.

No tool profiles. No protocol routing. Just: should this tool call execute?
"""

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import os
import re
import shlex
from pathlib import Path
from typing import Any
import traceback

from ..desktop.tool_actions import computer_engine_tool_name
from .tool_constants import (
    ACTUATION_TOOLS,
    CLARIFICATION_BOARD_ACTIONS,
    DESIGN_ARTIFACT_ACTIONS,
    DESIGN_ARTIFACT_TOOL,
    DESIGN_ONLY_LANE,
    MUTATING_TOOLS,
    READ_ONLY_LANES,
    SECRET_READ_BASENAMES,
    SECRET_READ_DIR_NAMES,
    SECRET_READ_PATH_SUFFIXES,
    SECRET_READ_PREFIXES,
    SECRET_READ_SUFFIXES,
)
from ..utils.text_safety import SECRET_NAME_PATTERN, PROVIDER_TOKEN_PATTERN, contains_hardcoded_secret_literal


def _emit_sandbox_event(event_type: str, payload: dict[str, Any]) -> None:
    try:
        from ..runtime.backend_monitor import get_monitor
        monitor = get_monitor()
        if monitor:
            monitor.emit(event_type, payload)
    except Exception:
        traceback.print_exc()


# ── Hard boundary patterns (from guard_policy) ─────────────────────
# These detect genuinely destructive operations or credential exposure.
# Ordinary publication and service lifecycle commands follow the current
# authorized task and do not require a second approval turn.

HARD_BOUNDARY_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\bforce[-\s]?push\b|\brewrite\s+history\b|\breset\s+--hard\b|\bdelete\s+repo\b|"
        r"\bdrop\s+table\b|\btruncate\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bcredential(?:s)?\b|\bsecret(?:s)?\b|\boauth\b|\bprivate\s+key\b|\bbearer\b", re.IGNORECASE),
    re.compile(r"\b(?:api|access|auth|oauth|bearer)\s+token\b|\btoken\s+(?:rotation|value|secret|credential)\b", re.IGNORECASE),
    re.compile(r"\bwallet\b|\bpayment\b|\bbilling\b|\bdatabase\s+migration\b|\bexternal\s+account\b", re.IGNORECASE),
]

# Windows drive paths are a single slash/backslash after the colon (``C:\x`` or
# ``C:/x``). Do not classify URL schemes such as ``https://`` as a fake ``s:/``
# drive path; shell URL arguments and inline Python URL strings are not local
# filesystem access.
ABSOLUTE_PATH_PATTERN = re.compile(r"([a-z]:(?:\\|/(?!/))[^\s\"'`;|&<>]+|(?<![\w.~-])/[^\s\"'`;|&<>]+)", re.IGNORECASE)

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
_SSH_VALUE_OPTIONS = frozenset({
    "-B", "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J",
    "-L", "-l", "-m", "-O", "-o", "-P", "-p", "-Q", "-R", "-S",
    "-W", "-w",
})
_PYTHON_INLINE_SLASH_LITERAL_RE = re.compile(r"/([A-Za-z][A-Za-z0-9_-]*)(?:[=:{\[][^\s\"'`;|&<>/]*)?")


def _touches_hard_boundary(text: str) -> bool:
    """True if text matches any hard boundary pattern.

    Quoted strings are masked before matching so that
    `grep "credential"` is not blocked by the credential pattern.
    Same masking approach as shell_command_is_mutating.
    """
    masked = _mask_quoted_shell_text(text)
    return shell_command_touches_destructive_boundary(text) or any(
        pattern.search(masked) for pattern in HARD_BOUNDARY_PATTERNS[1:]
    )


def _command_text_for_hard_boundary(command: str) -> str:
    """Return command text with SSH connection metadata removed from boundary scan."""
    remote = _ssh_remote_command_text(command)
    if remote is None:
        return str(command or "")
    wrapped = _remote_shell_script_text(remote)
    if wrapped is not None:
        remote = wrapped
    # Remote filesystem paths are not local sandbox paths. Scan the command
    # words, not path literals, for hard-boundary intent.
    return ABSOLUTE_PATH_PATTERN.sub("", remote)


# ── Read-only git inspection: allow source pathspecs, still block secret-bearing ones ──
# A credential keyword in the NAME of a tracked SOURCE file (e.g. `core/state/secrets.py`) must
# not trip the hard-boundary credential scan — the model can already read that file with
# read_file. But git can also surface tracked SECRET content (e.g. `git show HEAD:.env`),
# so secret-bearing pathspecs stay blocked.
_GIT_READONLY_SUBCMDS = frozenset({
    "diff", "show", "log", "status", "blame", "shortlog",
    "ls-files", "ls-tree", "cat-file", "describe", "whatchanged",
})
_GIT_BOARDLESS_READ_SUBCMDS = _GIT_READONLY_SUBCMDS | frozenset({
    "rev-parse", "rev-list", "merge-base", "ls-remote", "branch", "tag",
    "reflog", "worktree",
})
_GIT_INDEX_NEUTRAL_SUBCMDS = frozenset({"add", "push"})
_GIT_DELIVERY_SUBCMDS = frozenset({"add", "commit", "push"})
_GIT_VALUE_OPTIONS = frozenset({
    "--abbrev", "--author", "--color", "--column", "--contains", "--date",
    "--diff-filter", "--format", "--glob", "--grep", "--max-count", "--merged",
    "--no-contains", "--no-merged", "--points-at", "--since", "--sort",
    "--until",
})
_GIT_NO_VALUE_OPTIONS = frozenset({
    "--all", "--branch", "--branches", "--cached", "--check", "--decorate", "--exit-code",
    "--count", "--full-name", "--is-ancestor", "--list", "--name-only", "--name-status",
    "--no-color", "--no-column", "--no-pager", "--oneline", "--porcelain",
    "--quiet", "--remotes", "--short", "--show-current", "--single-worktree", "--stat",
    "--summary", "--tags", "--verbose", "-a", "-l", "-r", "-s", "-v", "-vv",
})
_GIT_GLOBAL_VALUE_OPTIONS = frozenset({
    "-C", "-c", "--config-env", "--exec-path", "--git-dir",
    "--namespace", "--super-prefix", "--work-tree",
})
_GIT_SAFE_INSPECTION_GLOBALS = frozenset({
    "--literal-pathspecs", "--no-optional-locks", "--no-pager", "-P",
})
_GIT_GLOBAL_NO_VALUE_OPTIONS = _GIT_SAFE_INSPECTION_GLOBALS | frozenset({"--bare", "--paginate", "-p"})
_GIT_FORBIDDEN_OPTIONS = frozenset({
    "--contents", "--exclude-from", "--ext-diff", "--external-diff",
    "--filters", "--output", "--pathspec-from-file", "--stdin-paths",
    "--textconv", "-o",
})
_GIT_REFLOG_READ_ACTIONS = frozenset({"show", "list", "exists"})

# Pathspec shapes that bear ACTUAL secret material — never unmasked, always blockable.
_SECRET_BEARING_PATH = re.compile(
    r"(?:^|[\\/])\.env(?:\.[\w-]+)?$"                       # .env, .env.local
    r"|\.(?:pem|key|p12|pfx|ppk|jks|keystore|asc|gpg)$"     # key / cert material
    r"|(?:^|[\\/])\.(?:ssh|aws|gnupg)(?:[\\/]|$)"           # secret directories
    r"|(?:^|[\\/])(?:credentials?|id_rsa|id_dsa|id_ecdsa|id_ed25519|\.netrc|\.htpasswd)(?:[\\/.]|$)"
    r"|(?:^|[\\/])wallet\.dat$",
    re.IGNORECASE,
)

# Source / text file extensions: a credential keyword in such a NAME is a module/doc
# name, not exposed secret material.
_SOURCE_FILE_EXT = re.compile(
    r"\.(?:py|pyi|js|jsx|mjs|cjs|ts|tsx|go|rs|java|kt|c|h|cc|cpp|hpp|cs|rb|php|swift|"
    r"scala|md|rst|txt|json|ya?ml|toml|ini|cfg|conf|xml|html?|css|scss|less|sql|sh|"
    r"ps1|bat|cmd|lock|gradle|properties)$",
    re.IGNORECASE,
)


def _git_segment_parts(segment: str) -> list[str] | None:
    parts = _split_shell_words_posix(segment)
    if len(parts) < 2:
        return None
    exe = Path(str(parts[0]).strip("'\"")).name.lower()
    if exe.endswith(".exe"):
        exe = exe[:-4]
    if exe != "git":
        return None
    return [str(part).strip("'\"") for part in parts]


def _git_subcommand_index(
    parts: list[str] | None,
    *,
    allowed_subcommands: frozenset[str],
    no_value_globals: frozenset[str],
    allow_value_globals: bool,
) -> int:
    if not parts:
        return -1
    idx = 1
    while idx < len(parts):
        option = parts[idx]
        if not option.startswith("-") or option == "-":
            break
        name = option.split("=", 1)[0]
        if name in no_value_globals:
            idx += 1
            continue
        if not allow_value_globals or name not in _GIT_GLOBAL_VALUE_OPTIONS:
            break
        idx += 1 if "=" in option else 2
    if idx < len(parts) and str(parts[idx]).lower() in allowed_subcommands:
        return idx
    return -1


def _git_secret_token_values(parts: list[str]):
    clean_parts = [str(part).strip("'\"") for part in parts]
    for idx, raw in enumerate(clean_parts):
        _name, sep, value = raw.partition("=")
        if raw.startswith("--") and sep:
            yield raw, value
        elif raw.startswith("--") and idx + 1 < len(parts):
            yield raw, clean_parts[idx + 1]
        elif not raw.startswith("-"):
            yield raw, raw


def _git_path_for_secret_scan(value: str) -> str:
    path = str(value or "").strip().strip("'\"")
    if path.startswith(":(") and ")" in path:
        path = path.split(")", 1)[1]
    elif ":" in path and not re.match(r"^[a-zA-Z]:[\\/]", path):
        path = path.split(":", 1)[1]
        if path.startswith(":(") and ")" in path:
            path = path.split(")", 1)[1]
    return path


def _git_value_has_secret_path(value: str) -> bool:
    for candidate in {str(value or ""), str(value or "").rsplit("=", 1)[-1]}:
        if _SECRET_BEARING_PATH.search(_git_path_for_secret_scan(candidate)):
            return True
    return False


def _git_segment_options_are_exact(args: list[str], *, numeric_count: bool = False) -> bool:
    idx = 0
    while idx < len(args):
        raw = str(args[idx]).strip("'\"")
        name, sep, _value = raw.partition("=")
        if not raw or raw == "--":
            return True
        if not raw.startswith("-"):
            idx += 1
            continue
        if re.fullmatch(r"-\d+", raw) and numeric_count:
            idx += 1
            continue
        if name in _GIT_FORBIDDEN_OPTIONS:
            return False
        if name in _GIT_VALUE_OPTIONS:
            if not sep and idx + 1 >= len(args):
                return False
            idx += 1 if sep else 2
            continue
        if name in _GIT_NO_VALUE_OPTIONS:
            idx += 1
            continue
        return False
    return True


def _git_list_segment_is_inspection(subcommand: str, args: list[str]) -> bool:
    if not _git_segment_options_are_exact(args, numeric_count=subcommand == "reflog"):
        return False
    positionals = [arg for arg in args if arg != "--" and not str(arg).startswith("-")]
    if subcommand == "branch":
        return not positionals or any(arg in args for arg in ("--list", "--all", "--remotes", "-a", "-r"))
    if subcommand == "tag":
        return not positionals or any(arg in args for arg in ("--list", "-l"))
    if subcommand == "reflog":
        if not positionals:
            return True
        if positionals[0] not in _GIT_REFLOG_READ_ACTIONS:
            return False
        return len(positionals) <= 2
    if subcommand == "worktree":
        return positionals == ["list"]
    return True


def _git_segment_is_inspection_only(segment: str) -> bool:
    parts = _git_segment_parts(segment)
    idx = _git_subcommand_index(
        parts,
        allowed_subcommands=_GIT_BOARDLESS_READ_SUBCMDS,
        no_value_globals=_GIT_SAFE_INSPECTION_GLOBALS,
        allow_value_globals=False,
    )
    if parts is None or idx < 0:
        return False
    subcommand = str(parts[idx]).lower()
    args = parts[idx + 1:]
    if subcommand in {"branch", "tag", "reflog", "worktree"}:
        return _git_list_segment_is_inspection(subcommand, args)
    return _git_segment_options_are_exact(
        args,
        numeric_count=subcommand in {"log", "shortlog"},
    )


def _git_pathspec_targets(parts: list[str], start_idx: int = 2):
    """Path-looking, non-option args of a git read-only segment (the `rev:path` form is
    reduced to its path). Bare refs without a separator/extension are ignored."""
    for raw in parts[start_idx:]:
        w = str(raw).strip().strip("'\"")
        if not w or w == "--" or w.startswith("-"):
            continue
        path = _git_path_for_secret_scan(w)
        if "/" in path or "\\" in path or "." in Path(path).name:
            yield w, path


def _read_only_git_secret_path_reason(command: str) -> str | None:
    """Block reason when a read-only git inspection targets a secret-bearing path
    (.env/.pem/.key/.ssh/credentials/…); else None."""
    for segment in _top_level_shell_segments(command):
        parts = _git_segment_parts(segment)
        if parts is None:
            continue
        for raw, path in _git_secret_token_values(parts[1:]):
            if _git_value_has_secret_path(path):
                return ("[HARD BOUNDARY] git command targets a secret-bearing path "
                        f"({raw}). Secret files are not inspectable via shell; this stays blocked.")
    return None


def _mask_git_readonly_source_pathspecs(command: str) -> str:
    """Replace SOURCE-file pathspecs of read-only git inspection commands with a neutral
    token for the hard-boundary scan, so a credential keyword in a tracked source FILENAME
    (e.g. `core/state/secrets.py`) is not blocked. Secret-bearing pathspecs are never masked."""
    masked = str(command or "")
    for segment in _top_level_shell_segments(command):
        parts = _git_segment_parts(segment)
        idx = _git_subcommand_index(
            parts,
            allowed_subcommands=_GIT_READONLY_SUBCMDS,
            no_value_globals=_GIT_GLOBAL_NO_VALUE_OPTIONS,
            allow_value_globals=True,
        )
        if parts is None or idx < 0:
            continue
        for raw, path in _git_pathspec_targets(parts, idx + 1):
            if not _SECRET_BEARING_PATH.search(path) and _SOURCE_FILE_EXT.search(path):
                masked = masked.replace(raw, "_srcpath_")
    return masked


def _native_media_arguments(command: str) -> list[str] | None:
    """Recognize the literal native add-media form, without shell evaluation."""
    if re.search(r"[$`%!;&|<>\r\n]", command):
        return None
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return None
    words = [part.strip("'\"") for part in parts]
    if (
        len(words) != 9
        or words[0].lower() not in {"mo", "mo.exe"}
        or words[1:3] != ["--explainer", "add-media"]
        or set(words[5::2]) != {"--id", "--origin"}
        or any(not word or word.startswith("-") for word in words[3:5] + words[6::2])
    ):
        return None
    return parts


def _mask_native_media_asset_ids(command: str) -> str:
    """An add-media --id is an asset label, not an operation or a file path."""
    masked = command
    for segment in _top_level_shell_segments(command):
        parts = _native_media_arguments(segment)
        if parts is None:
            continue
        index = 6 if parts[5].strip("'\"") == "--id" else 8
        parts[index] = "_asset_id_"
        masked = masked.replace(segment, " ".join(parts))
    return masked


# ── Shell safety patterns ──────────────────────────────────────────

SHELL_MUTATION_PATTERNS = [
    r"\b(rm|del|erase|rmdir|mkdir|touch|mv|move|cp|copy|xcopy|robocopy)\b",
    r"\b(remove-item|set-content|add-content|out-file|new-item|move-item|copy-item|rename-item|clear-content)\b",
    r"\b(git\s+(add|commit|push|reset|checkout|clean|rm|mv|restore\s+--staged))\b",
    r"\b(pip|npm|pnpm|yarn)\s+(install|add|remove|uninstall|update)\b",
    # `adb` reaches a real device outside every Live Control consent switch.
    # Uninstalling or clearing a package this way destroys app data and device
    # pairing exactly like `phone_package_action`, which requires a default-off
    # switch and confirmation, so the shell path must not be the cheap way in.
    r"\badb\b.*\b(install|uninstall|push|pull|pm|shell|reboot|root|remount|emu)\b",
    # A Gradle task that assembles, installs, or wipes state is a build action on
    # the operator's machine, not inspection.
    r"\bgradlew?(?:\.bat)?\b.*\b(assemble|install|clean|publish|bundle|connected|lint|test)",
    r"(^|[^<])>>?\s*[^&\s]",
]

_INSPECTION_SHELL_SEGMENT_RE = re.compile(
    r"^(?:(?:python(?:3(?:\.\d+)*)?|py)\s+(?:--version|-V)\b|"
    r"(?:python\s+-m\s+)?(?:rg|grep|find|findstr|ls|dir|tree|pwd|where|which|"
    r"type|cat|head|tail|stat|wc|sha\d*sum|get-content|get-item|get-childitem|"
    r"get-filehash|select-string|test-path)\b|"
    r"(?:echo|printf|hostname|uptime|uname|whoami|id|date|df|du|free|journalctl|ss|netstat|lsof|ping)\b|"
    r"wmic(?:\.exe)?\s+process\s+get\s+ProcessId\s*$|"
    r"black\s+--(?:check|diff)(?=\s|$)|"
    r"command\s+-v\b|docker(?:\s+compose)?\s+ps\b|"
    r"if\s+(?:not\s+)?exist\b|test\s+-[efd]\b|"
    r"systemctl(?:\s+--user)?\s+(?:status|is-active|is-enabled|show|list-units|list-unit-files)\b|"
    r"service\s+\S+\s+status\b|sc(?:\.exe)?\s+(?:query|qc)\b|"
    r"get-service\b|get-process\b|tasklist\b|ps\b)",
    re.I,
)

_NULL_REDIRECTION_RE = re.compile(
    r"(?i)(?<![\w<])\d*>>?\s*(?:/dev/null|nul)(?=$|\s|[;&|])"
)

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
    prefix, name, value = match.group(1), match.group(2), match.group(3)
    # Quoted value is a secret literal → redact (`api_key = "hunter2"`).
    if value[:1] in "\"'":
        return prefix + "[redacted]"
    # Function call or subscripted type → code, not a secret
    # (`secret = compute_value()`, `key = cfg.get_key()`, `token: Optional[str]`).
    if "(" in value or "[" in value:
        return match.group(0)
    # Attribute references and same-name assignments are code, not literal
    # credentials (`token = response.token`, `token = token`).
    if "." in value and re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", value):
        return match.group(0)
    if value.lower() == name.lower():
        return match.group(0)
    # Bare Python type/keyword used as an annotation (`token: str`).
    if value.rstrip(".,:;)").lower() in _CODE_VALUE_TOKENS:
        return match.group(0)
    return prefix + "[redacted]"


SENSITIVE_VALUE_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+"), r"\1[redacted]"),
    (re.compile(r"(?i)([\"']?authorization[\"']?[ \t]*(?::|=(?!=))[ \t]*[\"']?(?:bearer[ \t]+)?)[^\s\"',}]+"), r"\1[redacted]"),
    # Secret name = value. Only fires on secret-SHAPED values (quoted, or long
    # unquoted high-entropy strings) so it never mangles type annotations or code
    # like `token: str` / `secret = compute()` — which previously corrupted code
    # reads in session/compaction and made MO chase phantom `[redacted]` findings.
    (re.compile(
        r"(?i)(?<![A-Za-z0-9_])([\"']?((?:" + SECRET_NAME_PATTERN
        + r"))(?![A-Za-z0-9_])[\"']?[ \t]*(?::|=(?!=))[ \t]*)([^\s,;}]+)"
    ), _redact_named_secret_value),
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


def _mask_exact_git_pretty_placeholders(command: str) -> str:
    """Mask the two bounded pretty placeholders used by MO's Git summaries.

    On ``cmd.exe``, two Git ``%x`` placeholders can resemble one ``%VAR%``
    expansion to the generic control scanner. Only mask ``%h`` and ``%s``
    inside a segment that the exact Git read-only grammar already accepted;
    ordinary environment expansions remain visible and fail closed.
    """
    masked = str(command or "")
    for segment in _top_level_shell_segments(masked):
        if not _git_segment_is_inspection_only(segment):
            continue
        replacement = re.sub(r"%(?:h|s)(?![A-Za-z0-9_])", "_gitfmt_", segment)
        masked = masked.replace(segment, replacement, 1)
    return masked


def _shell_has_ambiguous_controls(command: str) -> bool:
    """Reject unresolved cmd expansion and controls hidden by POSIX quotes."""
    if os.name != "nt":
        return False
    value = _mask_exact_git_pretty_placeholders(command)
    ambiguous = bool(re.search(r"%[^%\r\n]+%|![^!\r\n]+!", value))
    quote = ""
    for char in value:
        if quote == "'" and char in ";&|<>\r\n":
            ambiguous = True
            break
        if char == quote:
            quote = ""
        elif not quote and char in {"'", '"'}:
            quote = char
    if not ambiguous:
        return False
    from tools.shell import _configured_shell
    return Path(_configured_shell()).name.lower() not in {
        "pwsh", "pwsh.exe", "powershell", "powershell.exe",
    }


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

    Sandbox self-tests often use inline `python -c` snippets that pass
    outside-root paths as strings into `guard_tool_call`. Those strings are test
    data, not shell path arguments, so remove only safe self-test code from the
    path scanner. General Python snippets are still scanned.
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


def _python_inline_code_raw_span(command: str) -> tuple[int, int] | None:
    text = str(command or "")
    match = re.search(r"(?<!\S)-c(?:\s+)", text)
    if not match:
        return None
    idx = match.end()
    while idx < len(text) and text[idx].isspace():
        idx += 1
    if idx >= len(text):
        return None
    quote = text[idx]
    if quote in {"'", '"'}:
        start = idx + 1
        idx = start
        while idx < len(text):
            if quote == '"' and text[idx] == "\\":
                idx += 2
                continue
            if text[idx] == quote:
                return (start, idx)
            idx += 1
        return (start, len(text))
    start = idx
    while idx < len(text) and not text[idx].isspace() and text[idx] not in "`;|&<>":
        idx += 1
    return (start, idx)


def _python_code_has_open_string_at(code: str, pos: int) -> bool:
    in_quote: str | None = None
    triple = False
    idx = 0
    while idx < min(pos, len(code)):
        ch = code[idx]
        if in_quote:
            if ch == "\\":
                idx += 2
                continue
            if triple and code.startswith(in_quote * 3, idx):
                in_quote = None
                triple = False
                idx += 3
                continue
            if not triple and ch == in_quote:
                in_quote = None
                idx += 1
                continue
            idx += 1
            continue
        if ch in {"'", '"'}:
            in_quote = ch
            triple = code.startswith(ch * 3, idx)
            idx += 3 if triple else 1
            continue
        idx += 1
    return in_quote is not None


def _is_inside_python_inline_string(command: str, start: int, end: int) -> bool:
    span = _python_inline_code_raw_span(command)
    if not span:
        return False
    code_start, code_end = span
    if start < code_start or end > code_end:
        return False
    return _python_code_has_open_string_at(str(command or "")[code_start:code_end], start - code_start)


def _is_quoted_python_slash_literal(command: str, start: int, end: int, candidate: str) -> bool:
    """Return True for inline Python slash-command literals, not paths."""
    if not _is_python_inline_command(command):
        return False
    if not _is_inside_python_inline_string(command, start, end):
        return False
    name_match = re.match(r"/([A-Za-z][A-Za-z0-9_-]*)", candidate or "")
    if name_match and name_match.group(1).lower() in _UNIX_ROOT_PATH_NAMES:
        return False
    if "/" in str(candidate or "")[1:]:
        return False
    if _PYTHON_INLINE_SLASH_LITERAL_RE.fullmatch(candidate or ""):
        return True
    return re.match(r"/[\{\[]", candidate or "") is not None


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
    parsed = _ssh_parts_and_destination(command)
    if parsed is None:
        return str(command or "")
    parts, idx = parsed
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


def _ssh_parts_and_destination(command: str) -> tuple[list[str], int] | None:
    """Return parsed SSH words and its destination index, preserving option case."""
    parts = _split_shell_words_posix(command)
    if not parts:
        return None
    executable = Path(str(parts[0]).strip("'\"")).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable != "ssh":
        return None

    idx = 1
    while idx < len(parts):
        part = str(parts[idx]).strip("'\"")
        if part == "--":
            idx += 1
            break
        if not part.startswith("-") or part == "-":
            break
        idx += 2 if part in _SSH_VALUE_OPTIONS else 1
    return parts, idx


def _ssh_remote_command_text(command: str) -> str | None:
    """Return an SSH command's remote command, or ``None`` when not SSH.

    ``shlex`` keeps a quoted compound remote command as one argument, so its
    internal pipes/semicolons can be classified recursively without confusing
    them with local shell separators. Missing remote commands fail closed.
    """
    parsed = _ssh_parts_and_destination(command)
    if parsed is None:
        return None
    parts, idx = parsed
    if idx >= len(parts):
        return ""
    # Skip the destination. Everything after it is the remote command.
    return " ".join(str(part) for part in parts[idx + 1:]).strip()


def _remote_shell_script_text(remote: str) -> str | None:
    """Return a single script passed through an SSH-side shell wrapper.

    SSH already executes its remote command through the remote account's shell.
    Agents commonly add ``sudo -u USER bash -lc SCRIPT`` to select the service
    account and load its login environment.  Treating that wrapper itself as a
    local shell escape is a false positive; the contained script remains subject
    to the normal escape and hard-boundary scans.
    """
    parts = _split_shell_words_posix(remote)
    if not parts:
        return None
    idx = 0
    executable = Path(str(parts[idx]).strip("'\"")).name.lower()
    if executable == "sudo":
        idx += 1
        value_options = {"-C", "-D", "-g", "-h", "-p", "-R", "-T", "-u"}
        while idx < len(parts) and str(parts[idx]).startswith("-"):
            option = str(parts[idx]).split("=", 1)[0]
            idx += 1
            if option in value_options and "=" not in str(parts[idx - 1]):
                idx += 1
        if idx >= len(parts):
            return None
        executable = Path(str(parts[idx]).strip("'\"")).name.lower()
    if executable not in {"bash", "sh"} or idx + 2 >= len(parts):
        return None
    flags = str(parts[idx + 1]).strip("'\"")
    if not flags.startswith("-") or "c" not in flags[1:] or len(parts) != idx + 3:
        return None
    return str(parts[idx + 2])


def _top_level_shell_segments(command: str) -> list[str]:
    """Split shell control operators while retaining quoted remote commands."""
    value = str(command or "")
    segments: list[str] = []
    start = 0
    quote = ""
    escaped = False
    for idx, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in ";&|\r\n":
            segment = value[start:idx].strip()
            if segment:
                segments.append(segment)
            start = idx + 1
    tail = value[start:].strip()
    if tail:
        segments.append(tail)
    return segments


def _git_command_touches_destructive_boundary(command: str) -> bool:
    parts = _split_shell_words_posix(command)
    if len(parts) < 2:
        return False
    executable = Path(str(parts[0]).strip("'\"")).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable != "git":
        return False

    idx = 1
    while idx < len(parts):
        option = str(parts[idx]).strip("'\"")
        if not option.startswith("-") or option == "-":
            break
        name = option.split("=", 1)[0]
        if name in _GIT_GLOBAL_NO_VALUE_OPTIONS:
            idx += 1
        elif name in _GIT_GLOBAL_VALUE_OPTIONS and "=" not in option:
            idx += 2
        else:
            idx += 1
    if idx >= len(parts):
        return False

    subcommand = str(parts[idx]).strip("'\"").lower()
    args = [str(part).strip("'\"") for part in parts[idx + 1:]]
    lowered_args = [part.lower() for part in args]
    if subcommand == "clean":
        return not any(
            part == "--dry-run"
            or (part.startswith("-") and not part.startswith("--") and "n" in part[1:])
            for part in lowered_args
        )
    if subcommand == "branch":
        return any(
            part == "--delete"
            or part.startswith("--delete=")
            or (part.startswith("-") and not part.startswith("--") and "d" in part[1:])
            for part in lowered_args
        )
    if subcommand != "push":
        return False
    for part in lowered_args:
        if (
            part in {"-d", "-f", "--delete", "--force", "--mirror", "--prune"}
            or part.startswith("--force-with-lease")
            or (part.startswith("-") and not part.startswith("--") and "f" in part[1:])
            or part.startswith("+")
            or (part.startswith(":") and len(part) > 1)
        ):
            return True
    return False


def shell_command_touches_destructive_boundary(command: str) -> bool:
    """Return whether a shell command crosses the retained destructive boundary."""
    value = str(command or "").strip()
    if not value:
        return False
    if HARD_BOUNDARY_PATTERNS[0].search(_mask_quoted_shell_text(value)):
        return True
    for segment in _top_level_shell_segments(value):
        remote = _ssh_remote_command_text(segment)
        if remote is not None:
            if remote and shell_command_touches_destructive_boundary(remote):
                return True
            continue
        if _git_command_touches_destructive_boundary(segment):
            return True
    return False


def _is_safe_sandbox_inline_self_test(code: str) -> bool:
    text = str(code or "")
    if "core.tooling.sandbox" not in text:
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
    # Suppressing output is inspection, not filesystem mutation.  This matters
    # when an SSH command is classified recursively: the quoted remote command
    # becomes plain text on the second pass, and ordinary ``2>/dev/null`` health
    # checks must not be mistaken for writes.
    lowered = _NULL_REDIRECTION_RE.sub("", lowered)
    return any(re.search(pattern, lowered) for pattern in SHELL_MUTATION_PATTERNS)


def shell_command_is_inspection_only(command: str, *, _remote: bool = False) -> bool:
    """Return whether every shell segment is a recognized read-only inspection."""
    value = str(command or "").strip()
    if not value or shell_command_is_mutating(value):
        return False
    # This classifier is a dispatch boundary. Fail closed on nested command
    # evaluation and do not trust only the first prefix of a compound command.
    if re.search(r"`|\$\(|[<>]\(", value):
        return False
    if not _remote and _shell_has_ambiguous_controls(value):
        return False
    remote = _ssh_remote_command_text(value)
    if remote is not None:
        # Remote nesting creates an ambiguous second network boundary. The
        # operator can still run it with explicit approval, but it is not a
        # boardless/read-only inspection primitive.
        if not remote or _ssh_remote_command_text(remote) is not None:
            return False
        return shell_command_is_inspection_only(remote, _remote=True)
    segments = _top_level_shell_segments(value)
    if not segments:
        return False
    for segment in segments:
        if _git_segment_parts(segment) is not None:
            if not _git_segment_is_inspection_only(segment):
                return False
            continue
        if not _INSPECTION_SHELL_SEGMENT_RE.match(segment):
            return False
        if re.match(r"(?i)^find\b", segment) and re.search(
            r"(?i)(?:^|\s)-(?:delete|exec|execdir|ok|okdir)\b",
            segment,
        ):
            return False
    return True


def _shell_uses_only_inspection_or_git(
    command: str,
    allowed_subcommands: frozenset[str],
    *,
    require_git: bool,
) -> bool:
    segments = _top_level_shell_segments(str(command or "").strip())
    if not segments:
        return False
    saw_git = False
    for segment in segments:
        if shell_command_is_inspection_only(segment):
            continue
        parts = _git_segment_parts(segment)
        index = _git_subcommand_index(
            parts,
            allowed_subcommands=allowed_subcommands,
            no_value_globals=_GIT_GLOBAL_NO_VALUE_OPTIONS,
            allow_value_globals=True,
        )
        if parts is None or index < 0:
            return False
        saw_git = True
    return saw_git or not require_git


def shell_command_changes_indexed_project_state(command: str) -> bool:
    """Return whether a successful shell command may change source or local HEAD."""
    if not str(command or "").strip():
        return False
    return not _shell_uses_only_inspection_or_git(
        command,
        _GIT_INDEX_NEUTRAL_SUBCMDS,
        require_git=False,
    )


def shell_command_is_git_delivery_only(command: str) -> bool:
    """Return whether every non-inspection segment is Git delivery metadata.

    ``add``, ``commit``, and ``push`` normally leave working-file bytes alone,
    but commit/push hooks are allowed to edit them.  This classifier therefore
    does not claim that the verification candidate is unchanged; it only marks
    commands for a cheap before/after candidate snapshot by the turn owner.
    """
    return _shell_uses_only_inspection_or_git(
        command,
        _GIT_DELIVERY_SUBCMDS,
        require_git=True,
    )


def shell_command_escapes(command: str) -> bool:
    value = str(command or "")
    parsed = _ssh_parts_and_destination(value)
    if parsed is not None:
        parts, destination_idx = parsed
        local_text = " ".join(str(part) for part in parts[: destination_idx + 1])
        if any(re.search(pattern, local_text) for pattern in SHELL_ESCAPE_PATTERNS):
            return True
        remote = _ssh_remote_command_text(value) or ""
        wrapped = _remote_shell_script_text(remote)
        return shell_command_escapes(wrapped if wrapped is not None else remote)
    text = _escape_scan_command_text(value)
    return any(re.search(pattern, text) for pattern in SHELL_ESCAPE_PATTERNS)


def shell_command_uses_network(command: str) -> bool:
    return any(re.search(pattern, command or "") for pattern in SHELL_NETWORK_PATTERNS)


def shell_paths_allowed(command: str, allowed_roots: list[str] | None) -> bool:
    if not allowed_roots:
        return True
    raw_command = _path_scan_command_text(command or "")
    # Variable expansion into a path escapes the static scope check; block it
    # when roots are restricted unless it is a current-dir variable. Tilde paths
    # are deterministic enough to expand and scope-check, which lets profile
    # tools run from private home paths without widening access.
    for m in _SHELL_VAR_PATH_PATTERN.finditer(raw_command):
        if m.group(0).startswith("~"):
            end = m.start()
            while end < len(raw_command) and raw_command[end] not in " \t\r\n\"'`;|&<>":
                end += 1
            if path_allowed(raw_command[m.start():end], allowed_roots):
                continue
            return False
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
        if candidate.startswith("/") and candidate != "/dev/null" and not candidate.startswith("//"):
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
    # SYSTEMDRIVE/PROGRAMDATA prevent literal-dir litter; shutdown.exe returns
    # error 203 when the non-secret COMPUTERNAME variable is absent.
    "SYSTEMDRIVE", "PROGRAMDATA", "PUBLIC", "ALLUSERSPROFILE", "COMPUTERNAME",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONUTF8",
    # Non-secret MO routing context. Shell/test subprocesses need these to
    # preserve isolated worktree roots and private-state location while still
    # excluding provider keys, tokens, and credentials.
    "MO_PRODUCT_ROOT", "MO_PROJECT_CWD", "MO_DEFAULT_ROOTS",
    "MO_TOOL_ROOT_REMAP_FROM", "MO_TOOL_ROOT_REMAP_TO",
    "MO_DEVMODE_AUTOPILOT_ISOLATED",
    "MO_LOCAL_EXTENSION_ROOT", "MO_STATE_HOME", "MO_INSTANCE_ID",
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
    "test_runner": ("command",),
    "grep": ("pattern",),
    "web_fetch": ("url",),
    "web_search": ("query",),
    "desktop_find": ("query",),
    "desktop_inspect": ("target",),
    "desktop_wait": ("query",),
    "desktop_invoke": ("target",),
    "desktop_window": ("target",),
    "desktop_recipe_run": ("recipe",),
    "phone_click": ("target",),
    "phone_set_text": ("target", "text"),
    "phone_key": ("action",),
    "phone_file_read": ("path",),
    "phone_file_delete": ("path",),
    "phone_cache_trim": ("bytes_to_free",),
    "phone_package_action": ("action", "package"),
    "phone_shell": ("command",),
}

_NONBLANK_TOOL_ARGS: dict[str, tuple[str, ...]] = {
    "read_file": ("path",),
    "write_file": ("path",),
    "edit_file": ("path", "old_text"),
    "shell": ("command",),
    "test_runner": ("command",),
    "grep": ("pattern",),
    "web_fetch": ("url",),
    "web_search": ("query",),
    "desktop_find": ("query",),
    "desktop_inspect": ("target",),
    "desktop_wait": ("query",),
    "desktop_invoke": ("target",),
    "desktop_window": ("target",),
    "desktop_recipe_run": ("recipe",),
    "phone_click": ("target",),
    "phone_set_text": ("target", "text"),
    "phone_key": ("action",),
    "phone_file_read": ("path",),
    "phone_file_delete": ("path",),
    "phone_package_action": ("action", "package"),
    "phone_shell": ("command",),
}

def _normalize_tool_argument_aliases(name: str, arguments: dict[str, Any]) -> None:
    """Normalize common model aliases before required-argument validation."""
    if not isinstance(arguments, dict):
        return
    if name in {"desktop_inspect", "desktop_invoke", "desktop_window"} and not arguments.get("target"):
        for alias in ("ref", "element_id"):
            if arguments.get(alias):
                arguments["target"] = arguments[alias]
                break
    if name == "press_key" and not arguments.get("keys") and arguments.get("key"):
        arguments["keys"] = arguments["key"]

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
    r"(?:^|_)(?i:write|edit|create|delete|remove|move|rename|patch|apply|update|build|commit|push|"
    r"merge|deploy|run|overwrite|truncate|drop|clear|unlink|mkdir|set|put|append|insert|upload|"
    r"place|cancel|flatten|execute|trade|buy|sell|swap)"
    r"(?:_|$|(?=[A-Z]))",
)


def mcp_tool_name_is_mutating(name: str) -> bool:
    """Return whether an MCP tool name carries the shared mutation signal."""
    return bool(_MCP_MUTATING_NAME_PATTERN.search(str(name or "")))


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


def _validate_tool_arguments(
    name: str,
    arguments: dict[str, Any],
    *,
    display_name: str | None = None,
) -> str | None:
    args = arguments or {}
    _normalize_tool_argument_aliases(name, args)
    label = str(display_name or name)
    for key in _REQUIRED_TOOL_ARGS.get(name, ()):
        if key not in args:
            return f"[TOOL BLOCKED] {label} missing required argument: {key}."
        if key in _NONBLANK_TOOL_ARGS.get(name, ()) and not str(args.get(key) or "").strip():
            return f"[TOOL BLOCKED] {label} blank required argument: {key}."
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
    if name == "media":
        if str(arguments.get("action") or "catalog") not in {"create", "wait", "credits"}:
            return None
        name, arguments = "web_fetch", {"url": "https://api.kie.ai"}
    if name == "computer_act" and str(arguments.get("action") or "").strip().lower() == "open":
        name = "browser_open"
    if name not in {"web_fetch", "web_search", "browser_open", "inspect_repo", "use_repo"}:
        return None
    if cfg.get("enabled") and not cfg.get("web_fetch_enabled", True):
        return f"[SANDBOX BLOCKED] {name} network access disabled."
    # Model-driven navigation must not bypass path scope with file:/data: URLs.
    if name == "browser_open" and cfg.get("enabled"):
        from urllib.parse import urlparse
        raw_url = str(arguments.get("url", "") or "")
        scheme = (urlparse(raw_url).scheme or "").lower()
        if scheme in {"file", "data"}:
            return f"[SANDBOX BLOCKED] {name} does not allow {scheme}: URLs."
    if cfg.get("enabled") and cfg.get("web_fetch_allowed_hosts"):
        from urllib.parse import urlparse
        if name == "web_search":
            host = "api.duckduckgo.com"
        else:
            host = (urlparse(str(arguments.get("url", ""))).hostname or "").lower()
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
    # git can surface tracked SECRET content (e.g. `git show HEAD:.env`), so read-only
    # git inspection of a secret-bearing path stays blocked even though it is git.
    if not operator_override:
        if reason := _read_only_git_secret_path_reason(command):
            return reason
    boundary_text = _command_text_for_hard_boundary(command)
    # ...but a credential keyword in a tracked SOURCE pathspec NAME (e.g.
    # `git diff core/state/secrets.py`) must not false-positive the hard-boundary scan.
    boundary_text = _mask_git_readonly_source_pathspecs(boundary_text)
    boundary_text = _mask_native_media_asset_ids(boundary_text)
    if not operator_override and _touches_hard_boundary(boundary_text):
        return (
            "[HARD BOUNDARY] shell command touches credentials or a destructive "
            "operation that the current request does not explicitly authorize."
        )
    if cfg.get("block_shell_escape", True) and shell_command_escapes(command):
        return "[SANDBOX BLOCKED] shell escape/path traversal blocked."
    if cfg.get("enabled") and not cfg.get("shell_network_enabled", True) and shell_command_uses_network(command):
        return "[SANDBOX BLOCKED] shell network command blocked."
    if lane in READ_ONLY_LANES and not shell_command_is_inspection_only(command):
        return f"[LANE LOCKED] shell command is not a read-only inspection in {lane} lane."
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
    command = str(arguments.get("command", ""))
    if lane in READ_ONLY_LANES and shell_command_is_mutating(command):
        return f"[LANE LOCKED] test_runner mutation blocked in {lane} lane."
    if cfg.get("block_shell_escape", True) and shell_command_escapes(command):
        return "[SANDBOX BLOCKED] shell escape/path traversal blocked."
    if cfg.get("enabled") and not cfg.get("shell_network_enabled", True) and shell_command_uses_network(command):
        return "[SANDBOX BLOCKED] shell network command blocked."
    if not shell_paths_allowed(command, allowed_roots):
        return "[PATH BLOCKED] test_runner command references a path outside allowed roots."
    return None


def _readable_profile_reference(path: str | None) -> bool:
    """Allow reads of MO's own profile and skill sources, never all private state."""
    if not path:
        return False
    try:
        from ..state.paths import mo_home
        from ..skills import skills_root

        roots = [str(mo_home({}) / "memory" / "profile"), str(skills_root())]
        return path_allowed(str(path), roots)
    except Exception:
        return False


def _secret_read_path_kind(path: str | None) -> str | None:
    """Return a secret-path label for raw file reads that must use the broker."""
    if not path:
        return None
    try:
        target = Path(str(path)).expanduser()
    except Exception:
        target = Path(str(path))
    parts = tuple(part.lower() for part in target.parts if part not in {"", ".", ".."})
    name = target.name.lower()
    suffix = target.suffix.lower()
    # Checked-in templates are documentation, not credential stores.
    if name.endswith((".env.example", ".env.sample", ".env.template")):
        return None
    if name in SECRET_READ_BASENAMES:
        return name
    if any(name.startswith(prefix) for prefix in SECRET_READ_PREFIXES):
        return "secret filename"
    if suffix in SECRET_READ_SUFFIXES:
        return suffix
    if any(part in SECRET_READ_DIR_NAMES for part in parts):
        return "secret directory"
    for secret_suffix in SECRET_READ_PATH_SUFFIXES:
        if len(parts) >= len(secret_suffix) and parts[-len(secret_suffix):] == secret_suffix:
            return "/".join(secret_suffix)
    return None


_SHELL_READ_PATH_COMMANDS = frozenset({"type", "cat", "gc", "get-content", "more", "head", "tail", "less"})
_SHELL_SEARCH_PATH_COMMANDS = frozenset({"grep", "rg", "ripgrep", "findstr", "select-string", "sls"})
_SHELL_PATH_OPTION_NAMES = frozenset({"-path", "-literalpath", "--path"})


def _shell_command_secret_read_candidates(command: str, workdir: str | None = None) -> list[str]:
    """Extract likely file-read targets from shell commands without treating grep patterns as paths."""
    candidates: list[str] = []
    base_dir = Path(workdir).expanduser() if workdir else Path.cwd()
    for segment in re.split(r"[|;&]", _path_scan_command_text(command or "")):
        parts = _split_shell_words(segment)
        if not parts:
            continue
        media_parts = _native_media_arguments(segment)
        if media_parts is not None:
            for raw in media_parts[3:5]:
                path = Path(raw.strip("'\"")).expanduser()
                candidates.append(str(path if path.is_absolute() else base_dir / path))
            continue
        executable = Path(str(parts[0]).strip("'\"")).name.lower()
        if executable.endswith(".exe"):
            executable = executable[:-4]
        if executable in {"cmd", "powershell", "pwsh"}:
            flags = {"/c", "-c", "-command"}
            for index, token in enumerate(parts[1:], start=1):
                if str(token).strip("'\"").lower() in flags and index + 1 < len(parts):
                    nested = " ".join(str(item) for item in parts[index + 1 :])
                    candidates.extend(_shell_command_secret_read_candidates(nested, str(base_dir)))
                    break
            continue
        if executable not in _SHELL_READ_PATH_COMMANDS and executable not in _SHELL_SEARCH_PATH_COMMANDS:
            continue
        path_candidates: list[str] = []
        saw_search_pattern = False
        idx = 1
        while idx < len(parts):
            raw = str(parts[idx]).strip().strip("'\"")
            lower = raw.lower()
            if not raw:
                idx += 1
                continue
            if lower in _SHELL_PATH_OPTION_NAMES and idx + 1 < len(parts):
                path_candidates.append(str(parts[idx + 1]).strip().strip("'\""))
                idx += 2
                continue
            if raw.startswith("-") or (executable in {"findstr"} and raw.startswith("/")):
                idx += 1
                continue
            if executable in _SHELL_SEARCH_PATH_COMMANDS:
                if not saw_search_pattern:
                    saw_search_pattern = True
                    idx += 1
                    continue
                path_candidates.append(raw)
            else:
                path_candidates.append(raw)
            idx += 1
        for candidate in path_candidates:
            path = Path(candidate).expanduser()
            candidates.append(str(path if path.is_absolute() else base_dir / path))
    return candidates


def secret_read_path_kind(path: str | Path | None) -> str | None:
    """Public value-free predicate used by recursive read tools."""
    return _secret_read_path_kind(str(path) if path is not None else None)


def _guard_path_scope(
    name: str,
    arguments: dict[str, Any],
    allowed_roots: list[str] | None,
) -> str | None:
    """Check file/path scope for find, grep, git, project_bridge tools.
    Also handles shell workdir path check.
    Return block reason or None."""
    if name == "media":
        for ref in arguments.get("references") or []:
            if isinstance(ref, dict) and not path_allowed(str(ref.get("path") or ""), allowed_roots):
                return "[PATH BLOCKED] media reference is outside allowed roots."
    # File tools: read_file, write_file, edit_file. read_file may ALSO reach the
    # operator's own profile and skills (read-only); writes stay on allowed_roots.
    if name in {"read_file", "write_file", "edit_file"} and "path" in arguments:
        path = str(arguments["path"])
        readable_profile = name == "read_file" and _readable_profile_reference(path)
        if not readable_profile and not path_allowed(path, allowed_roots):
            return f"[PATH BLOCKED] {name} outside allowed roots: {arguments['path']}"
    if name == "show_image" and arguments.get("path"):
        source = str(arguments["path"])
        if not _readable_profile_reference(source) and not path_allowed(source, allowed_roots):
            return f"[PATH BLOCKED] show_image source outside allowed roots: {source}"
    if name == "edit_image" and arguments.get("path"):
        source = str(arguments["path"])
        output = str(arguments.get("out") or "").strip()
        source_readable = _readable_profile_reference(source) or path_allowed(source, allowed_roots)
        if not source_readable:
            return f"[PATH BLOCKED] edit_image source outside allowed roots: {source}"
        # The default output is written beside the source, so that directory must
        # itself be writable. An explicit output may instead target another
        # allowed root while reading a profile-owned source.
        if not output and not path_allowed(source, allowed_roots):
            return f"[PATH BLOCKED] edit_image default output outside allowed roots: {source}"
        if output and not path_allowed(output, allowed_roots):
            return f"[PATH BLOCKED] edit_image output outside allowed roots: {output}"
    if name == "file_transfer":
        action = str(arguments.get("action") or "list").strip().lower()
        if action == "send" and arguments.get("path"):
            source = str(arguments["path"])
            if not path_allowed(source, allowed_roots):
                return f"[PATH BLOCKED] file_transfer source outside allowed roots: {source}"
        destination = str(arguments.get("destination_path") or "").strip()
        if action == "send" and destination and not Path(destination).expanduser().is_absolute():
            return (
                "[PATH BLOCKED] file_transfer destination_path must be an absolute "
                f"receiver-side path: {destination}"
            )
        if action == "accept" and destination and (
            not Path(destination).expanduser().is_absolute()
            or not path_allowed(destination, allowed_roots)
        ):
            return (
                "[PATH BLOCKED] file_transfer destination_path must be an absolute "
                f"path inside allowed roots: {destination}"
            )
    # perceive reads one local image/PDF read-only, like read_file. Live targets
    # are owned exclusively by computer_observe.
    if name == "perceive":
        src = str(arguments.get("source", "")).strip()
        if src.casefold() == "screen":
            return (
                "[TOOL BLOCKED] perceive accepts local image/PDF paths only; "
                "use computer_observe for live screens."
            )
        if src:
            readable_profile = _readable_profile_reference(src)
            if not readable_profile and not path_allowed(src, allowed_roots):
                return f"[PATH BLOCKED] perceive source outside allowed roots: {src}"
    # Shell workdir
    if name == "shell" and arguments.get("workdir"):
        if not path_allowed(str(arguments["workdir"]), allowed_roots):
            return f"[PATH BLOCKED] shell workdir outside allowed roots: {arguments['workdir']}"
    # Find/grep/git/test_runner/project_bridge/map_project/redundancy_scan root/workdir
    if name in {"find_files", "grep", "git_status", "test_runner", "project_bridge", "map_project", "redundancy_scan"}:
        path_value = arguments.get("root") or arguments.get("workdir")
        if name == "project_bridge":
            path_value = path_value or arguments.get("path")
        readable_profile = name in {"find_files", "grep"} and _readable_profile_reference(str(path_value) if path_value else None)
        if path_value and not readable_profile and not path_allowed(str(path_value), allowed_roots):
            return f"[PATH BLOCKED] {name} path outside allowed roots: {path_value}"
    return None


def _guard_read_secret(name: str, arguments: dict[str, Any], operator_override: bool) -> str | None:
    """Block raw reads of secret-shaped paths; sanctioned helpers handle secrets."""
    del operator_override  # raw values never enter model context, even in full-access mode
    candidates: list[str] = []
    if name == "media":
        candidates.extend(str(ref.get("path") or "") for ref in arguments.get("references") or [] if isinstance(ref, dict))
    if name == "read_file" and arguments.get("path"):
        candidates.append(str(arguments.get("path")))
    elif name in {"find_files", "grep"}:
        root = arguments.get("root")
        if root:
            candidates.append(str(root))
    elif name == "shell":
        candidates.extend(_shell_command_secret_read_candidates(str(arguments.get("command") or ""), str(arguments.get("workdir") or "") or None))
    for candidate in candidates:
        if kind := _secret_read_path_kind(candidate):
            return (
                f"[TOOL BLOCKED] raw read of secret-looking path blocked ({kind}): {candidate}. "
                "Use the credential broker/status helper; raw credential values never enter model context."
            )
    return None


def _guard_mcp_tool(
    name: str,
    arguments: dict[str, Any],
    lane: str | None,
    allowed_roots: list[str] | None,
) -> str | None:
    if not str(name or "").startswith("mcp__"):
        return None
    if lane in READ_ONLY_LANES and mcp_tool_name_is_mutating(name):
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
    if not cfg.get("block_write_secrets", DEFAULT_PREFERENCES["sandbox.block_write_secrets"]):
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
            "private key). Use the authorized credential broker or platform secret store "
            "instead of a literal credential. If this is genuinely intended, the operator "
            "must approve it explicitly."
        )
    return None


def _point_only_desktop_recipe(arguments: dict[str, Any]) -> bool:
    """True only for a bounded recipe whose every step is non-actuating pointing."""
    recipe = arguments.get("recipe")
    if recipe is None and "steps" in arguments:
        recipe = arguments
    if not isinstance(recipe, dict):
        return False
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 40:
        return False
    for step in steps:
        if not isinstance(step, dict):
            return False
        action = str(step.get("action") or step.get("tool") or "").strip()
        if action != "point":
            return False
        args = step.get("args") if isinstance(step.get("args"), dict) else step
        try:
            int(args.get("x"))
            int(args.get("y"))
        except (TypeError, ValueError):
            return False
    return True


def desktop_recipe_is_point_only(arguments: dict[str, Any]) -> bool:
    """Expose the sandbox's presentation-only recipe decision to evidence gates."""
    return _point_only_desktop_recipe(arguments)


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

    operator_override=True skips the remaining credential/destructive boundary
    after the operator explicitly authorizes that exact action.
    """
    cfg = sandbox_config or {}

    def block(reason: str) -> str:
        _emit_sandbox_event("sandbox_blocked", {"tool": name, "lane": lane or "", "reason": reason[:240]})
        return reason

    try:
        guard_name = computer_engine_tool_name(name, arguments)
    except ValueError as exc:
        return block(f"[SANDBOX BLOCKED] invalid computer request: {exc}.")

    _emit_sandbox_event("sandbox_guard", {"tool": name, "lane": lane or "", "enabled": bool(cfg.get("enabled"))})

    # Lane guard: block mutating + actuation tools in read-only lanes. A schedule
    # list is the read-only action of an otherwise mutating lifecycle tool.
    action = str(arguments.get("action") or "").strip().lower()
    design_artifact_action = (
        lane == DESIGN_ONLY_LANE
        and name == DESIGN_ARTIFACT_TOOL
        and action in DESIGN_ARTIFACT_ACTIONS
    )
    clarification_board_action = (
        lane in READ_ONLY_LANES
        and lane != DESIGN_ONLY_LANE
        and name == DESIGN_ARTIFACT_TOOL
        and (
            action in CLARIFICATION_BOARD_ACTIONS
            or (
                action in {"open", "show"}
                and str(arguments.get("view") or "").strip().lower() == "board"
            )
        )
    )
    mutates = name in MUTATING_TOOLS and not (
        (name == "schedule_job" and (action or "list") == "list")
        or (name == "media" and (action or "catalog") in {"catalog", "credits", "list", "status", "cleanup_review"})
        or (name == "file_transfer" and (action or "list") == "list")
        or (name == "migrate" and action in {"inspect", "plan"})
        or (name == "project_history" and action in {"status", "inspect", "trace"})
        or (name == "role_work" and action in {"list", "status", "wait"})
        or (name == "mail" and (action or "status") in {"status", "list", "search", "read_latest", "read"})
        or design_artifact_action
        or clarification_board_action
    )
    safe_point_recipe = guard_name == "desktop_recipe_run" and _point_only_desktop_recipe(arguments)
    if (
        lane in READ_ONLY_LANES
        and (mutates or (name in ACTUATION_TOOLS and not safe_point_recipe))
    ):
        return block(f"[LANE LOCKED] {name} blocked in {lane} lane.")
    # Computer-use: screen-capture privacy kill-switch.
    if guard_name in {"capture_screen", "browser_capture"} and cfg.get("enabled") and not cfg.get("screen_capture_enabled", True):
        return block("[SANDBOX BLOCKED] screen capture disabled (sandbox.screen_capture_enabled=false).")
    argument_error = _validate_tool_arguments(guard_name, arguments, display_name=name)
    if argument_error:
        return block(argument_error)

    # Web/network tools
    if reason := _guard_web_tools(guard_name, arguments, cfg):
        return block(reason)

    # Shell safety
    if reason := _guard_shell_tool(name, arguments, cfg, lane, allowed_roots, operator_override):
        return block(reason)

    # Path scope (file tools, shell workdir, find/grep/git/project_bridge)
    if reason := _guard_path_scope(name, arguments, allowed_roots):
        return block(reason)

    # Read-time secret path guard: content redaction is intentionally conservative,
    # so block raw reads of credential-shaped files before they reach the provider.
    if reason := _guard_read_secret(name, arguments, operator_override):
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
