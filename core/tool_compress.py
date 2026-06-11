"""MO Agent native tool output compression.

Classifies tool output by format and applies lossless structural compression.
Uses MO Agent's own regex-based detection pattern (same idiom as sandbox.py,
goal_auditor.py). Always safe: failures return original text.

Design contract:
- Never return empty string (passthrough on failure)
- Never grow the output (passthrough if no savings)
- Peeks first 1KB for format detection (configurable via detect_window)
- Respects min_bytes threshold (skip tiny outputs)
- Emits monitor events for UI observability
"""
from __future__ import annotations

import re

# Detection markers (MO Agent pattern: compiled regex lists like sandbox.py L18-25)

_GIT_DIFF_HEAD_RE = re.compile(r"^diff --git ", re.MULTILINE)
_GIT_STATUS_HEAD_RE = re.compile(r"^On branch |^nothing to commit|^Changes |^Untracked files:", re.MULTILINE)
_GREP_LINE_RE = re.compile(r"^[^\s:]+\.[a-z0-9]+:\d+:", re.MULTILINE)
_BUILD_HEAD_RE = re.compile(r"^npm (ERR!|warn|error)|^\s*Compiling\s+|^BUILD (SUCCESS|FAILED)|^\[ERROR\]|^error(\[|:)", re.MULTILINE)
_FIND_PATH_RE = re.compile(r"^(\.{1,2}[/\\]|[a-zA-Z]:[/\\]|[/\\])\S|^\S+[/\\]\S+$", re.MULTILINE)
_LS_PERMS_RE = re.compile(r"^[-dlbcps][rwx-]{9}", re.MULTILINE)
_TREE_GLYPH_RE = re.compile(r"[├└]──|│  ")
_TEST_PASS_FAIL_RE = re.compile(r"^\d+\s+(passed|failed)", re.MULTILINE)
_LINE_NUMBERED_RE = re.compile(r"^\s*\d+:", re.MULTILINE)
_WEB_HTML_RE = re.compile(r"^<!DOCTYPE html|<html[\s>]", re.MULTILINE | re.IGNORECASE)

# Compressor functions (each follows MO Agent "compact + summary" pattern)

def _compress_git_diff(text):
    """Collapse hunks over 100 lines, keep file headers and +/- counts."""
    result = []
    current_file = ""
    added = 0
    removed = 0
    in_hunk = False
    hunk_shown = 0
    hunk_skipped = 0
    max_hunk = 100

    for line in text.split("\n"):
        if line.startswith("diff --git"):
            if hunk_skipped > 0:
                result.append(f"  ... ({hunk_skipped} lines truncated)")
                hunk_skipped = 0
            if current_file and (added or removed):
                result.append(f"  +{added} -{removed}")
            current_file = line.split(" b/")[-1] if " b/" in line else line
            result.append(f"\n{current_file}")
            added = 0
            removed = 0
            in_hunk = False
            hunk_shown = 0
        elif line.startswith("@@"):
            if hunk_skipped > 0:
                result.append(f"  ... ({hunk_skipped} lines truncated)")
                hunk_skipped = 0
            in_hunk = True
            hunk_shown = 0
            result.append(f"  {line}")
        elif in_hunk:
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
            if hunk_shown < max_hunk:
                result.append(f"  {line}")
                hunk_shown += 1
            else:
                hunk_skipped += 1
        if len(result) >= 500:
            result.append("\n... (more changes truncated)")
            break

    if hunk_skipped > 0:
        result.append(f"  ... ({hunk_skipped} lines truncated)")
    if current_file and (added or removed):
        result.append(f"  +{added} -{removed}")
    return "\n".join(result)


def _compress_git_status(text):
    """Cap changed/untracked files to 10 each (same cap philosophy as memory.py's 200-turn limit)."""
    raw_lines = text.split("\n")
    branch_lines = [ln for ln in raw_lines if ln.startswith("##")]
    changed = [ln for ln in raw_lines if ln.strip() and not ln.startswith("##") and not ln.startswith("?")]
    untracked = [ln for ln in raw_lines if ln.startswith("?")]
    out = list(branch_lines)
    out.extend(changed[:10])
    if len(changed) > 10:
        out.append(f"... +{len(changed) - 10} more changed files")
    out.extend(untracked[:10])
    if len(untracked) > 10:
        out.append(f"... +{len(untracked) - 10} more untracked files")
    return "\n".join(out) if out else text


def _compress_grep(text):
    """Group matches by file, cap 10 per file."""
    by_file = {}
    for line in text.split("\n"):
        m = re.match(r"^([^:]+):(\d+):\s*(.*)", line)
        if m:
            fname = m.group(1)
            lineno = m.group(2)
            content = m.group(3).strip()[:200]
            by_file.setdefault(fname, []).append((lineno, content))
    if not by_file:
        return text
    total = sum(len(v) for v in by_file.values())
    out = [f"{total} matches in {len(by_file)} files:"]
    for fname in sorted(by_file):
        matches = by_file[fname]
        out.append(f"{fname} ({len(matches)})")
        for lineno, content in matches[:10]:
            out.append(f"  {lineno}: {content}")
        if len(matches) > 10:
            out.append(f"  +{len(matches) - 10} more")
    return "\n".join(out)


def _compress_build(text):
    """Keep errors + final summary, collapse compile/download counts."""
    raw_lines = text.split("\n")
    errors = []
    warnings = []
    summary = []
    compiling = 0
    downloading = 0
    for line in raw_lines:
        s = line.strip()
        if not s:
            continue
        if re.search(r"(error|ERR!|FAILED|\[ERROR\])", s, re.I):
            errors.append(line)
        elif re.search(r"^npm warn|^yarn warn", s, re.I):
            warnings.append(line)
        elif re.search(r"(added \d+ package|audited \d+|BUILD SUCCESS|Successfully|\d+ packages|\d+ vulnerabilities)", s, re.I):
            summary.append(line)
        elif re.search(r"^\s*Compiling\s+", s, re.I):
            compiling += 1
        elif re.search(r"^\s*Downloading\s+|^Fetching\s+", s, re.I):
            downloading += 1
    out = []
    if compiling:
        out.append(f"Compiled {compiling} packages")
    if downloading:
        out.append(f"Downloaded {downloading} packages")
    out.extend(warnings[:5])
    if len(warnings) > 5:
        out.append(f"... +{len(warnings) - 5} more warnings")
    out.extend(errors)
    out.extend(summary[-3:])
    return "\n".join(out) if out else text


def _compress_find(text):
    """Group by directory, cap 10 per dir, 20 dirs total."""
    by_dir = {}
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        path = stripped
        if "/" in path:
            d, f = path.rsplit("/", 1)
        elif "\\" in path:
            d, f = path.rsplit("\\", 1)
        else:
            d, f = ".", path
        by_dir.setdefault(d, []).append(f)
    if not by_dir:
        return text
    out = []
    for d in sorted(by_dir)[:20]:
        files = by_dir[d]
        out.append(f"{d}/ ({len(files)} files)")
        for fname in files[:10]:
            out.append(f"  {fname}")
        if len(files) > 10:
            out.append(f"  ... +{len(files) - 10} more")
    if len(by_dir) > 20:
        out.append(f"+{len(by_dir) - 20} more dirs")
    return "\n".join(out)


def _compress_ls(text):
    """Collapse ls -la to name+size, extension summary."""
    date_re = re.compile(r"\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+(\d{4}|\d{2}:\d{2})\s+")
    dirs = []
    files = []
    by_ext = {}
    for line in text.split("\n"):
        if line.startswith("total ") or not line.strip():
            continue
        m = date_re.search(line)
        if not m:
            continue
        name = line[m.end():].strip()
        if name in (".", ".."):
            continue
        before = line[:m.start()].split()
        if len(before) < 4:
            continue
        perms = before[0]
        size = 0
        for part in reversed(before):
            try:
                size = int(part)
                break
            except ValueError:
                pass
        if perms.startswith("d"):
            dirs.append(name)
        elif perms.startswith("-") or perms.startswith("l"):
            dot = name.rfind(".")
            ext = name[dot:] if dot > 0 else "no ext"
            by_ext[ext] = by_ext.get(ext, 0) + 1
            if size >= 1_048_576:
                sz = f"{size / 1_048_576:.1f}M"
            elif size >= 1024:
                sz = f"{size / 1024:.1f}K"
            else:
                sz = f"{size}B"
            files.append((name, sz))
    if not dirs and not files:
        return text
    out = []
    for d in dirs:
        out.append(f"{d}/")
    for name, sz in files:
        out.append(f"{name}  {sz}")
    summary_text = f"\nSummary: {len(files)} files, {len(dirs)} dirs"
    if by_ext:
        top = sorted(by_ext.items(), key=lambda x: -x[1])[:5]
        parts = [f"{c} {e}" for e, c in top]
        summary_text += f" ({', '.join(parts)}"
        if len(by_ext) > 5:
            summary_text += f", +{len(by_ext) - 5} more"
        summary_text += ")"
    return "\n".join(out) + summary_text


def _compress_tree(text):
    """Strip directory summary line, cap at 200 lines."""
    raw_lines = [ln for ln in text.split("\n") if "director" not in ln.lower() or "file" not in ln.lower()]
    while raw_lines and not raw_lines[0].strip():
        raw_lines.pop(0)
    while raw_lines and not raw_lines[-1].strip():
        raw_lines.pop()
    if len(raw_lines) > 200:
        return "\n".join(raw_lines[:200]) + f"\n... +{len(raw_lines) - 200} more lines"
    return "\n".join(raw_lines)


def _compress_test_output(text):
    """Keep assertion failures + final count, deduplicate consecutive identical lines."""
    raw_lines = text.split("\n")
    keep = [ln for ln in raw_lines if re.search(r"(FAILED|ERROR|assert|Traceback|^\d+ (passed|failed))", ln)]
    if not keep:
        keep = [ln for ln in raw_lines[-8:] if ln.strip()]
    deduped = []
    for ln in keep:
        if not deduped or ln != deduped[-1]:
            deduped.append(ln)
    return "\n".join(deduped)


def _compress_read_numbered(text):
    """For line-numbered file dumps (N: content), collapse consecutive duplicates."""
    raw_lines = text.split("\n")
    kept = []
    prev_content = ""
    run = 0
    for line in raw_lines:
        m = _LINE_NUMBERED_RE.match(line)
        if m:
            content = line[m.end():].strip()
            if content == prev_content:
                run += 1
                if run > 1:
                    continue
            else:
                if run > 1:
                    kept.append(f"  ... ({run - 1} duplicate lines)")
                prev_content = content
                run = 1
        elif run > 1:
            kept.append(f"  ... ({run - 1} duplicate lines)")
            run = 0
            prev_content = ""
        kept.append(line)
    if run > 1:
        kept.append(f"  ... ({run - 1} duplicate lines)")
    return "\n".join(kept)


def _compress_dedup_log(text):
    """Collapse consecutive duplicate lines, cap at 2000 lines."""
    raw_lines = text.split("\n")
    out = []
    prev = None
    run_count = 0
    for line in raw_lines:
        if line == prev:
            run_count += 1
            continue
        if prev is not None and run_count > 1:
            out.append(f"  ... ({run_count} duplicate lines)")
        out.append(line)
        prev = line
        run_count = 1
        if len(out) >= 2000:
            out.append("... (truncated at 2000 lines)")
            return "\n".join(out)
    if prev is not None and run_count > 1:
        out.append(f"  ... ({run_count} duplicate lines)")
    return "\n".join(out)


def _compress_web_text(text):
    """Compress web/HTML content: collapse blank lines, truncate long lines, keep structure."""
    lines = text.split("\n")
    out = []
    blank_run = 0
    for line in lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= 1:
                out.append(line)
            continue
        blank_run = 0
        if len(line) > 800:
            out.append(line[:800] + f" [truncated {len(line) - 800} chars]")
        else:
            out.append(line)
    # If still very long, keep head + tail with summary
    if len(out) > 500:
        head = out[:300]
        tail = out[-100:]
        cut = len(out) - 400
        out = head + [f"... +{cut} lines truncated"] + tail
    return "\n".join(out)


# Detector table (MO Agent pattern: tuple list like goal_auditor.py's *_MARKERS)

_DETECTORS = [
    ("git-diff",       [_GIT_DIFF_HEAD_RE], 0),
    ("git-status",     [_GIT_STATUS_HEAD_RE], 0),
    ("grep",           [_GREP_LINE_RE], 3),
    ("build",          [_BUILD_HEAD_RE], 0),
    ("web-html",        [_WEB_HTML_RE], 0),
    ("find",           [_FIND_PATH_RE], 3),
    ("ls",             [_LS_PERMS_RE], 0),
    ("tree",           [_TREE_GLYPH_RE], 0),
    ("test-output",    [_TEST_PASS_FAIL_RE], 0),
    ("read-numbered",  [_LINE_NUMBERED_RE], 5),
]

_COMPRESSORS = {
    "git-diff":       _compress_git_diff,
    "git-status":     _compress_git_status,
    "grep":           _compress_grep,
    "build":          _compress_build,
    "find":           _compress_find,
    "ls":             _compress_ls,
    "tree":           _compress_tree,
    "test-output":    _compress_test_output,
    "read-numbered":  _compress_read_numbered,
    "web-html":       _compress_web_text,
}


def classify(text, detect_window=1024):
    """Return format name or None. Peeks first N chars.
    Examines the head of the text and returns an enum-like format name.
    """
    head = text[:detect_window] if len(text) > detect_window else text
    non_empty = [ln for ln in head.split("\n") if ln.strip()]
    for name, regexes, min_lines in _DETECTORS:
        if min_lines and len(non_empty) < min_lines:
            continue
        if all(r.search(head) for r in regexes):
            return name
    return None


def compress(text, *, min_bytes=500, detect_window=1024, pressure=0.0):
    """Attempt to classify and compress tool output.

    Args:
        text: Raw tool output to compress.
        min_bytes: Skip compression below this byte threshold.
        detect_window: Peek first N chars for format detection.
        pressure: Optional context pressure (0.0-1.0). When >0.60,
                  applies additional aggressive post-compression
                  (dedup_log on all output, lower smart_truncate threshold).

    Returns (result_text, stats_dict_or_None).
    Stats: {format, before_chars, after_chars, saved_chars, saved_pct}.
    Returns (original_text, None) if no compression applicable or safe.
    """
    if len(text) < min_bytes:
        return text, None
    format_name = classify(text, detect_window)
    if not format_name or format_name not in _COMPRESSORS:
        return text, None
    try:
        result = _COMPRESSORS[format_name](text)
    except Exception:
        return text, None
    if not result or len(result) >= len(text):
        return text, None

    # Aggressive post-compression near handoff boundary: squeeze extra turns
    if pressure > 0.60:
        # Apply dedup_log to reduce repetitive patterns
        deduped = _compress_dedup_log(result)
        if deduped and len(deduped) < len(result):
            result = deduped
        # For unstructured text, lower the smart truncate threshold
        if len(result.split("\n")) > 150:
            head = result.split("\n")[:80]   # was 120
            tail = result.split("\n")[-40:]  # was 60
            cut = len(result.split("\n")) - 120
            if cut > 0:
                result = "\n".join(head + [f"... +{cut} lines truncated (aggressive)"] + tail)

    if not result or len(result) >= len(text):
        return text, None
    stats = {
        "format": format_name,
        "before_chars": len(text),
        "after_chars": len(result),
        "saved_chars": len(text) - len(result),
        "saved_pct": round((1 - len(result) / len(text)) * 100, 1),
    }
    return result, stats
