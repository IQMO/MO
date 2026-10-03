"""File and search tool executors for MO (read/write/edit, find, grep).

Moved verbatim from tools/__init__.py (consolidation pass 1); tools re-exports
every public name so existing import paths and monkeypatch seams keep working.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any

from core.tooling.sandbox import secret_read_path_kind


SKIP_PATH_PARTS = {
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules",
    ".venv", "venv", "logs", "memory", ".codex", ".gradle", "build", "dist", "tmp",
}
MAX_GREP_SCANNED_FILES = 2500
MAX_GREP_FILE_BYTES = 1_000_000
DEFAULT_READ_FILE_OUTPUT_CHARS = 50_000
DEFAULT_READ_FILE_MAX_LINES = 2_000


def _skip_path(path: Path) -> bool:
    return any(part in SKIP_PATH_PARTS for part in path.parts)


def _iter_unskipped_files(root_path: Path):
    # Exclusions apply below the explicit root, never to its ancestors. Naming
    # a root inside "tmp" must not also allow every nested "tmp" directory.
    if root_path.is_file():
        yield root_path
        return
    for dirpath, dirnames, filenames in os.walk(root_path):
        current = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in SKIP_PATH_PARTS]
        for filename in filenames:
            p = current / filename
            if not _skip_path(p.relative_to(root_path)):
                yield p



def _numbered_lines(lines: list[str], start_line: int = 1) -> str:
    width = len(str(start_line + len(lines) - 1)) if lines else len(str(start_line))
    return "\n".join(f"{index:>{width}}: {line}" for index, line in enumerate(lines, start_line))


def _bounded_read_page(
    lines: list[str],
    *,
    start_line: int,
    total_lines: int,
    view_note: str,
    max_chars: int,
    source_truncated: bool = False,
) -> str:
    """Render complete numbered lines inside the dispatcher's output budget."""
    max_chars = max(1, int(max_chars or DEFAULT_READ_FILE_OUTPUT_CHARS))
    if not lines:
        if total_lines == 0 and start_line == 1:
            return (view_note + "[Empty file; 0 lines]")[:max_chars]
        return (
            f"Error: offset {start_line} is beyond the end of this view "
            f"({total_lines} lines)."
        )[:max_chars]

    def render(count: int) -> str:
        end_line = start_line + count - 1
        forced = source_truncated or count < len(lines)
        if forced:
            header = (
                f"[Truncated — showing lines {start_line}-{end_line} of {total_lines}; "
                f"continue with offset={end_line + 1}]"
            )
        else:
            header = f"[Lines {start_line}-{end_line} of {total_lines}]"
        return view_note + header + "\n" + _numbered_lines(lines[:count], start_line)

    complete = render(len(lines))
    if len(complete) <= max_chars:
        return complete

    low, high = 0, len(lines)
    while low < high:
        mid = (low + high + 1) // 2
        if len(render(mid)) <= max_chars:
            low = mid
        else:
            high = mid - 1
    if low:
        return render(low)

    header = (
        f"[Truncated within line {start_line} of {total_lines}; "
        "line exceeds read_file output limit]"
    )
    prefix = f"{start_line}: "
    suffix = "\n[...line truncated at read_file output limit...]"
    fixed = view_note + header + "\n" + prefix
    room = max_chars - len(fixed) - len(suffix)
    if room <= 0:
        return (view_note + header)[:max_chars]
    return fixed + lines[0][:room].rstrip() + suffix


def execute_read_file(arguments: dict[str, Any]) -> str:
    path = arguments["path"]
    offset = _coerce_positive_int(arguments.get("offset"), "offset")
    limit = _coerce_positive_int(arguments.get("limit"), "limit")
    if isinstance(offset, str):
        return offset
    if isinstance(limit, str):
        return limit
    try:
        max_chars = int(arguments.get("_mo_max_output_chars") or DEFAULT_READ_FILE_OUTPUT_CHARS)
    except (TypeError, ValueError):
        max_chars = DEFAULT_READ_FILE_OUTPUT_CHARS
    if max_chars <= 0:
        max_chars = DEFAULT_READ_FILE_OUTPUT_CHARS
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
    view_note = ""
    if arguments.get("_mo_session_snapshot"):
        from core.session.session import project_messages

        try:
            snapshot = json.loads(content)
            archived_chain = isinstance(snapshot, list)
            messages = snapshot if archived_chain else snapshot["messages"]
            if not isinstance(messages, list):
                raise TypeError("messages must be a list")
            view = str(arguments.get("view") or (
                "evidence" if archived_chain else "conversation"
            )).strip().lower()
            if view not in {"conversation", "evidence"}:
                return "Error: saved session view must be conversation or evidence."
            messages = project_messages(messages)
            if view == "conversation":
                messages = [
                    {key: value for key, value in message.items() if key != "tool_calls"}
                    for message in messages
                    if isinstance(message, dict) and message.get("role") in {"user", "assistant"}
                    and message.get("content")
                ]
            if archived_chain:
                snapshot = messages
            else:
                snapshot["messages"] = messages
            content = json.dumps(snapshot, ensure_ascii=False, indent=2)
        except (ValueError, KeyError, TypeError):
            return f"Error: Invalid saved session: {path}"
        view_note = (
            f"[{'Compacted tool archive' if archived_chain else 'Saved session'} {view} view; "
            "message replay and presentation fields omitted. "
            + ("Tool calls/results omitted: conversation is dated orientation, not execution proof. "
               "Use view=evidence on this same path to inspect original tool records. "
               if view == "conversation" else "Original tool calls/results retained. ")
            + "Line numbers refer to this view; restart paging when changing view. Stored snapshot unchanged.]\n"
        )
    lines = content.splitlines()
    total_lines = len(lines)
    if offset is not None or limit is not None:
        start = (offset or 1) - 1
        end = (start + limit) if limit else total_lines
        return _bounded_read_page(
            lines[start:end],
            start_line=start + 1,
            total_lines=total_lines,
            view_note=view_note,
            max_chars=max_chars,
        )
    selected = lines[:DEFAULT_READ_FILE_MAX_LINES]
    return _bounded_read_page(
        selected,
        start_line=1,
        total_lines=total_lines,
        view_note=view_note,
        max_chars=max_chars,
        source_truncated=len(selected) < total_lines,
    )


def _coerce_positive_int(value: Any, name: str) -> int | None | str:
    if value is None:
        return None
    if isinstance(value, bool):
        return f"Error: {name} must be a positive integer."
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return f"Error: {name} must be a positive integer."
    if coerced < 1:
        return f"Error: {name} must be a positive integer."
    return coerced


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
        return (
            f"Error: old_text not found in {path}. No file was changed; this is an exact-text mismatch. "
            "Read the relevant range again and correct old_text before retrying. Preserve the file's "
            "indentation; read_file's line-number prefix and its single separator space are not file content."
        )
    if content.count(old_text) > 1:
        return f"Error: old_text is not unique in {path} (found {content.count(old_text)} occurrences)"
    new_content = content.replace(old_text, new_text, 1)
    try:
        p.write_text(new_content, encoding="utf-8")
        return f"Edited {path} — 1 replacement"
    except Exception as e:
        return f"Error writing {path}: {e}"




def execute_find_files(arguments: dict[str, Any]) -> str:
    pattern = str(arguments.get("pattern", ""))
    root = arguments.get("root") or os.getcwd()
    limit_value = _coerce_positive_int(arguments.get("limit", 200), "limit")
    if isinstance(limit_value, str):
        return limit_value
    limit = min(limit_value or 200, 1000)
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
    limit_value = _coerce_positive_int(arguments.get("limit", 200), "limit")
    if isinstance(limit_value, str):
        return limit_value
    limit = min(limit_value or 200, 1000)
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
        if secret_read_path_kind(p) or not _glob_hint_matches(p, file_glob):
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
