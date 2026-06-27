"""MO Agent — tool implementations and provider-facing definitions.

Code-search and caller/callee tools expose MO's graph as first-class tools
instead of shell one-liners. The agent keeps the full catalog locally and sends
a small active subset to the provider; tool_search activates deferred schemas.
Sandbox gates still enforce every dispatch via core.tooling.sandbox.guard_tool_call().
"""

import os
import re
import sys
import json
import subprocess
import fnmatch
import threading
from pathlib import Path
from typing import Any

from core.tooling.sandbox import safe_env, redact_sensitive_text
from core.tooling.shell_processes import (
    _register_shell_process,
    _unregister_shell_process,
    _kill_process_tree,
)
from .screen import execute_capture_screen
from .desktop import (
    execute_screen_size,
    execute_point_on_screen,
    execute_move_pointer,
    execute_mouse_click,
    execute_type_text,
    execute_press_key,
)


def execute_browser_open(arguments: dict[str, Any]) -> str:
    from .browser import execute_browser_open as _execute
    return _execute(arguments)


def execute_browser_snapshot(arguments: dict[str, Any]) -> str:
    from .browser import execute_browser_snapshot as _execute
    return _execute(arguments)


def execute_browser_click(arguments: dict[str, Any]) -> str:
    from .browser import execute_browser_click as _execute
    return _execute(arguments)


def execute_browser_type(arguments: dict[str, Any]) -> str:
    from .browser import execute_browser_type as _execute
    return _execute(arguments)


def execute_browser_eval(arguments: dict[str, Any]) -> str:
    from .browser import execute_browser_eval as _execute
    return _execute(arguments)


def execute_browser_close(arguments: dict[str, Any]) -> str:
    from .browser import execute_browser_close as _execute
    return _execute(arguments)


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
            "name": "tool_search",
            "description": "Search MO's local tool catalog and activate matching deferred tool schemas for the next provider request. Use this before calling tools that are not currently available, such as editing, shell/test execution, web, browser, desktop, or memory-recording tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Capability or exact tool name to search for, e.g. 'edit files', 'run tests', 'browser click', or 'shell'."},
                    "tools": {
                        "type": "array",
                        "description": "Optional exact tool names to activate.",
                        "items": {"type": "string"},
                    },
                    "max_results": {"type": "integer", "description": "Maximum result rows to return (default 8, max 20)."},
                    "activate_limit": {"type": "integer", "description": "Maximum matching deferred tools to activate (default 4, max 8)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the operator's DEFAULT browser, visibly (their own profile and logins). THIS is the tool for 'open / show me / pull up <site>' — the operator wants to look at it themselves. Do NOT use shell (e.g. `start chrome`) for this, and do NOT use browser_open (that is an isolated, invisible browser for autonomous tasks).",
            "parameters": {
                "type": "object",
                "required": ["url"],
                "properties": {"url": {"type": "string", "description": "URL to open (https:// added if missing)"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_screen",
            "description": "Capture a screenshot of the operator's primary display so MO can SEE what is currently on screen. Use when the operator asks about what's on their screen, to read an on-screen error/dialog/diagram/UI, or to check the result of a desktop action. Returns a confirmation; the image itself is attached for vision analysis. Requires a vision-capable provider.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_size",
            "description": "Get the operator's screen resolution (width x height) so you can compute coordinates for pointing/clicking.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "point_on_screen",
            "description": "GUIDED mode (safe, actuates nothing): show MO's on-screen bubble + label at a coordinate to point the operator at something. Use to guide the operator ('click here') without taking control.",
            "parameters": {
                "type": "object",
                "required": ["x", "y"],
                "properties": {
                    "x": {"type": "integer", "description": "X pixel coordinate"},
                    "y": {"type": "integer", "description": "Y pixel coordinate"},
                    "label": {"type": "string", "description": "Short text shown in the bubble"},
                    "seconds": {"type": "number", "description": "How long to show (default 4)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_pointer",
            "description": "ACTUATION: move the real mouse cursor to a screen coordinate. Slam the mouse into a screen corner to abort all actuation (failsafe).",
            "parameters": {
                "type": "object",
                "required": ["x", "y"],
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_click",
            "description": "ACTUATION: click the real mouse. Omit x/y to click at the current pointer, or pass x/y to click there. button: left/right/middle; clicks: 1 or 2.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"}, "y": {"type": "integer"},
                    "button": {"type": "string", "description": "left (default), right, middle"},
                    "clicks": {"type": "integer", "description": "1 (default) or 2 for double-click"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "ACTUATION: type text on the real keyboard into whatever window has focus.",
            "parameters": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string", "description": "Text to type"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "ACTUATION: press a key or chord on the real keyboard. 'keys' is a single key ('enter', 'win', 'esc', 'tab'), a combo ('ctrl+c'), or a list for a sequence. Example to open an app: press 'win', then type_text the name, then press 'enter'.",
            "parameters": {
                "type": "object",
                "required": ["keys"],
                "properties": {"keys": {"description": "Key, 'mod+key' chord, or list of them"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": "Open a real browser (Chrome via DevTools Protocol) and navigate to a URL. Use to do things on the web for the operator. Returns the page title; then call browser_snapshot to see clickable elements.",
            "parameters": {
                "type": "object",
                "required": ["url"],
                "properties": {"url": {"type": "string", "description": "URL to open (https:// added if missing)"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": "List the interactive elements on the current browser page as numbered refs (e1, e2, ...) with role and label. Call this before browser_click/browser_type, and again after any navigation (refs reset).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element on the current page by its ref from browser_snapshot (e.g. 'e3').",
            "parameters": {
                "type": "object",
                "required": ["ref"],
                "properties": {"ref": {"type": "string", "description": "Element ref from browser_snapshot"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type text into an input/textarea on the current page by its ref. Set submit=true to submit the form after.",
            "parameters": {
                "type": "object",
                "required": ["ref", "text"],
                "properties": {
                    "ref": {"type": "string", "description": "Element ref from browser_snapshot"},
                    "text": {"type": "string", "description": "Text to type"},
                    "submit": {"type": "boolean", "description": "Submit the form after typing (default false)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_eval",
            "description": "Run JavaScript in the current page and return the result. Power tool for reading page state or doing what click/type can't.",
            "parameters": {
                "type": "object",
                "required": ["expression"],
                "properties": {"expression": {"type": "string", "description": "JavaScript expression to evaluate"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "Close the browser MO opened and clean up its temporary profile.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
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
                    "timeout": {"type": "integer", "description": "Timeout seconds (default 420)"},
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
            "name": "code_search",
            "description": "Find files/symbols by a loose natural-language query using BM25 relevance over MO's code graph (e.g. 'where is rate limiting', 'auth logic'). Prefer this over a blind grep/read sweep for orientation — one call ranks the most relevant nodes and replaces many grep/read_file calls. Returns ranked source files + locations.",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Natural-language description of what to find"},
                    "top_n": {"type": "integer", "description": "Maximum results to return (default 10)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_callers",
            "description": "Answer 'who calls / depends on X?' by walking MO's code graph backward. Far cheaper than grepping a symbol across the tree. Returns caller symbols, their files, and the relation.",
            "parameters": {
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {"type": "string", "description": "Function/class/module symbol to find callers of"},
                    "max_depth": {"type": "integer", "description": "How many edges to walk back (default 2)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_callees",
            "description": "Answer 'what does X call / depend on?' by walking MO's code graph forward. Returns callee symbols, their files, and the relation.",
            "parameters": {
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {"type": "string", "description": "Function/class/module symbol to find dependencies of"},
                    "max_depth": {"type": "integer", "description": "How many edges to walk forward (default 2)"},
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
    {
        "type": "function",
        "function": {
            "name": "record_convention",
            "description": "Record a durable, code-LOCATION-scoped convention you have learned, so it AUTO-SURFACES whenever you or a future MO run works on the matching files - without re-reading context. Use ONLY for a real, evidence-backed rule tied to a code area (e.g. 'in core/tasking, task rows advance only via complete_task evidence'). Requires a file-glob `scope`. Do NOT use for one-off task notes or general behavioral style. Persists to the operator's global skill store across all runs.",
            "parameters": {
                "type": "object",
                "required": ["name", "rule", "scope"],
                "properties": {
                    "name": {"type": "string", "description": "short convention name"},
                    "rule": {"type": "string", "description": "the rule in one or two sentences"},
                    "scope": {"type": "string", "description": "space-separated file-globs the rule governs, e.g. 'core/tasking/* core/agent/agent_taskboard.py'"},
                    "evidence": {"type": "string", "description": "why it is true - the correction, pattern, or file:line evidence"},
                    "confidence": {"type": "string", "description": "high | medium | low"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_profile_fact",
            "description": "Persist a durable OPERATIONAL fact the operator just shared about their setup, so you don't re-ask or re-discover it next time. Use it AUTONOMOUSLY (you decide) whenever the operator reveals something durable: a server/host, a repo or GitHub account/access, a deploy method, a project path, where a credential/key lives (its LOCATION, never the value), an SSH target, or a stated preference. Capture only what the operator actually shared — never guess. It auto-surfaces in your profile context on later turns. Do NOT store secret VALUES (keys/passwords/tokens) — only their location/status.",
            "parameters": {
                "type": "object",
                "required": ["category", "fact"],
                "properties": {
                    "category": {"type": "string", "description": "one of: server | repo | access | credential | deploy | project | preference"},
                    "fact": {"type": "string", "description": "the durable fact in one line, e.g. 'prod API runs as a systemd service on the deploy host under /opt/<app>'"},
                    "evidence": {"type": "string", "description": "the operator's words / how you learned it"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_plan",
            "description": "Set the visible taskboard to YOUR OWN plan for this turn — the concrete steps you will actually take, in order. The board then tracks your real work; advance each row with complete_task as you finish it. Use a small, accurate plan (typically 2-6 steps), not generic boilerplate. Call this once near the start of a multi-step task.",
            "parameters": {
                "type": "object",
                "required": ["tasks"],
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "ordered list of concrete steps; each a short string or {text, kind}",
                        "items": {"type": "object", "properties": {"text": {"type": "string"}, "kind": {"type": "string", "description": "inspect | edit | verify | report"}}},
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


def _looks_like_pytest_command(command: object) -> bool:
    text = str(command or "").lower()
    return bool(re.search(r"\b(pytest|python\s+-m\s+pytest|py\s+-m\s+pytest)\b", text))


def _tool_timeout(command: object, requested: object, default: int) -> int:
    try:
        timeout = int(requested if requested is not None else default)
    except (TypeError, ValueError):
        timeout = default
    if _looks_like_pytest_command(command):
        return max(timeout, 420)
    return timeout


def _test_runner_timeout(command: object, requested: object = None) -> int:
    return _tool_timeout(command, requested, 420)


def execute_shell(arguments: dict[str, Any]) -> str:
    command = str(arguments.get("command", "")).strip()
    workdir = arguments.get("workdir") or os.getcwd()
    timeout = _tool_timeout(command, arguments.get("timeout"), 60)
    cwd = workdir

    shell_cmd, use_shell, registered_command = _shell_command(command)

    try:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        env = safe_env() if bool(arguments.get("_clean_env", True)) else os.environ.copy()
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
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
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def _drain_pipe(stream: Any, chunks: list[str]) -> None:
            try:
                while True:
                    chunk = stream.read(1)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except Exception:
                pass

        stdout_thread = threading.Thread(target=_drain_pipe, args=(proc.stdout, stdout_chunks), daemon=True)
        stderr_thread = threading.Thread(target=_drain_pipe, args=(proc.stderr, stderr_chunks), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        try:
            try:
                proc.wait(timeout=timeout)
                stdout_thread.join(timeout=2)
                stderr_thread.join(timeout=2)
            except subprocess.TimeoutExpired:
                _kill_process_tree(proc.pid)
                stdout_thread.join(timeout=0.2)
                stderr_thread.join(timeout=0.2)
                # Capture whatever the process printed BEFORE the kill, so a slow run
                # still yields actionable progress instead of nothing — this is what
                # stops the model from backgrounding+poll-looping (or blindly re-running)
                # a long job, which leaves it parked with no real output.
                stdout_partial = "".join(stdout_chunks)
                stderr_partial = "".join(stderr_chunks)
                partial = (stdout_partial + ("\n[stderr]\n" + stderr_partial if stderr_partial else "")).strip()
                guidance = (
                    f"Error: Command timed out after {timeout}s and was killed. Do NOT re-run the "
                    "same long command on a loop and do NOT background it and poll — that burns turns "
                    "and never finishes. Run it ONCE with a higher `timeout`, narrow the scope, or use "
                    "a faster invocation (for the test suite: `python -m pytest -q -n auto --dist loadfile`)."
                )
                if partial:
                    return (
                        f"[Partial output captured before the {timeout}s timeout — process killed, "
                        f"likely incomplete]\n{partial[-6000:]}\n\n{guidance}"
                    )
                return guidance
        finally:
            _unregister_shell_process(proc.pid)

        output = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
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
    command = arguments.get("command", "python -m pytest -q")
    return execute_shell({
        "command": command,
        "workdir": arguments.get("workdir"),
        "timeout": _test_runner_timeout(command, arguments.get("timeout")),
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

def _format_brave_results(data: dict, limit: int) -> str:
    items = ((data.get("web") or {}).get("results") or [])[:limit]
    out = []
    for it in items:
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        desc = str(it.get("description") or "").strip()[:200]
        out.append(f"- {title}\n  {url}" + (f"\n  {desc}" if desc else ""))
    return "\n".join(out)


def _format_serper_results(data: dict, limit: int) -> str:
    items = (data.get("organic") or [])[:limit]
    out = []
    for it in items:
        title = str(it.get("title") or "").strip()
        url = str(it.get("link") or "").strip()
        snippet = str(it.get("snippet") or "").strip()[:200]
        out.append(f"- {title}\n  {url}" + (f"\n  {snippet}" if snippet else ""))
    return "\n".join(out)


def _web_search_keyed(query: str, limit: int) -> str | None:
    """Real web search via an optional, operator-set API key (no Python dependency).

    Reads MO_WEB_SEARCH_PROVIDER (brave|serper) + MO_WEB_SEARCH_API_KEY from the env.
    Returns formatted results, or None to fall through to the keyless default. Opt-in:
    absent key → None → DuckDuckGo fallback (current behavior preserved).
    """
    provider = os.environ.get("MO_WEB_SEARCH_PROVIDER", "").strip().lower()
    key = os.environ.get("MO_WEB_SEARCH_API_KEY", "").strip()
    if not key or provider not in {"brave", "serper"}:
        return None
    try:
        import urllib.request
        import urllib.parse
        import json as _json
        if provider == "brave":
            url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={limit}"
            req = urllib.request.Request(url, headers={"X-Subscription-Token": key, "Accept": "application/json", "User-Agent": "MO-Agent/1.0"})
            resp = urllib.request.urlopen(req, timeout=12)
            formatted = _format_brave_results(_json.loads(resp.read().decode("utf-8")), limit)
        else:  # serper
            body = _json.dumps({"q": query, "num": limit}).encode("utf-8")
            req = urllib.request.Request("https://google.serper.dev/search", data=body, headers={"X-API-KEY": key, "Content-Type": "application/json", "User-Agent": "MO-Agent/1.0"})
            resp = urllib.request.urlopen(req, timeout=12)
            formatted = _format_serper_results(_json.loads(resp.read().decode("utf-8")), limit)
        return formatted or f"No results found for: {query}"
    except Exception:
        # Keyed backend failed — fall through to the keyless default rather than error out.
        return None


def _web_search_duckduckgo(query: str, limit: int) -> str:
    """Keyless fallback: DuckDuckGo Instant Answer (encyclopedic; limited)."""
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
            return (f"No results found for: {query}. (DuckDuckGo Instant Answer is limited; "
                    "set MO_WEB_SEARCH_PROVIDER=brave|serper + MO_WEB_SEARCH_API_KEY for full web search.)")
        return "\n".join(results[:limit])
    except Exception as e:
        return f"Search error: {e}"


def execute_web_search(arguments: dict[str, Any]) -> str:
    """Search the web. Uses an operator-set API key (Brave/Serper) when present for full
    results; otherwise falls back to DuckDuckGo Instant Answer. No Python dependency."""
    query = str(arguments.get("query", "")).strip()
    limit = min(max(int(arguments.get("limit", 5) or 5), 1), 10)
    if not query:
        return "Error: empty search query"
    keyed = _web_search_keyed(query, limit)
    if keyed is not None:
        return keyed
    return _web_search_duckduckgo(query, limit)


def _format_graph_hits(hits: list[dict[str, Any]], fields: list[tuple[str, str]], empty: str, limit: int = 20) -> str:
    if not hits:
        return empty
    lines: list[str] = []
    for hit in hits[:limit]:
        parts = [f"{label}={hit.get(key)}" for label, key in fields if hit.get(key) not in (None, "")]
        lines.append("- " + ", ".join(parts))
    more = len(hits) - limit
    if more > 0:
        lines.append(f"... (+{more} more)")
    return "\n".join(lines)


def execute_code_search(arguments: dict[str, Any]) -> str:
    query = str(arguments.get("query", "") or "").strip()
    if not query:
        return "Error: code_search requires a 'query'."
    top_n = arguments.get("top_n")
    try:
        top_n = int(top_n) if top_n else 10
    except (TypeError, ValueError):
        top_n = 10
    try:
        from core.graph.search import search
    except Exception as exc:
        return f"Error: code graph search unavailable: {exc}"
    try:
        hits = search(query, cwd=os.getcwd(), top_n=top_n)
    except Exception as exc:
        return f"Error running code_search: {exc}"
    return _format_graph_hits(
        hits,
        [("file", "source_file"), ("symbol", "label"), ("at", "source_location"), ("score", "score")],
        empty=f"No code-graph matches for {query!r}. The graph may be empty/stale — fall back to grep/read_file.",
    )


def execute_find_callers(arguments: dict[str, Any]) -> str:
    symbol = str(arguments.get("symbol", "") or "").strip()
    if not symbol:
        return "Error: find_callers requires a 'symbol'."
    max_depth = arguments.get("max_depth")
    try:
        max_depth = int(max_depth) if max_depth else 2
    except (TypeError, ValueError):
        max_depth = 2
    try:
        from core.graph.callgraph import get_callers
    except Exception as exc:
        return f"Error: code graph unavailable: {exc}"
    try:
        hits = get_callers(symbol, cwd=os.getcwd(), max_depth=max_depth)
    except Exception as exc:
        return f"Error running find_callers: {exc}"
    return _format_graph_hits(
        hits,
        [("caller", "caller_label"), ("file", "caller_file"), ("relation", "relation"), ("depth", "depth")],
        empty=f"No callers found for {symbol!r} in the code graph (it may be a leaf, or the graph is stale).",
    )


def execute_find_callees(arguments: dict[str, Any]) -> str:
    symbol = str(arguments.get("symbol", "") or "").strip()
    if not symbol:
        return "Error: find_callees requires a 'symbol'."
    max_depth = arguments.get("max_depth")
    try:
        max_depth = int(max_depth) if max_depth else 2
    except (TypeError, ValueError):
        max_depth = 2
    try:
        from core.graph.callgraph import get_callees
    except Exception as exc:
        return f"Error: code graph unavailable: {exc}"
    try:
        hits = get_callees(symbol, cwd=os.getcwd(), max_depth=max_depth)
    except Exception as exc:
        return f"Error running find_callees: {exc}"
    return _format_graph_hits(
        hits,
        [("callee", "callee_label"), ("file", "callee_file"), ("relation", "relation"), ("depth", "depth")],
        empty=f"No callees found for {symbol!r} in the code graph (it may have no outgoing edges, or the graph is stale).",
    )


def execute_open_url(arguments: dict[str, Any]) -> str:
    """Open a URL in the operator's DEFAULT browser, visibly (their profile/logins).

    This is the right tool for "open/show me/pull up <site>" — it uses the OS
    default-browser handler (whatever the operator set), not a hardcoded browser
    and not the isolated CDP browser. For autonomous web *tasks* (read/click/fill
    programmatically) use the browser_* tools instead.
    """
    import webbrowser
    url = str(arguments.get("url", "") or "").strip()
    if not url:
        return "Error: open_url requires a 'url'."
    if not url.startswith(("http://", "https://", "file:", "mailto:", "about:")):
        url = "https://" + url
    try:
        opened = webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001
        return f"Error: could not open the default browser: {type(exc).__name__}: {exc}"
    if opened:
        return f"Opened {url} in your default browser."
    return f"Requested to open {url}, but no default browser handler confirmed success."


def execute_tool_search(arguments: dict[str, Any]) -> str:
    """Fallback catalog search for non-Agent dispatch contexts.

    Agent dispatch replaces this with a per-turn registry so activations affect
    the next provider request.  This fallback still returns the same result shape
    for tests, diagnostics, and standalone tool contexts.
    """
    try:
        from core.tooling.tool_registry import DeferredToolRegistry
        return DeferredToolRegistry(TOOL_DEFINITIONS).search(arguments or {})
    except Exception as exc:
        return f"Error running tool_search: {exc}"


def execute_complete_task(arguments: dict[str, Any]) -> str:
    task_id = str(arguments.get("task_id", "") or "").strip()
    if task_id:
        return f"Task {task_id} marked as complete. You may now proceed to the next phase of work."
    return "Active task marked as complete. You may now proceed to the next phase of work."


def execute_record_convention(arguments: dict[str, Any]) -> str:
    """MO records a durable, code-location-scoped convention it has learned (autonomous)."""
    try:
        from core.skills import write_convention
        path = write_convention(
            name=str(arguments.get("name", "") or "").strip(),
            rule=str(arguments.get("rule", "") or "").strip(),
            scope=str(arguments.get("scope", "") or "").strip(),
            evidence=str(arguments.get("evidence", "") or "").strip(),
            confidence=(str(arguments.get("confidence", "high") or "high").strip() or "high"),
        )
        return (f"Convention recorded ({path}). Scope: {arguments.get('scope')}. "
                "It will auto-surface on later turns and future runs when you work on matching files.")
    except ValueError as exc:
        return (f"Convention NOT recorded: {exc}. A convention needs a concrete rule AND a file-glob "
                "scope (e.g. 'core/tasking/*'). A behavioral style with no code location is not a convention.")
    except Exception as exc:
        return f"Convention write failed: {exc}"


def execute_record_profile_fact(arguments: dict[str, Any]) -> str:
    """MO autonomously persists a durable operational fact the operator shared.

    Hardened: facts.md is auto-injected into provider context every turn, so this
    fails CLOSED against (a) secret values, (b) prompt-injection / markdown control
    structure, and (c) raw reachable endpoints (IPs / SSH connection strings) —
    record host aliases / locations / status, never raw endpoints or instructions.
    """
    import re as _re
    category = str(arguments.get("category", "") or "").strip().lower()
    # Collapse to a single clean line — no multiline/markdown structure survives.
    fact = " ".join(str(arguments.get("fact", "") or "").split())
    evidence = " ".join(str(arguments.get("evidence", "") or "").split())
    if not category or not fact:
        return "Fact NOT recorded: need a category and a concrete one-line fact."
    if len(fact) > 200:
        return "Fact NOT recorded: keep it to one short line (<=200 chars)."
    try:
        from core.utils.text_safety import contains_secret_value
        if contains_secret_value(fact) or contains_secret_value(evidence):
            return ("Fact NOT recorded: it contained a secret value. Record only the LOCATION/"
                    "status of a credential (e.g. 'the API keys live in the profile vault'), never the value.")
    except Exception:
        pass
    # Reject instruction/markdown/control content (prompt-injection persistence).
    if _re.search(r"(?:^|\s)#{1,6}\s|```|\b(?:system|assistant|user)\s*:|ignore (?:previous|prior|above)|disregard .*instruction|override .*instruction", fact, _re.I):
        return "Fact NOT recorded: looks like instruction/markdown content, not a plain operational fact."
    # Reject raw reachable endpoints — IPs / SSH connection strings get auto-injected
    # every turn; store an alias/host name/location/status instead.
    if _re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", fact) or _re.search(r"\bssh://|\bssh\s+\S+@|@\d{1,3}(?:\.\d{1,3}){3}", fact, _re.I):
        return "Fact NOT recorded: don't store raw IPs or SSH connection strings — record a host alias / location / status instead."
    # Fail-closed safety scan (same class workflow-learning already blocks).
    try:
        from core.gates.threat_scan import scan_text
        scan = scan_text(f"{fact}\n{evidence}", surface="profile fact")
        if getattr(scan, "blocked", False):
            return f"Fact NOT recorded: failed the safety scan ({scan.reason()})."
    except Exception:
        pass
    try:
        from pathlib import Path
        from core.state.paths import resolve_state_path
        from core.utils.atomic_write import atomic_write_text
        path = Path(resolve_state_path("memory/profile/facts.md"))
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# Operator Operational Facts (auto-captured)\n\n"
            "Durable facts the operator shared — servers, repos, access, deploy methods, "
            "project paths, credential LOCATIONS (never values). MO records these autonomously.\n\n"
        )
        existing = path.read_text(encoding="utf-8") if path.exists() else header
        line = f"- [{category}] {fact}"
        if line in existing:
            return f"Fact already recorded: {fact}"
        body = existing if existing.strip() else header
        atomic_write_text(path, body.rstrip() + "\n" + line + "\n", encoding="utf-8")
        return f"Recorded operator fact [{category}]: {fact} (auto-surfaces in profile context next turns)."
    except Exception as exc:
        return f"Fact write failed: {exc}"


def execute_set_plan(arguments: dict[str, Any]) -> str:
    """Stub: the real board mutation happens in the dispatch layer (agent_taskboard
    ._apply_model_plan), like complete_task. This only confirms to the model."""
    raw = arguments.get("tasks") or arguments.get("plan") or []
    if not isinstance(raw, list):
        return "set_plan needs a 'tasks' list of short ordered steps."
    n = sum(1 for t in raw if (isinstance(t, str) and t.strip()) or (isinstance(t, dict) and (t.get("text") or t.get("title"))))
    if not n:
        return "set_plan needs a non-empty 'tasks' list (each a short step)."
    return f"Plan set with {n} step(s); the taskboard now tracks your plan. Advance each row with complete_task as you finish it."


TOOL_EXECUTORS = {
    "tool_search": execute_tool_search,
    "record_profile_fact": execute_record_profile_fact,
    "set_plan": execute_set_plan,
    "capture_screen": execute_capture_screen,
    "open_url": execute_open_url,
    "screen_size": execute_screen_size,
    "point_on_screen": execute_point_on_screen,
    "move_pointer": execute_move_pointer,
    "mouse_click": execute_mouse_click,
    "type_text": execute_type_text,
    "press_key": execute_press_key,
    "browser_open": execute_browser_open,
    "browser_snapshot": execute_browser_snapshot,
    "browser_click": execute_browser_click,
    "browser_type": execute_browser_type,
    "browser_eval": execute_browser_eval,
    "browser_close": execute_browser_close,
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
    "code_search": execute_code_search,
    "find_callers": execute_find_callers,
    "find_callees": execute_find_callees,
    "complete_task": execute_complete_task,
    "record_convention": execute_record_convention,
}
