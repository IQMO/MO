"""MO Agent — Tool implementations and definitions.

All 13 tools defined. Full tool list sent to provider every turn.
Sandbox gates at dispatch time via core.sandbox.guard_tool_call().
"""

import os
import re
import sys
import json
import subprocess
import fnmatch
from pathlib import Path
from typing import Any

from core.sandbox import safe_env, redact_sensitive_text
from core.shell_processes import (
    _register_shell_process,
    _unregister_shell_process,
    _kill_process_tree,
)


SKIP_PATH_PARTS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".venv", "venv", "logs", "memory"}
MAX_GREP_SCANNED_FILES = 2500
MAX_GREP_FILE_BYTES = 1_000_000
MAX_WEB_FETCH_BYTES = 2_000_000


def _skip_path(path: Path, allowed_parts: set[str] | None = None) -> bool:
    for part in path.parts:
        if part in SKIP_PATH_PARTS:
            if allowed_parts and part in allowed_parts:
                continue
            return True
    return False


def _iter_unskipped_files(root_path: Path):
    # Allow explicit roots under skipped trees (user explicitly navigated there)
    root_parts = set(root_path.resolve().parts)
    allowed = root_parts & SKIP_PATH_PARTS
    if root_path.is_file():
        if not _skip_path(root_path, allowed):
            yield root_path
        return
    for dirpath, dirnames, filenames in os.walk(root_path):
        current = Path(dirpath)
        if _skip_path(current, allowed):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_PATH_PARTS or d in allowed]
        for filename in filenames:
            p = current / filename
            if not _skip_path(p, allowed):
                yield p


# ── Tool Definitions ───────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Supports text files. Use offset/limit for large files.",
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to read (relative or absolute)"},
                    "offset": {"type": "integer", "description": "Line number to start reading from (1-indexed)"},
                    "limit": {"type": "integer", "description": "Maximum number of lines to read"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a NEW file or overwrite a SMALL file (<50 lines). For existing files, use edit_file instead — targeted exact-text replacements. Writing an existing file with write_file will be blocked by the sandbox if it exceeds 250 lines.",
            "parameters": {
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to write"},
                    "content": {"type": "string", "description": "Content to write"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit an EXISTING file by exact text replacement. This is the PRIMARY tool for modifying files. old_text must be unique in the file. Split large changes into multiple small edit_file calls (each <=250 lines).",
            "parameters": {
                "type": "object",
                "required": ["path", "old_text", "new_text"],
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to edit"},
                    "old_text": {"type": "string", "description": "Exact text to replace"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Execute a shell command in the configured/ambient system shell. Returns stdout and stderr. Match command syntax to the active environment; use python -c for portable Python snippets.",
            "parameters": {
                "type": "object",
                "required": ["command"],
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"},
                    "workdir": {"type": "string", "description": "Working directory for the command"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find files under a root by substring/glob-like pattern. Returns compact relative paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Case-insensitive substring to match; empty lists files"},
                    "root": {"type": "string", "description": "Directory root (default current working directory)"},
                    "limit": {"type": "integer", "description": "Maximum paths to return (default 200)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search text files under a root and return compact path:line matches.",
            "parameters": {
                "type": "object",
                "required": ["pattern"],
                "properties": {
                    "pattern": {"type": "string", "description": "Regex or literal pattern to search"},
                    "root": {"type": "string", "description": "Directory root or file (default current working directory)"},
                    "file_glob": {"type": "string", "description": "Optional suffix/glob hint like .py or *.md"},
                    "limit": {"type": "integer", "description": "Maximum matches to return (default 200)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Return structured git branch/status for a working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workdir": {"type": "string", "description": "Git working tree directory (default current working directory)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "test_runner",
            "description": "Run the project test command with timeout and exit-code marker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Test command (default python -m pytest -q)"},
                    "workdir": {"type": "string", "description": "Working directory (default current working directory)"},
                    "timeout": {"type": "integer", "description": "Timeout seconds (default 120)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_bridge",
            "description": "Read nearest AGENTS.md or CLAUDE.md for a target path before project edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file/directory path (default cwd)"},
                    "limit": {"type": "integer", "description": "Maximum chars to return (default 4000)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch content from a URL. Returns response body as text.",
            "parameters": {
                "type": "object",
                "required": ["url"],
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "method": {"type": "string", "description": "HTTP method (default GET)"},
                    "headers": {"type": "string", "description": "JSON string of headers"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_snapshot",
            "description": "Low-cost web eyes: fetch a URL and return its main content as compact, readable Markdown (nav/boilerplate stripped, structure preserved).",
            "parameters": {
                "type": "object",
                "required": ["url"],
                "properties": {
                    "url": {"type": "string", "description": "URL to snapshot"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo. Returns top results as title + URL + snippet.",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark the current taskboard task as complete and advance to the next task. Use this explicitly when you have finished all work for the current phase. Optionally specify task_id to confirm which task you are completing; if omitted, the active task is completed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Optional: the ID of the task to complete. If omitted, completes the currently active task.",
                    },
                },
            },
        },
    },
]


# ── Tool Executors ─────────────────────────────────────────────────

def _numbered_lines(lines: list[str], start_line: int = 1) -> str:
    width = len(str(start_line + len(lines) - 1)) if lines else len(str(start_line))
    return "\n".join(f"{index:>{width}}: {line}" for index, line in enumerate(lines, start_line))


def execute_read_file(arguments: dict[str, Any]) -> str:
    path = arguments["path"]
    offset = arguments.get("offset")
    limit = arguments.get("limit")
    p = Path(path)
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"
    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: Cannot read {path} as text (binary file)."
    except Exception as e:
        return f"Error reading {path}: {e}"
    lines = content.splitlines()
    total_lines = len(lines)
    if offset is not None or limit is not None:
        start = (offset or 1) - 1
        end = (start + limit) if limit else total_lines
        sliced = lines[start:end]
        return f"[Lines {start+1}-{min(end, total_lines)} of {total_lines}]\n" + _numbered_lines(sliced, start + 1)
    if total_lines > 2000 or len(content) > 50000:
        return f"[Truncated — showing first 2000 of {total_lines} lines]\n" + _numbered_lines(lines[:2000], 1)
    return f"[Lines 1-{total_lines} of {total_lines}]\n" + _numbered_lines(lines, 1)


def execute_write_file(arguments: dict[str, Any]) -> str:
    path = arguments["path"]
    content = arguments["content"]
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        size = p.stat().st_size
        return f"Wrote {size} bytes to {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


def execute_edit_file(arguments: dict[str, Any]) -> str:
    path = arguments["path"]
    old_text = arguments["old_text"]
    new_text = arguments["new_text"]
    p = Path(path)
    if not p.exists():
        return f"Error: File not found: {path}"
    try:
        content = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading {path}: {e}"
    if old_text not in content:
        return f"Error: old_text not found in {path}"
    if content.count(old_text) > 1:
        return f"Error: old_text is not unique in {path} (found {content.count(old_text)} occurrences)"
    new_content = content.replace(old_text, new_text, 1)
    try:
        p.write_text(new_content, encoding="utf-8")
        return f"Edited {path} — 1 replacement"
    except Exception as e:
        return f"Error writing {path}: {e}"


def _configured_shell() -> str:
    """Return the operator's configured/ambient shell without forcing one globally."""
    explicit = os.environ.get("MO_TOOL_SHELL") or os.environ.get("MO_SHELL")
    if explicit and explicit.strip():
        return explicit.strip()
    if sys.platform == "win32":
        return os.environ.get("COMSPEC") or "cmd.exe"
    return os.environ.get("SHELL") or "/bin/sh"


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
        return command, True, command
    return [shell_exe, "-c", command], False, command


def execute_shell(arguments: dict[str, Any]) -> str:
    command = str(arguments.get("command", "")).strip()
    workdir = arguments.get("workdir") or os.getcwd()
    timeout = arguments.get("timeout", 60)
    cwd = workdir

    shell_cmd, use_shell, registered_command = _shell_command(command)

    try:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        env = safe_env() if bool(arguments.get("_clean_env", True)) else os.environ.copy()
        popen_kwargs: dict[str, Any] = {
            "shell": use_shell,
            "cwd": cwd,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(shell_cmd, **popen_kwargs)
        _register_shell_process(proc, registered_command, cwd, timeout)
        try:
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_process_tree(proc.pid)
                try:
                    proc.communicate(timeout=2)
                except Exception:
                    pass
                return (
                    f"Error: Command timed out after {timeout}s — the process was killed "
                    "before it finished, so no output was produced. If the command "
                    "legitimately needs longer (full test suites often need 300s+), retry "
                    "once with a higher `timeout` argument instead of lowering it or "
                    "shrinking scope blindly."
                )
        finally:
            _unregister_shell_process(proc.pid)

        output = stdout or ""
        if stderr:
            output += "\n[stderr]\n" + stderr
        if not output.strip():
            output = f"[Command completed with exit code {proc.returncode}]"
        elif "exit code" not in output.lower():
            output = output.rstrip() + f"\n[exit code {proc.returncode}]"
        if len(output) > 50000:
            output = output[:50000] + "\n[...truncated...]"
        return output
    except Exception as e:
        return f"Error executing command: {e}"


def execute_find_files(arguments: dict[str, Any]) -> str:
    pattern = str(arguments.get("pattern", ""))
    root = arguments.get("root") or os.getcwd()
    limit = max(1, min(int(arguments.get("limit", 200)), 1000))
    root_path = Path(root)
    if not root_path.exists():
        return f"Error: root not found: {root_path}"
    if root_path.is_file():
        return str(root_path)
    needle = pattern.lower()
    glob_like = any(ch in needle for ch in "*?[]")
    matches: list[str] = []
    for p in _iter_unskipped_files(root_path):
        rel = str(p.relative_to(root_path)).replace("\\", "/")
        rel_low = rel.lower()
        name_low = p.name.lower()
        glob_match = glob_like and (
            fnmatch.fnmatch(rel_low, needle) or fnmatch.fnmatch(name_low, needle)
        )
        if not needle or needle in rel_low or glob_match:
            matches.append(rel)
        if len(matches) >= limit:
            break
    suffix = f"\n[truncated at {limit} matches]" if len(matches) >= limit else ""
    return "\n".join(matches) + suffix if matches else "[no files matched]"


def _glob_hint_matches(path: Path, hint: str) -> bool:
    if not hint:
        return True
    hint = hint.strip()
    if hint.startswith("*."):
        return path.name.endswith(hint[1:])
    if hint.startswith("."):
        return path.suffix == hint
    return hint.lower() in path.name.lower()


def execute_grep(arguments: dict[str, Any]) -> str:
    pattern = arguments["pattern"]
    root = arguments.get("root") or os.getcwd()
    file_glob = str(arguments.get("file_glob", ""))
    limit = max(1, min(int(arguments.get("limit", 200)), 1000))
    root_path = Path(root)
    if not root_path.exists():
        return f"Error: root not found: {root_path}"
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(re.escape(pattern))
    matches: list[str] = []
    scanned = 0
    skipped_large = 0
    for p in _iter_unskipped_files(root_path):
        if _skip_path(p) or not _glob_hint_matches(p, file_glob):
            continue
        scanned += 1
        if scanned > MAX_GREP_SCANNED_FILES:
            suffix = f"\n[grep scan capped after {MAX_GREP_SCANNED_FILES} files]"
            return ("\n".join(matches) + suffix) if matches else "[no matches]" + suffix
        try:
            if p.stat().st_size > MAX_GREP_FILE_BYTES:
                skipped_large += 1
                continue
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        base = root_path if root_path.is_dir() else root_path.parent
        rel = str(p.relative_to(base)).replace("\\", "/")
        for idx, line in enumerate(lines, start=1):
            if rx.search(line):
                snippet = line.strip()
                if len(snippet) > 220:
                    snippet = snippet[:219] + "…"
                matches.append(f"{rel}:{idx}: {snippet}")
                if len(matches) >= limit:
                    suffix = f"\n[truncated at {limit} matches]"
                    if skipped_large:
                        suffix += f"\n[skipped {skipped_large} large files]"
                    return "\n".join(matches) + suffix
    suffix = f"\n[skipped {skipped_large} large files]" if skipped_large else ""
    return ("\n".join(matches) if matches else "[no matches]") + suffix


def execute_git_status(arguments: dict[str, Any]) -> str:
    cwd = str(Path(arguments.get("workdir") or os.getcwd()))
    try:
        proc = subprocess.run(["git", "status", "--short", "--branch"], cwd=cwd,
                              text=True, capture_output=True, timeout=20)
    except Exception as exc:
        return f"Error running git status: {exc}"
    output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    return (output.strip() or "[clean/no output]") + f"\n[exit code {proc.returncode}]"


def execute_test_runner(arguments: dict[str, Any]) -> str:
    # Default must fit a FULL suite run (~3 min serial here) — a 120s default
    # killed in-session full-suite runs and pushed the model into scoped
    # substitutes (observed live: DEVMODE05 T1355, two test_runner timeouts).
    return execute_shell({
        "command": arguments.get("command", "python -m pytest -q"),
        "workdir": arguments.get("workdir"),
        "timeout": arguments.get("timeout", 420),
        "_clean_env": arguments.get("_clean_env", True),
    })


def execute_project_bridge(arguments: dict[str, Any]) -> str:
    target = Path(arguments.get("path") or os.getcwd())
    limit = max(800, min(int(arguments.get("limit", 4000)), 20000))
    # Walk up from target looking for AGENTS.md or CLAUDE.md
    bridges = []
    for d in [target] + list(target.parents):
        for name in ("AGENTS.md", "CLAUDE.md"):
            f = d / name
            if f.exists() and f.is_file():
                try:
                    content = f.read_text(encoding="utf-8")
                    bridges.append(f"## {f.relative_to(target if target.is_dir() else target.parent)}\n\n{content}")
                except Exception:
                    pass
        if bridges:
            break
    if not bridges:
        return "[no AGENTS.md or CLAUDE.md found in project path]"
    result = "\n\n".join(bridges)
    if len(result) > limit:
        result = result[:limit] + "\n\n[...truncated...]"
    return result


_SNAPSHOT_DROP_TAGS = ("script", "style", "noscript", "svg", "head", "nav", "header", "footer", "aside", "form")


def _extract_readable(html: str) -> str:
    """Heuristic main-content extraction → clean markdown-ish text. Zero-dep.

    Drops boilerplate (nav/header/footer/aside/script/style), prefers a <main> or
    <article> region when present, and preserves heading/list/paragraph structure
    instead of collapsing the whole page to a single line.
    """
    import html as _htmllib
    import re as _re

    title_match = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.IGNORECASE | _re.DOTALL)
    title = _htmllib.unescape(title_match.group(1).strip()) if title_match else ""

    work = html
    for tag in _SNAPSHOT_DROP_TAGS:
        work = _re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", " ", work, flags=_re.DOTALL | _re.IGNORECASE)
    work = _re.sub(r"<!--.*?-->", " ", work, flags=_re.DOTALL)

    # Prefer the marked main-content region when the page provides one.
    region = _re.search(r"<(main|article)\b[^>]*>(.*?)</\1>", work, _re.DOTALL | _re.IGNORECASE)
    body = region.group(2) if region else work

    # Preserve structure: headings → markdown, list items + block ends → newlines.
    body = _re.sub(r"<h([1-6])\b[^>]*>", lambda m: "\n\n" + "#" * int(m.group(1)) + " ", body, flags=_re.IGNORECASE)
    body = _re.sub(r"</h[1-6]>", "\n", body, flags=_re.IGNORECASE)
    body = _re.sub(r"<li\b[^>]*>", "\n- ", body, flags=_re.IGNORECASE)
    body = _re.sub(r"</?(p|div|section|tr|ul|ol|table|br)\b[^>]*>", "\n", body, flags=_re.IGNORECASE)

    text = _re.sub(r"<[^>]+>", " ", body)            # strip remaining tags
    text = _htmllib.unescape(text)                   # decode entities
    text = _re.sub(r"[ \t]+", " ", text)             # collapse inline whitespace
    text = _re.sub(r" *\n *", "\n", text)            # trim around newlines
    text = _re.sub(r"\n{3,}", "\n\n", text).strip()  # collapse blank runs

    # Prepend the title as a heading, but don't duplicate it when the body already
    # opens with a matching heading (common when <title> == <h1>).
    first_heading = text.lstrip().split("\n", 1)[0].lstrip("# ").strip() if text else ""
    if title and first_heading.lower() != title.lower():
        return f"# {title}\n\n{text}".strip()
    return text


def execute_web_snapshot(arguments: dict[str, Any]) -> str:
    url = arguments["url"]
    try:
        import httpx
    except ImportError:
        return "Error: httpx not installed"
    try:
        with httpx.stream("GET", url, timeout=30, follow_redirects=True) as response:
            try:
                content_length = int(response.headers.get("content-length") or 0)
            except Exception:
                content_length = 0
            if content_length > MAX_WEB_FETCH_BYTES:
                return f"Error fetching {url}: response too large ({content_length} bytes)"
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_WEB_FETCH_BYTES:
                    return f"Error fetching {url}: response too large"
                chunks.append(chunk)
            html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
            status_code = response.status_code
    except Exception as e:
        return f"Error fetching {url}: {e}"
    result = _extract_readable(html)
    result = redact_sensitive_text(result)
    if len(result) > 10000:
        result = result[:10000] + f"\n\n[...truncated {len(result)} chars total]"
    return f"[HTTP {status_code}]\n{result}"


def execute_web_fetch(arguments: dict[str, Any]) -> str:
    url = arguments["url"]
    method = arguments.get("method", "GET").upper()
    headers_str = arguments.get("headers")
    try:
        import httpx
    except ImportError:
        return "Error: httpx not installed"
    headers = {}
    if headers_str:
        try:
            headers = json.loads(headers_str)
        except json.JSONDecodeError:
            return f"Error: Invalid headers JSON: {headers_str}"
    try:
        with httpx.stream(method, url, headers=headers, timeout=30, follow_redirects=True) as response:
            try:
                content_length = int(response.headers.get("content-length") or 0)
            except Exception:
                content_length = 0
            if content_length > MAX_WEB_FETCH_BYTES:
                return f"Error fetching {url}: response too large ({content_length} bytes)"
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_WEB_FETCH_BYTES:
                    return f"Error fetching {url}: response too large"
                chunks.append(chunk)
            body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
            status_code = response.status_code
        clean_body = redact_sensitive_text(body)
        if len(clean_body) > 50000:
            clean_body = clean_body[:50000] + f"\n[...truncated {len(clean_body)} chars total]"
        return f"[HTTP {status_code}]\n{clean_body}"
    except Exception as e:
        return f"Error fetching {url}: {e}"


# ── Tool executor map ──────────────────────────────────────────────

def execute_web_search(arguments: dict[str, Any]) -> str:
    """Search DuckDuckGo and return top results."""
    query = str(arguments.get("query", "")).strip()
    limit = min(max(int(arguments.get("limit", 5) or 5), 1), 10)
    if not query:
        return "Error: empty search query"
    try:
        import urllib.request
        import urllib.parse
        import json as _json
        url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "MO-Agent/1.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = _json.loads(resp.read().decode("utf-8"))
        results = []
        for topic in (data.get("RelatedTopics") or [])[:limit]:
            if isinstance(topic, dict) and topic.get("Text"):
                text = topic["Text"][:200]
                link = topic.get("FirstURL", "")
                results.append(f"- {text}\n  {link}" if link else f"- {text}")
            elif isinstance(topic, dict) and topic.get("Topics"):
                for sub in topic["Topics"][:2]:
                    if sub.get("Text"):
                        text = sub["Text"][:200]
                        link = sub.get("FirstURL", "")
                        results.append(f"- {text}\n  {link}" if link else f"- {text}")
        abstract = data.get("AbstractText", "")
        if abstract:
            results.insert(0, f"Summary: {abstract[:300]}")
        if not results:
            return f"No results found for: {query}"
        return "\n".join(results[:limit])
    except Exception as e:
        return f"Search error: {e}"


def execute_complete_task(arguments: dict[str, Any]) -> str:
    task_id = str(arguments.get("task_id", "") or "").strip()
    if task_id:
        return f"Task {task_id} marked as complete. You may now proceed to the next phase of work."
    return "Active task marked as complete. You may now proceed to the next phase of work."


TOOL_EXECUTORS = {
    "read_file": execute_read_file,
    "write_file": execute_write_file,
    "edit_file": execute_edit_file,
    "shell": execute_shell,
    "find_files": execute_find_files,
    "grep": execute_grep,
    "git_status": execute_git_status,
    "test_runner": execute_test_runner,
    "project_bridge": execute_project_bridge,
    "web_fetch": execute_web_fetch,
    "web_snapshot": execute_web_snapshot,
    "web_search": execute_web_search,
    "complete_task": execute_complete_task,
}
