"""MO Profile — lightweight persistent user/project profile.

Stored at memory/mo.db (JSON). Referenced by Gateway and Agent for personalization.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import traceback

from ..utils.atomic_write import atomic_write_json, atomic_write_text
from ..utils.env_utils import int_env
from ..utils.text_utils import cap_by_tokens, token_aware_truncation_enabled


DEFAULT_PROFILE_PATH = "memory/mo.db"


TEMPLATE_FILES = {
    "operator.md": """# Operator Profile

- **Name:** {operator_name}

## Communication
- Use direct, evidence-backed answers.
- Keep routine replies concise.
- Verify files, logs, runtime, and config before making claims.

## Working Style
- Preserve the operator's goal frame.
- Ask only when a missing answer would change the action or risk.
""",
    "thinking_model.md": """# Operator Thinking Model

Purpose: help MO reason with the operator's intent without becoming a yes-man.

## Rules
- Understand the desired outcome.
- Separate vision from implementation risk.
- Verify current reality before proposing new mechanisms.
- Preserve useful nonstandard ideas while challenging weak evidence.
""",
    "terms.md": """# Operator Terms

Add local shorthand terms here when they become durable operator workflow, not temporary chat phrasing.
""",
    "identity.md": """# MO Identity

You are MO. Backend models are runtime providers, not identity.
""",
    "learning.md": """# Operator Learning

Durable insights and preferences learned dynamically from interaction history.
""",
    "behavior.md": """# MO Behavioral Learning

Generated compact behavior rules from explicit operator learning. Applies below the internal system prompt, current user request, tool/sandbox rules, taskboard truth, and direct evidence.
""",
    "facts.md": """# Operator Operational Facts (auto-captured)

Durable facts the operator shared — servers, repos, access, deploy methods, project paths, credential LOCATIONS (never values), and host ALIASES (never raw IPs/SSH connection strings). MO records these autonomously via record_profile_fact.
""",
}


@dataclass
class ProjectEntry:
    path: str
    name: str = ""
    last_opened: float = 0.0
    session_count: int = 0
    notes: str = ""


@dataclass
class Profile:
    """Lightweight user profile persisted as JSON."""

    # Identity
    user_name: str = ""
    user_alias: str = ""

    # Preferences
    preferred_tools: list[str] = field(default_factory=lambda: [
        "read_file", "write_file", "edit_file", "shell", "grep",
        "find_files", "git_status", "test_runner", "web_fetch", "web_snapshot",
    ])
    default_roots: list[str] = field(default_factory=list)
    important_paths: list[str] = field(default_factory=list)
    favorite_provider: str = ""
    favorite_model: str = ""

    # Projects
    projects: dict[str, ProjectEntry] = field(default_factory=dict)

    # Stats
    total_sessions: int = 0
    total_turns: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    created_at: float = 0.0
    last_active: float = 0.0

    # Meta
    _path: str = field(default=DEFAULT_PROFILE_PATH, repr=False)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    # ── persistence ──────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | None = None) -> Profile:
        from ..state.paths import resolve_state_path
        # Route the default through private-state resolution so a default-path
        # Profile lands in ~/.mo (or MO_STATE_HOME), never the project cwd.
        # Explicit absolute paths (what the agent passes in production) are kept.
        path = resolve_state_path(path or DEFAULT_PROFILE_PATH)
        p = Path(path)
        if not p.exists():
            profile = cls(_path=path)
            profile._hydrate_identity_from_operator_profile()
            profile.save()
            return profile
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            profile = cls(_path=path)
            profile._apply_raw(raw)
            if profile._hydrate_identity_from_operator_profile():
                profile.save()
            return profile
        except (json.JSONDecodeError, OSError):
            profile = cls(_path=path)
            profile._hydrate_identity_from_operator_profile()
            profile.save()
            return profile

    def save(self) -> None:
        p = Path(self._path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.last_active = time.time()
        atomic_write_json(p, self._to_raw(), indent=2, ensure_ascii=False)

    def _to_raw(self) -> dict:
        projects_raw = {}
        for key, entry in self.projects.items():
            projects_raw[key] = {
                "path": entry.path,
                "name": entry.name,
                "last_opened": entry.last_opened,
                "session_count": entry.session_count,
                "notes": entry.notes,
            }
        return {
            "user_name": self.user_name,
            "user_alias": self.user_alias,
            "preferred_tools": self.preferred_tools,
            "default_roots": self.default_roots,
            "important_paths": self.important_paths,
            "favorite_provider": self.favorite_provider,
            "favorite_model": self.favorite_model,
            "projects": projects_raw,
            "total_sessions": self.total_sessions,
            "total_turns": self.total_turns,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "created_at": self.created_at,
            "last_active": self.last_active,
        }

    def _apply_raw(self, raw: dict) -> None:
        self.user_name = str(raw.get("user_name") or "")
        self.user_alias = str(raw.get("user_alias") or "")
        self.preferred_tools = raw.get("preferred_tools") or self.preferred_tools
        self.default_roots = raw.get("default_roots") or []
        self.important_paths = raw.get("important_paths") or []
        self.favorite_provider = str(raw.get("favorite_provider") or "")
        self.favorite_model = str(raw.get("favorite_model") or "")
        self.total_sessions = int(raw.get("total_sessions") or 0)
        self.total_turns = int(raw.get("total_turns") or 0)
        self.total_tokens_in = int(raw.get("total_tokens_in") or 0)
        self.total_tokens_out = int(raw.get("total_tokens_out") or 0)
        self.created_at = float(raw.get("created_at") or time.time())
        self.last_active = float(raw.get("last_active") or 0)
        for key, entry in (raw.get("projects") or {}).items():
            self.projects[key] = ProjectEntry(
                path=str(entry.get("path") or ""),
                name=str(entry.get("name") or ""),
                last_opened=float(entry.get("last_opened") or 0),
                session_count=int(entry.get("session_count") or 0),
                notes=str(entry.get("notes") or ""),
            )

    # ── project tracking ─────────────────────────────────────────

    def touch_project(self, project_path: str, name: str = "") -> ProjectEntry:
        key = self._project_key(project_path)
        if key not in self.projects:
            self.projects[key] = ProjectEntry(path=project_path)
        entry = self.projects[key]
        entry.last_opened = time.time()
        entry.session_count += 1
        if name:
            entry.name = name
        if not entry.name:
            entry.name = Path(project_path).name or project_path
        return entry

    @staticmethod
    def _project_key(path: str) -> str:
        return str(Path(path).resolve()).lower()

    def active_project(self) -> ProjectEntry | None:
        if not self.projects:
            return None
        return max(self.projects.values(), key=lambda e: e.last_opened)

    def ensure_operator_profile(self) -> None:
        pdir = Path(self._path).parent / "profile"
        pdir.mkdir(parents=True, exist_ok=True)
        name = self.user_name or "Operator"
        for fname, template in TEMPLATE_FILES.items():
            path = pdir / fname
            if not path.exists():
                atomic_write_text(path, template.format(operator_name=name).strip() + "\n", encoding="utf-8")

    def _hydrate_identity_from_operator_profile(self) -> bool:
        """Backfill the JSON identity from the local markdown profile if needed."""
        if str(self.user_name or "").strip():
            return False
        operator_path = Path(self._path).parent / "profile" / "operator.md"
        name = _read_operator_profile_name(operator_path)
        if not name:
            return False
        self.user_name = name
        self._profile_cache_mtimes = None
        self._profile_cache_text = None
        return True

    def sync_operator_profile_files(self) -> None:
        """Update generated operator identity lines without overwriting custom profile notes."""
        self.ensure_operator_profile()
        name = self.user_name or "Operator"
        pdir = Path(self._path).parent / "profile"
        operator_path = pdir / "operator.md"
        thinking_path = pdir / "thinking_model.md"

        try:
            text = operator_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            if lines:
                lines[0] = f"# Operator Profile — {name}"
            updated = "\n".join(lines)
            if re.search(r"(?m)^- \*\*Name:\*\*\s*.*$", updated):
                updated = re.sub(r"(?m)^- \*\*Name:\*\*\s*.*$", f"- **Name:** {name}", updated, count=1)
            else:
                updated = updated.replace(lines[0], lines[0] + f"\n\n- **Name:** {name}", 1) if lines else f"# Operator Profile — {name}\n\n- **Name:** {name}"
            atomic_write_text(operator_path, updated.rstrip() + "\n", encoding="utf-8")
        except Exception:
            traceback.print_exc()

        try:
            text = thinking_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            if lines and re.match(r"^# .*Thinking Model$", lines[0]):
                lines[0] = f"# {name} Thinking Model"
                atomic_write_text(thinking_path, "\n".join(lines).rstrip() + "\n", encoding="utf-8")
        except Exception:
            traceback.print_exc()

        self._profile_cache_mtimes = None  # invalidate after sync writes
        self._profile_cache_text = None

    def build_profile_context(self, max_chars: int = 3000) -> str:
        if self._hydrate_identity_from_operator_profile():
            self.save()
        self.ensure_operator_profile()
        pdir = Path(self._path).parent / "profile"

        # Cache: skip 5 file reads when nothing changed since last build.
        # Profile files change only on explicit /profile edits or learning events,
        # so mtime-based invalidation is both safe and effective.
        # Inject a compact SUMMARY only — operator essentials, structured project
        # paths, and a pointer to the full files. MO reads the full operator.md on
        # demand with read_file (its profile dir is read-allowed) instead of
        # carrying the whole profile every turn.
        # Order matters: operator identity + terms first so MO has the user's
        # vocabulary even when the summary is truncated at max_chars.
        profile_files = (
            ("operator.md", 1400, False),
            ("terms.md", 450, False),
            ("facts.md", 700, True),
            ("thinking_model.md", 500, False),
            ("behavior.md", 600, False),
            ("learning.md", 700, True),
            ("identity.md", 250, False),
        )
        try:
            mtimes = tuple(
                (pdir / fname).stat().st_mtime if (pdir / fname).exists() else 0.0
                for fname, _limit, _tail in profile_files
            )
        except Exception:
            mtimes = ()
        cache_mtimes = getattr(self, "_profile_cache_mtimes", None)
        if cache_mtimes == mtimes and getattr(self, "_profile_cache_text", None) is not None:
            cached = str(self._profile_cache_text or "")
            if len(cached) <= max_chars:
                return cached
            return _cap_profile_text(cached, max_chars, "[operator profile context truncated]")

        operator_md = pdir / "operator.md"
        lines = [
            "## Active Operator Profile (summary — full detail on demand)",
            f"Current operator: {self.user_name or 'Operator'}",
            "Profile files loaded below are SUMMARIES. For complete operator/project/"
            f"deploy/path detail, read the full file with read_file: {operator_md}. "
            "Never guess a project's location or scan the filesystem for it — read the profile.",
        ]
        if self.preferred_tools:
            lines.append("Preferred tool order: " + ", ".join(self.preferred_tools[:12]))
        if self.favorite_provider:
            lines.append(
                f"Favorite provider/model metadata: {self.favorite_provider} / {self.favorite_model or 'default'} "
                "(non-authoritative; runtime provider lane is config/code owned)"
            )
        if self.default_roots:
            lines.append(
                "Profile default roots metadata (non-authoritative; sandbox roots come from access config/current project): "
                + ", ".join(str(root) for root in self.default_roots[:8])
            )
        if self.projects:
            proj_lines = []
            for key, entry in list(self.projects.items())[:12]:
                name = (getattr(entry, "name", "") or key).strip()
                path = (getattr(entry, "path", "") or key).strip()
                proj_lines.append(f"  - {name}: {path}")
            if proj_lines:
                lines.append(
                    "Known operator project paths (projects MO has opened; verify live repo/runtime state before claims): "
                    "\n" + "\n".join(proj_lines)
                )

        # Profile Index — compact auto-generated map of what each file contains.
        # Always comes BEFORE the per-file excerpts so MO knows what exists even
        # when individual excerpts are truncated.  Tells MO which file to
        # read_file when it needs a term/section that was cut.
        def _file_active(file_name: str) -> bool:
            # facts.md ships as a template header; it should not consume context
            # budget (or an index line) until MO has recorded a real fact entry.
            if file_name != "facts.md":
                return True
            try:
                return "- [" in (pdir / file_name).read_text(encoding="utf-8", errors="replace")
            except Exception:
                return False

        index_entries: list[str] = []
        for file_name, _limit, _tail in profile_files:
            if not _file_active(file_name):
                continue
            entry = _profile_index_line(pdir / file_name)
            if entry:
                index_entries.append(f"- {file_name}: {entry}")
        if index_entries:
            lines.append("### Profile Index (what's where)")
            lines.extend(index_entries)

        def excerpt(path: Path, limit: int, *, include_recent_tail: bool = False) -> str:
            if not path.exists():
                return ""
            try:
                text = path.read_text(encoding="utf-8").strip()
                if len(text) <= limit:
                    return text
                if include_recent_tail:
                    marker = "\n[profile middle truncated — recent learning follows]\n"
                    head_limit = max(120, limit // 3)
                    tail_limit = max(120, limit - head_limit - len(marker))
                    head = text[:head_limit].rsplit("\n", 1)[0].strip() or text[:head_limit].strip()
                    tail = text[-tail_limit:].split("\n", 1)[-1].strip() or text[-tail_limit:].strip()
                    return f"{head}{marker}{tail}"
                return _cap_profile_text(text, limit, "[profile excerpt truncated]")
            except Exception:
                return ""

        for file_name, limit, include_recent_tail in profile_files:
            if not _file_active(file_name):
                continue
            body = excerpt(pdir / file_name, limit, include_recent_tail=include_recent_tail)
            if body:
                lines.append(f"\n### {file_name}\n{body}")
                
        text = "\n".join(lines).strip()
        # Cache for next call — invalidated by mtime check at top of method
        self._profile_cache_mtimes = mtimes
        self._profile_cache_text = text
        if len(text) <= max_chars:
            return text
        return _cap_profile_text(text, max_chars, "[operator profile context truncated]")

    def append_profile_learning(self, source: str, insights: dict[str, Any]) -> None:
        self.ensure_operator_profile()
        pdir = Path(self._path).parent / "profile"
        path = pdir / "learning.md"
        source = str(source or "learning-turn").strip()

        try:
            existing = path.read_text(encoding="utf-8")
        except Exception:
            existing = ""

        marker = f"source: {source}"
        if marker in existing:
            return

        existing_norms = _existing_learning_norms(existing)
        existing_fps = set(re.findall(r"insight:([a-f0-9]{12})", existing))
        new_entries: list[tuple[str, str, str, str]] = []
        for key in ("core_traits", "current_focus", "communication_style", "evolution"):
            value = insights.get(key)
            items = value if isinstance(value, list) else [value]
            for raw in items[:5]:
                clean = _compact_learning_text(raw)
                if not clean:
                    continue
                norm = _normalize_learning_insight(clean)
                fp = _learning_fingerprint(norm)
                if norm in existing_norms or fp in existing_fps:
                    continue
                existing_norms.add(norm)
                existing_fps.add(fp)
                category = _learning_category(key, clean)
                new_entries.append((key, clean, fp, category))

        if not new_entries:
            return

        import datetime
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = [f"\n## {now} — profile learning", f"- source: {source}"]
        for key, clean, fp, category in new_entries:
            lines.append(f"- {key}: {clean} <!-- insight:{fp} category:{category} -->")

        try:
            with path.open("a", encoding="utf-8") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write("\n".join(lines).strip() + "\n")
            _prune_profile_learning(path)
            _append_behavior_learning(pdir / "behavior.md", new_entries)
            self._profile_cache_mtimes = None  # invalidate cache after write
            self._profile_cache_text = None
        except Exception:
            traceback.print_exc()

    # ── stats ────────────────────────────────────────────────────

    def record_session(self, turns: int = 0, tokens_in: int = 0, tokens_out: int = 0) -> None:
        self.total_sessions += 1
        self.total_turns += turns
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.save()

    # ── display ──────────────────────────────────────────────────

    def render(self) -> str:
        lines: list[str] = []
        name_part = ""
        if self.user_name:
            name_part = f" ({self.user_alias})" if self.user_alias else ""
            lines.append(f"User: {self.user_name}{name_part}")
        else:
            lines.append("User: [not set — use /profile name <your-name>]")

        active = self.active_project()
        if active:
            lines.append(f"Active project: {active.name} ({active.path})")
            lines.append(f"  sessions: {active.session_count} | last: {_fmt_time(active.last_opened)}")

        lines.append(f"Stats: {self.total_sessions} sessions · {self.total_turns} turns")
        if self.total_tokens_in or self.total_tokens_out:
            lines.append(f"Tokens: ↑{_fmt_num(self.total_tokens_in)} ↓{_fmt_num(self.total_tokens_out)}")

        if self.favorite_provider:
            lines.append(f"Provider preference metadata: {self.favorite_provider} / {self.favorite_model or 'default'}")

        lines.append(f"Created: {_fmt_time(self.created_at)}")
        return "\n".join(lines)


def _profile_index_line(path: Path) -> str:
    """Build one compact index line from a profile file's ## headers and bold terms.

    Returns a short comma-joined string of section names (or defined terms when
    the file has no ## headers).  This gives MO a MAP of what lives where so it
    can decide to read_file the full file on demand.
    """
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    headers = re.findall(r"^## (.+)$", text, re.MULTILINE)
    if headers:
        return ", ".join(headers)
    # Flat file without sections — list the bold terms (e.g. terms.md)
    terms = []
    for m in re.finditer(r"^\s*-\s*\*\*(.+?)\*\*", text, re.MULTILINE):
        term = m.group(1).strip()
        if term and term not in terms:
            terms.append(term)
    return ", ".join(terms[:10]) if terms else ""


def _read_operator_profile_name(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    patterns = (
        r"(?m)^[ \t]*-[ \t]*\*\*Name:\*\*[ \t]*(.+?)[ \t]*$",
        r"(?m)^[ \t]*#[ \t]+Operator Profile[ \t]+[\u2013\u2014-][ \t]*(.+?)[ \t]*$",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        name = _clean_operator_profile_name(match.group(1))
        if name:
            return name
    return ""


def _clean_operator_profile_name(value: str) -> str:
    name = " ".join(str(value or "").split()).strip(" -:\t")
    name = re.sub(r"^[`*_#>]+|[`*_#>]+$", "", name).strip(" -:\t")
    if not name:
        return ""
    if name.lower() in {"operator", "unknown", "not set", "none", "user"}:
        return ""
    if len(name) > 80 or any(ch in name for ch in "\r\n{}[]="):
        return ""
    if re.search(r"(?i)(token|secret|password|api[_ -]?key|-----BEGIN)", name):
        return ""
    return name


def _cap_profile_text(text: str, limit: int, marker: str) -> str:
    if token_aware_truncation_enabled():
        return cap_by_tokens(text, limit, marker)
    return str(text or "")[:limit].rstrip() + f"\n{marker}"


def _prune_profile_learning(path: Path) -> None:
    max_entries = int_env("MO_PROFILE_LEARNING_MAX_ENTRIES", 200)
    if max_entries <= 0:
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        matches = list(re.finditer(r"(?m)^## \S+T\S+Z\s+—\s+profile learning", text))
        if len(matches) <= max_entries:
            return
        prefix = text[:matches[0].start()].rstrip()
        body = text[matches[-max_entries].start():].strip()
        atomic_write_text(path, f"{prefix}\n\n{body}\n", encoding="utf-8")
    except Exception:
        return


def _prune_behavior_learning(path: Path) -> None:
    max_entries = int_env("MO_PROFILE_BEHAVIOR_MAX_ENTRIES", 100)
    if max_entries <= 0:
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        rules = [line for line in lines if line.startswith("- [")]
        if len(rules) <= max_entries:
            return
        kept = [line for line in lines if not line.startswith("- [")] + rules[-max_entries:]
        atomic_write_text(path, "\n".join(kept).rstrip() + "\n", encoding="utf-8")
    except Exception:
        return


def _compact_learning_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_learning_insight(value: str) -> str:
    text = re.sub(r"<!--.*?-->", "", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = text.strip(" .;:-")
    return text


def _learning_fingerprint(norm: str) -> str:
    import hashlib
    return hashlib.sha1(str(norm or "").encode("utf-8", errors="ignore")).hexdigest()[:12]


def _existing_learning_norms(existing: str) -> set[str]:
    norms: set[str] = set()
    for match in re.finditer(r"(?m)^-\s*(?:core_traits|current_focus|communication_style|evolution):\s*(.+)$", str(existing or "")):
        body = re.sub(r"<!--.*?-->", "", match.group(1)).strip()
        for part in _split_learning_items(body):
            norm = _normalize_learning_insight(part)
            if norm:
                norms.add(norm)
    return norms


def _split_learning_items(value: str) -> list[str]:
    parts = [part.strip() for part in str(value or "").split(";")]
    return [part for part in parts if part]


def _learning_category(key: str, text: str) -> str:
    low = str(text or "").lower()
    if any(word in low for word in ("evidence", "verify", "verified", "test", "logs", "runtime", "files")):
        return "evidence"
    if any(word in low for word in ("scope", "goal", "task", "lane")):
        return "scope"
    if str(key) == "communication_style" or any(word in low for word in ("tone", "wording", "language", "concise", "brief")):
        return "communication"
    if any(word in low for word in ("workflow", "process", "method", "from now on", "next time")):
        return "workflow"
    if str(key) == "current_focus":
        return "focus"
    return "behavior"


def _append_behavior_learning(path: Path, entries: list[tuple[str, str, str, str]]) -> None:
    if not entries:
        return
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else TEMPLATE_FILES["behavior.md"].strip() + "\n"
    except OSError:
        existing = TEMPLATE_FILES["behavior.md"].strip() + "\n"
    updated = existing.rstrip()
    if "## Active Learned Rules" not in updated:
        updated += "\n\n## Active Learned Rules"
    changed = False
    for _key, clean, fp, category in entries:
        if f"insight:{fp}" in updated:
            continue
        updated += f"\n- [{category}] {clean} <!-- insight:{fp} -->"
        changed = True
    if changed:
        atomic_write_text(path, updated.rstrip() + "\n", encoding="utf-8")
        _prune_behavior_learning(path)


def format_profile_time(ts: float) -> str:
    if not ts or ts <= 0:
        return "—"
    import datetime
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_time(ts: float) -> str:
    return format_profile_time(ts)


def _fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)
