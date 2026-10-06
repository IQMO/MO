"""MO profile access for structured metadata and curated operator prose.

Structured runtime metadata lives in ``memory/mo.db``; distinct curated sources
live under ``memory/profile/`` and are assembled into a bounded context capsule.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import traceback

from ..runtime.lock import file_byte_lock
from ..state.paths import PROFILE_DB_PATH, PROFILE_PROSE_FILES, PROFILE_PROSE_ROLES, mo_home
from ..utils.atomic_write import atomic_write_json, atomic_write_text
from ..utils.env_utils import int_env


DEFAULT_PROFILE_PATH = PROFILE_DB_PATH

# ponytail: profile writes are rare; split this by path only if contention is measured.
_PROFILE_THREAD_LOCK = threading.Lock()

_NAME_INTRO_RE = re.compile(
    r"\b(?:my\s+name\s+is|i\s*['’]?\s*am|i\s*['’]\s*m|call\s+me|you\s+can\s+call\s+me)\s+"
    r"([A-Za-z][A-Za-z'’-]{1,19})\b",
    re.I,
)
_NAME_STOPWORDS = {
    "not", "done", "sure", "here", "going", "trying", "just", "working", "looking", "sorry",
    "afraid", "good", "fine", "back", "ready", "still", "now", "also", "really", "about",
    "able", "glad", "happy", "curious", "the", "a", "an", "mo", "so", "very", "only",
}


def capture_operator_name(profile: Any, user_text: str) -> str:
    """Persist one explicit self-introduction through the profile owner."""
    if not profile or str(getattr(profile, "user_name", "") or "").strip():
        return ""
    match = _NAME_INTRO_RE.search(str(user_text or ""))
    if not match:
        return ""
    name = match.group(1).strip(" '’-")
    if len(name) < 2 or not name[0].isalpha() or not name[0].isupper():
        return ""
    if name.lower() in _NAME_STOPWORDS:
        return ""
    try:
        profile.user_name = name
        if hasattr(profile, "sync_operator_profile_files"):
            profile.sync_operator_profile_files()
        profile.save()
    except Exception:
        return ""
    return name


def is_trackable_project_path(path: str | Path) -> bool:
    """Return false for OS runtime directories that are not user projects."""
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    if not str(path or "").strip() or resolved.parent == resolved:
        return False
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or "C:/Windows").resolve(strict=False)
        try:
            resolved.relative_to(system_root)
            return False
        except ValueError:
            return True
    return not any(resolved == root or root in resolved.parents for root in map(Path, ("/proc", "/sys", "/dev")))


def is_user_project_path(path: str | Path, *, runtime_home: str | Path | None = None) -> bool:
    """A folder the user works in: trackable, not their home folder (or above it), and not inside
    MO's own runtime home, where MO's state and its installed checkout live."""
    if not is_trackable_project_path(path):
        return False
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
        home = Path.home().resolve(strict=False)
        runtime = Path(runtime_home or mo_home()).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    return not (resolved == home or resolved in home.parents
                or resolved == runtime or runtime in resolved.parents)


def _trackable_projects(raw: Any) -> dict[str, Any]:
    projects = raw if isinstance(raw, dict) else {}
    return {
        str(key): entry
        for key, entry in projects.items()
        if isinstance(entry, dict) and is_user_project_path(str(entry.get("path") or ""))
    }


@contextmanager
def _profile_file_lock(path: Path):
    """Serialize profile transactions across threads and local processes."""
    lock_path = path.with_name(path.name + ".lock")
    with file_byte_lock(lock_path, _PROFILE_THREAD_LOCK):
        yield


@contextmanager
def profile_transaction_lock(profile_path: str | Path):
    """One cross-process lock for every JSON/prose profile transaction."""
    path = Path(profile_path)
    if path.name != "mo.db":
        if path.name == "profile":
            path = path.parent / "mo.db"
        elif path.parent.name == "profile":
            path = path.parent.parent / "mo.db"
    with _profile_file_lock(path):
        yield


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
    "learning.md": """# Operator Learning

Historical learning-event ledger. Active accepted behavior is projected into behavior.md.
""",
    "behavior.md": """# MO Behavioral Learning

Generated compact behavior rules from explicit operator learning. Applies below the internal system prompt, current user request, tool/sandbox rules, taskboard truth, and direct evidence.
""",
    "facts.md": """# Operator Operational Facts (auto-captured)

Durable facts the operator shared — servers, repos, access, deploy methods, project paths, credential LOCATIONS (never values), and host ALIASES (never raw IPs/SSH connection strings). MO records these autonomously via record_profile_fact.
""",
}
_OPERATOR_TERM_DEFINITION_RE = re.compile(
    r"(?m)^\s*-\s+\*\*([^*]+)\*\*\s*(?:—|-|:)\s*(.+?)\s*$"
)


@dataclass
class ProjectEntry:
    path: str
    name: str = ""
    last_opened: float = 0.0
    session_count: int = 0
    notes: str = ""


def active_project_relative_path(profile: Any, project_root: str | Path) -> str:
    """Return the active profile project relative to this graph root."""
    try:
        active = profile.active_project() if profile else None
        raw_path = str(getattr(active, "path", "") or "").strip()
        if not raw_path:
            return ""
        active_path = Path(raw_path).resolve(strict=False)
        relative = active_path.relative_to(Path(project_root).resolve(strict=False)).as_posix()
    except (AttributeError, OSError, ValueError):
        return ""
    return "" if relative == "." else relative.lower().strip("/")


@dataclass
class Profile:
    """Lightweight user profile persisted as JSON."""

    # Identity
    user_name: str = ""
    user_alias: str = ""

    # Onboarding
    onboarding_offered: bool = False

    # Preferences
    preferred_tools: list[str] = field(default_factory=list)
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
    _baseline_raw: dict[str, Any] = field(default_factory=dict, repr=False)

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
            # A normalization save must merge from the snapshot just loaded.
            # Otherwise aggregate counters look like fresh local deltas and double.
            profile._baseline_raw = copy.deepcopy(raw)
            projects_pruned = len(_trackable_projects(raw.get("projects"))) != len(raw.get("projects") or {})
            if profile._hydrate_identity_from_operator_profile() or projects_pruned:
                profile.save()
            else:
                profile._baseline_raw = copy.deepcopy(profile._to_raw())
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
        with _profile_file_lock(p):
            current: dict[str, Any] = {}
            if p.is_file():
                try:
                    loaded = json.loads(p.read_text(encoding="utf-8"))
                    current = loaded if isinstance(loaded, dict) else {}
                except (json.JSONDecodeError, OSError):
                    current = {}
            merged = self._merge_raw(current, self._to_raw(), self._baseline_raw)
            atomic_write_json(p, merged, indent=2, ensure_ascii=False)
            self._apply_raw(merged)
            self._baseline_raw = copy.deepcopy(merged)

    @staticmethod
    def _merge_raw(current: dict[str, Any], local: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
        """Merge this instance's changes into the latest on-disk profile."""
        if not current:
            return copy.deepcopy(local)
        merged = copy.deepcopy(current)
        for key in (
            "user_name", "user_alias", "onboarding_offered", "preferred_tools",
            "default_roots", "important_paths", "favorite_provider", "favorite_model",
        ):
            if key not in baseline or local.get(key) != baseline.get(key):
                merged[key] = copy.deepcopy(local.get(key))

        for key in ("total_sessions", "total_turns", "total_tokens_in", "total_tokens_out"):
            base_value = int(baseline.get(key) or 0)
            delta = int(local.get(key) or 0) - base_value
            merged[key] = max(0, int(current.get(key) or base_value) + delta)

        created = [float(value or 0) for value in (current.get("created_at"), local.get("created_at")) if value]
        merged["created_at"] = min(created) if created else time.time()
        merged["last_active"] = max(float(current.get("last_active") or 0), float(local.get("last_active") or 0))

        current_projects = copy.deepcopy(_trackable_projects(current.get("projects")))
        baseline_projects = _trackable_projects(baseline.get("projects"))
        local_projects = _trackable_projects(local.get("projects"))
        for key, local_entry in local_projects.items():
            if not isinstance(local_entry, dict):
                continue
            base_entry = baseline_projects.get(key) if isinstance(baseline_projects.get(key), dict) else {}
            current_entry = current_projects.get(key) if isinstance(current_projects.get(key), dict) else {}
            base_count = int(base_entry.get("session_count") or 0)
            delta = int(local_entry.get("session_count") or 0) - base_count
            merged_entry = copy.deepcopy(current_entry)
            merged_entry["path"] = str(local_entry.get("path") or current_entry.get("path") or "")
            for field_name in ("name", "notes"):
                if not current_entry or local_entry.get(field_name) != base_entry.get(field_name):
                    merged_entry[field_name] = str(local_entry.get(field_name) or "")
            merged_entry["last_opened"] = max(
                float(current_entry.get("last_opened") or 0),
                float(local_entry.get("last_opened") or 0),
            )
            merged_entry["session_count"] = max(
                0,
                int(current_entry.get("session_count") or base_count) + delta,
            )
            current_projects[key] = merged_entry
        merged["projects"] = current_projects
        return merged

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
            "onboarding_offered": self.onboarding_offered,
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
        self.onboarding_offered = bool(raw.get("onboarding_offered") or False)
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
        self.projects.clear()
        for key, entry in _trackable_projects(raw.get("projects")).items():
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
        self.save()
        return self.projects[key]

    @staticmethod
    def _project_key(path: str) -> str:
        return str(Path(path).resolve()).lower()

    def active_project(self) -> ProjectEntry | None:
        if not self.projects:
            return None
        return max(self.projects.values(), key=lambda e: e.last_opened)

    def project_locations(self) -> tuple[ProjectEntry, ...]:
        """Project named projects and local directories from curated declarations.

        Recent launch folders are deliberately not ownership declarations. Read
        named entries under operator.md's Projects sections and project/repo
        facts through their existing owner; keep no second persisted catalog.
        Explicitly named projects remain visible without a launch location.
        Named personal locations are opaque declarations, never inspected here.
        """
        from .facts import list_profile_facts
        from ..state.paths import mo_home

        pdir = Path(self._path).parent / "profile"
        try:
            operator = (pdir / "operator.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            operator = ""
        declarations: list[tuple[str, str]] = []
        project_section = False
        for line in operator.splitlines():
            if line.startswith("## "):
                project_section = bool(re.search(r"\bprojects?\b", line, re.I))
            elif project_section and (match := _OPERATOR_TERM_DEFINITION_RE.fullmatch(line)):
                declarations.append(match.groups())
        for item in list_profile_facts(profile=self):
            if item.category in {"project", "repo"}:
                match = _OPERATOR_TERM_DEFINITION_RE.fullmatch("- " + item.fact)
                declarations.append(match.groups() if match else ("", item.fact))
        opaque_roots = (mo_home() / "personal", pdir.parent.parent / "personal")
        locations: dict[str, ProjectEntry] = {}
        unlocated: dict[str, ProjectEntry] = {}
        located_names: set[str] = set()
        for name, text in declarations:
            if name:
                unlocated.setdefault(name.casefold(), ProjectEntry(path="", name=name))
            # Backticks preserve spaces. Unquoted locations end at prose
            # separators. A name without a native absolute path is not guessed.
            for match in re.finditer(r"`([^`\r\n]+)`|(?<!\S)((?:[A-Za-z]:[\\/]|/|~/)[^\s`;,]+)", text):
                raw = match.group(1) or match.group(2).rstrip(".)")
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    continue
                path = Path(os.path.abspath(path))
                opaque = any(path == root or root in path.parents for root in opaque_roots)
                if opaque:
                    # An operator-named project may point into personal state.
                    # Display that declaration without resolving/statting it or
                    # discovering anything else beneath the opaque root.
                    if not name:
                        continue
                    locations.setdefault(os.path.normcase(str(path)),
                                         ProjectEntry(path=str(path), name=name))
                    located_names.add(name.casefold())
                    break
                try:
                    path = path.resolve()
                    if (any(path == root or root in path.parents for root in opaque_roots)
                            or not is_user_project_path(path) or not path.is_dir()):
                        continue
                except (OSError, RuntimeError, ValueError):
                    continue
                key = os.path.normcase(str(path))
                locations.setdefault(key, ProjectEntry(path=str(path), name=name or path.name or str(path)))
                located_names.add(name.casefold())
                break
        located_names.update(entry.name.casefold() for entry in locations.values())
        return (*locations.values(), *(entry for key, entry in unlocated.items() if key not in located_names))

    def referenced_work_path(self, path: str) -> Path | None:
        """Resolve an exact work-file pointer declared in the curated profile.

        Work reports stay in their existing domain; this neither indexes their
        contents nor grants directory, personal-state, or write access.
        """
        root = (Path(self._path).parent / "work").resolve()
        target = Path(os.path.abspath(Path(path).expanduser()))
        if root not in target.parents:
            return None
        target = target.resolve()
        if root not in target.parents:
            return None
        pdir = Path(self._path).parent / "profile"
        for name in PROFILE_PROSE_FILES:
            try:
                text = (pdir / name).read_text(encoding="utf-8")
            except OSError:
                continue
            for match in re.finditer(r"`([^`\r\n]+)`|(?<![\w])((?:[A-Za-z]:[\\/]|/|~/)[^\s`<>;,]+)", text):
                raw = match.group(1) or match.group(2).rstrip(".)")
                normalized = raw.replace("\\", "/")
                candidate = root.parent.parent / normalized[6:] if normalized.startswith("~/.mo/") else Path(raw).expanduser()
                # Check the lexical domain before following links; an opaque
                # personal path must not even be inspected as a candidate.
                if not candidate.is_absolute() or root not in candidate.parents:
                    continue
                if candidate.resolve() == target and target.is_file():
                    return target
        return None

    # ── onboarding ───────────────────────────────────────────────

    def needs_onboarding(self) -> bool:
        """True for a brand-new operator MO hasn't been introduced to yet.

        Fires only while no operator name is set and the one-time first-contact
        offer hasn't been surfaced. Returning users (named, or already offered)
        never see it again.
        """
        return not str(self.user_name or "").strip() and not self.onboarding_offered

    def mark_onboarding_offered(self) -> None:
        """Record that the one-time personalization offer has been surfaced."""
        if not self.onboarding_offered:
            self.onboarding_offered = True
            self.save()

    def ensure_operator_profile(self) -> None:
        pdir = Path(self._path).parent / "profile"
        pdir.mkdir(parents=True, exist_ok=True)
        name = self.user_name or "Operator"
        for fname in PROFILE_PROSE_FILES:
            template = TEMPLATE_FILES[fname]
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
        self._profile_cache_text = None
        return True

    def sync_operator_profile_files(self) -> None:
        """Update generated operator identity lines without overwriting custom profile notes."""
        with profile_transaction_lock(self._path):
            self._sync_operator_profile_files_unlocked()

    def _sync_operator_profile_files_unlocked(self) -> None:
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

        self._profile_cache_text = None

    def matching_term_definitions(
        self,
        user_input: str,
        *,
        limit: int | None = None,
    ) -> tuple[tuple[str, str], ...]:
        """Return canonical profile-term definitions explicitly used in input."""
        text = str(user_input or "")
        remaining = None if limit is None else max(0, int(limit))
        if not text or remaining == 0:
            return ()
        terms_path = Path(self._path).parent / "profile" / "terms.md"
        try:
            terms_text = terms_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ()

        matches: list[tuple[str, str]] = []
        seen: set[str] = set()
        for match in _OPERATOR_TERM_DEFINITION_RE.finditer(terms_text):
            term = str(match.group(1) or "").strip()
            definition = str(match.group(2) or "").strip()
            key = term.casefold()
            if not term or not definition or key in seen:
                continue
            if not re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I):
                continue
            matches.append((term, definition))
            seen.add(key)
            if remaining is not None and len(matches) >= remaining:
                break
        return tuple(matches)

    def build_profile_context(self, max_chars: int = 3000, *, query: str = "", policy: str = "work") -> str:
        if self._hydrate_identity_from_operator_profile():
            self.save()
        self.ensure_operator_profile()
        pdir = Path(self._path).parent / "profile"

        # Cache: skip active profile-file reads when nothing changed since last build.
        # Profile files change only on explicit /profile edits or learning events,
        # so mtime-based invalidation is both safe and effective.
        # Inject a compact SUMMARY only — operator essentials, structured project
        # paths, and a pointer to the full files. MO reads the full operator.md on
        # demand with read_file (its profile dir is read-allowed) instead of
        # carrying the whole profile every turn.
        # Order matters: operator identity + terms first so MO has the user's
        # vocabulary even when the summary is truncated at max_chars.
        profile_limits = {
            "operator.md": (1400, False),
            "terms.md": (450, False),
            "facts.md": (700, True),
            "thinking_model.md": (500, False),
            "behavior.md": (600, False),
            "learning.md": (700, True),
        }
        profile_files = tuple(
            (name, *limits) for name, limits in profile_limits.items()
        )
        query_text = " ".join(str(query or "").lower().split())
        matched_terms = self.matching_term_definitions(query)
        preferred: list[str] = []
        fact_query_needles = ("project", "server", "deploy", "credential", "repo", "account", "path")
        for file_name, needles in (
            ("terms.md", ("mean", "term", "shorthand", "definition")),
            ("facts.md", fact_query_needles),
            ("thinking_model.md", ("think", "reason", "decision", "approach")),
            ("behavior.md", ("prefer", "style", "communicat", "respond")),
            ("learning.md", ("learn", "correction", "feedback", "remember")),
        ):
            if any(needle in query_text for needle in needles):
                preferred.append(file_name)
        policy_name = str(policy or "work").lower()
        lookup_policy = policy_name == "lookup"
        conversation_policy = policy_name in {"chat", "lookup"}
        profile_policy = policy_name == "profile"
        if conversation_policy:
            preferred = [name for name in preferred if name != "facts.md"]
            preferred.extend(("operator.md", "behavior.md", "thinking_model.md", "terms.md"))
            order = list(dict.fromkeys(preferred))
        elif profile_policy:
            # A question about the operator is not ordinary project work. Keep
            # curated identity and operational facts ahead of recent workspace
            # metadata so opened scratch repos cannot masquerade as the user's
            # authoritative project inventory.
            preferred.extend((
                "operator.md", "facts.md", "thinking_model.md", "behavior.md",
                "learning.md", "terms.md",
            ))
            order = list(dict.fromkeys(preferred))
        else:
            # behavior.md is the compact active projection of accepted learning.
            # learning.md remains the durable event ledger and enters context only
            # for an explicit learning/profile lookup, never as a second always-on
            # copy of the same rules.
            active_names = [item[0] for item in profile_files if item[0] != "learning.md"]
            order = list(dict.fromkeys(preferred + active_names))
        by_name = {item[0]: item for item in profile_files}
        profile_files = tuple(by_name[name] for name in order)
        try:
            mtime_files = [fname for fname, _limit, _tail in profile_files]
            if conversation_policy and "facts.md" not in mtime_files:
                mtime_files.append("facts.md")
            mtimes = tuple(
                (pdir / fname).stat().st_mtime if (pdir / fname).exists() else 0.0
                for fname in mtime_files
            )
        except Exception:
            mtimes = ()
        query_terms = _profile_query_terms(query_text)
        fact_lookup_terms = (
            _profile_fact_lookup_terms(query_text)
            if lookup_policy or profile_policy
            else ()
        )
        cache_key = (
            mtimes,
            tuple(order),
            policy_name,
            int(max_chars or 0),
            query_terms,
            fact_lookup_terms,
            matched_terms,
        )
        cache_mtimes = getattr(self, "_profile_cache_key", None)
        if cache_mtimes == cache_key and getattr(self, "_profile_cache_text", None) is not None:
            cached = str(self._profile_cache_text or "")
            if len(cached) <= max_chars:
                return cached
            return _cap_profile_text(cached, max_chars, "[operator profile context truncated]")

        operator_md = pdir / "operator.md"
        lines = [
            "## Active Operator Profile (bounded summary)",
            f"Current operator: {self.user_name or 'Operator'}",
            f"For omitted detail, read the full file with read_file under {operator_md.parent}; never guess or scan for operator facts.",
            "This bounded capsule is not proof that an omitted fact is absent. Read the relevant profile file before making an absence claim.",
            "Source contract: the six memory/profile files have distinct roles; memory/mo.db holds structured identity, recent-workspace, preference, and usage metadata, not a second prose profile.",
        ]
        if matched_terms:
            term_lines = "\n".join(
                f"- **{term}** — {definition}"
                for term, definition in matched_terms
            )
            lines.insert(
                2,
                "\n### terms.md (explicitly used operator terms)\n"
                f"{term_lines}",
            )

        # Conversation capsules prioritize explicit preference facts ahead of
        # longer excerpts so tight budgets cannot hide their truncation marker.
        from .facts import list_profile_facts

        profile_facts = list_profile_facts(profile=self)
        # A requested operational fact must survive a populated profile's
        # general preferences and explanatory index at the normal lookup budget.
        operational_facts = ""
        if lookup_policy or profile_policy:
            operational_facts = _cap_profile_text(
                _query_matched_profile_fact_lines(profile_facts, fact_lookup_terms),
                max(120, min(900, int(max_chars or 3000) // 2)),
                "[query-matched operational facts truncated]",
            )
            if operational_facts:
                lines.insert(
                    4 if matched_terms else 3,
                    "\n### facts.md (query-matched operational facts)\n"
                    f"{operational_facts}",
                )
        # "How many projects do I have?" needs the whole declared inventory; the bounded
        # operator.md excerpt below names only the first few and cannot be counted.
        declared_projects = ""
        if (conversation_policy or profile_policy) and "project" in query_text:
            try:
                names = [entry.name for entry in self.project_locations() if entry.name]
            except Exception:
                names = []
            if names:
                declared_projects = _cap_profile_text(
                    f"Declared projects ({len(names)}): " + ", ".join(names),
                    max(120, min(600, int(max_chars or 3000) // 4)),
                    "[declared projects truncated]",
                )
                lines.insert(
                    (4 if matched_terms else 3) + bool(operational_facts),
                    "\n### Declared projects (operator.md Projects sections and project facts)\n"
                    f"{declared_projects}",
                )
        if conversation_policy:
            preference_facts = _cap_profile_text(
                "\n".join(
                    f"- [preference] {item.fact}"
                    for item in profile_facts
                    if item.category == "preference"
                ),
                max(120, min(700, int(max_chars or 3000) // 4)),
                "[preference facts truncated]",
            )
            if preference_facts:
                preference_index = (4 if matched_terms else 3) + bool(operational_facts)
                lines.insert(
                    preference_index,
                    f"\n### facts.md (preference facts only)\n{preference_facts}",
                )

        # behavior.md is the active projection of accepted operator learning.
        # Give its complete, query-ranked rules a dedicated early claim on the
        # capsule instead of letting workspace metadata or the generic balanced
        # excerpt truncate the appended Active Learned Rules section.
        behavior_rules = ""
        behavior_path = pdir / "behavior.md"
        try:
            behavior_text = behavior_path.read_text(encoding="utf-8", errors="replace")
            behavior_rules = active_behavior_rules_excerpt(
                behavior_text,
                query_terms=query_terms,
                policy=policy_name,
                max_chars=max(240, min(700, int(max_chars or 3000) // 3)),
            )
        except OSError:
            behavior_rules = ""
        if behavior_rules:
            lines.append(f"\n### behavior.md\n{behavior_rules}")
        if self.preferred_tools and not (conversation_policy or profile_policy):
            lines.append("Preferred tools when suitable: " + ", ".join(self.preferred_tools[:8]))
        if self.favorite_provider and not (conversation_policy or profile_policy):
            lines.append(
                f"Favorite provider/model metadata: {self.favorite_provider} / {self.favorite_model or 'default'} "
                "(non-authoritative; runtime provider lane is config/code owned)"
            )
        if self.default_roots and not (conversation_policy or profile_policy):
            lines.append(
                "Profile default roots metadata (non-authoritative; sandbox roots come from access config/current project): "
                + ", ".join(str(root) for root in self.default_roots[:8])
            )
        if self.projects and not (conversation_policy or profile_policy):
            # The profile DB is navigation metadata, not a second project
            # inventory. Inject only the current workspace; old worktrees and
            # incidental launch directories otherwise crowd out curated facts.
            active = self.active_project()
            if active is not None:
                name = (active.name or active.path).strip()
                path = active.path.strip()
                lines.append(
                    "Active MO workspace path (navigation metadata only; not project ownership; verify live repo/runtime state before claims): "
                    f"\n  - {name}: {path}"
                )

        work_facts = ""
        if policy_name == "work":
            work_facts = _query_matched_work_fact_lines(profile_facts, query_terms)
            work_facts = _cap_profile_text(
                work_facts,
                max(120, min(900, int(max_chars or 3000) // 3)),
                "[query-matched work facts truncated]",
            )
            if work_facts:
                lines.append(f"\n### facts.md (query-matched work facts)\n{work_facts}")

        # Profile Index — compact auto-generated map of what each file contains.
        # Query-matched facts above get first claim on the bounded context; the
        # index still directs MO to omitted files without pretending completeness.
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
            if file_name == "terms.md" and matched_terms:
                continue
            if not _file_active(file_name):
                continue
            role = PROFILE_PROSE_ROLES.get(file_name, "profile prose")
            entry = _profile_index_line(pdir / file_name)
            detail = f" — {entry}" if entry else ""
            index_entries.append(f"- {file_name}: {role}{detail}")
        # At chat-sized budgets, source excerpts already identify their owner and
        # the duplicate index can crowd the last source out of the hard cap.
        # Keep the role map for normal contexts; prefer actual source evidence in
        # tight capsules.
        if index_entries and int(max_chars or 3000) >= 1600:
            lines.append("### Profile Index (what's where)")
            index_budget = max(120, min(600, int(max_chars or 3000) // 4))
            index_text = _cap_profile_text(
                "\n".join(index_entries),
                index_budget,
                "[profile index truncated]",
            )
            lines.extend(index_text.splitlines())

        def excerpt(path: Path, limit: int, *, include_recent_tail: bool = False) -> str:
            if not path.exists():
                return ""
            try:
                text = path.read_text(encoding="utf-8").strip()
                relevant = _relevant_profile_excerpt(text, query_terms)
                if relevant:
                    return _cap_profile_text(relevant, limit, "[relevant profile excerpt truncated]")
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

        active_files = [
            item
            for item in profile_files
            if _file_active(item[0])
            and (pdir / item[0]).exists()
            and not (item[0] == "facts.md" and (operational_facts or work_facts))
            and not (item[0] == "behavior.md" and behavior_rules)
            and not (item[0] == "terms.md" and matched_terms)
        ]
        heading_chars = sum(len(item[0]) + 9 for item in active_files)
        available = max(0, int(max_chars or 3000) - len("\n".join(lines)) - heading_chars)
        balanced_limit = max(90, available // max(1, len(active_files)))
        for file_name, limit, include_recent_tail in active_files:
            body = excerpt(pdir / file_name, min(limit, balanced_limit), include_recent_tail=include_recent_tail)
            if body:
                lines.append(f"\n### {file_name}\n{body}")
                
        text = "\n".join(lines).strip()
        # Cache for next call — invalidated by mtime check at top of method
        self._profile_cache_key = cache_key
        self._profile_cache_text = text
        if len(text) <= max_chars:
            return text
        return _cap_profile_text(text, max_chars, "[operator profile context truncated]")

    def reconcile_profile_learning(self) -> dict[str, int]:
        """Remove redundant generated profile insights while preserving one authority."""
        with profile_transaction_lock(self._path):
            self.ensure_operator_profile()
            pdir = Path(self._path).parent / "profile"
            learning_path = pdir / "learning.md"
            behavior_path = pdir / "behavior.md"
            learning_text = learning_path.read_text(encoding="utf-8", errors="replace")
            behavior_text = behavior_path.read_text(encoding="utf-8", errors="replace")
            learning_text, audit = _deduplicate_profile_learning_text(learning_text)
            behavior_text, behavior_removed = _deduplicate_behavior_learning_text(behavior_text)
            if audit["duplicates_removed"]:
                atomic_write_text(learning_path, learning_text, encoding="utf-8")
            if behavior_removed:
                atomic_write_text(behavior_path, behavior_text, encoding="utf-8")
            if audit["duplicates_removed"] or behavior_removed:
                self._profile_cache_text = None
            return audit

    def append_profile_learning(self, source: str, insights: dict[str, Any]) -> bool:
        with profile_transaction_lock(self._path):
            return self._append_profile_learning_unlocked(source, insights)

    def _append_profile_learning_unlocked(self, source: str, insights: dict[str, Any]) -> bool:
        self.ensure_operator_profile()
        pdir = Path(self._path).parent / "profile"
        path = pdir / "learning.md"
        source = str(source or "learning-turn").strip()

        try:
            existing = path.read_text(encoding="utf-8")
        except Exception:
            existing = ""
        existing, audit = _deduplicate_profile_learning_text(existing)
        if audit["duplicates_removed"]:
            atomic_write_text(path, existing, encoding="utf-8")
            behavior_path = pdir / "behavior.md"
            behavior_text = behavior_path.read_text(encoding="utf-8", errors="replace")
            behavior_text, behavior_removed = _deduplicate_behavior_learning_text(behavior_text)
            if behavior_removed:
                atomic_write_text(behavior_path, behavior_text, encoding="utf-8")
            self._profile_cache_text = None

        marker = f"source: {source}"
        if marker in existing:
            return False

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
                if (
                    fp in existing_fps
                    or any(_learning_insights_duplicate(norm, prior) for prior in existing_norms)
                ):
                    continue
                existing_norms.add(norm)
                existing_fps.add(fp)
                category = _learning_category(key, clean)
                new_entries.append((key, clean, fp, category))

        if not new_entries:
            return False

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
            self._profile_cache_text = None
            return True
        except Exception:
            traceback.print_exc()
            return False

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
    """Build one compact index line from a profile file's ## headers, bold terms,
    and fact-like [category] entries.

    Always extracts **bold** defined terms so critical operator vocabulary and
    short workflow codenames survive context-bridge truncation.  The index tells
    MO the MAP so it can decide to read_file the full file on demand.
    """
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    headers = re.findall(r"^## (.+)$", text, re.MULTILINE)
    # Extract bold defined terms from ALL files, not just headerless ones.
    terms: list[str] = []
    for m in re.finditer(r"^\s*-\s*\*\*(.+?)\*\*", text, re.MULTILINE):
        term = m.group(1).strip()
        if term and term not in terms:
            terms.append(term)
    # facts.md uses - [category] not **bold** — extract those too.
    fact_categories: list[str] = []
    for m in re.finditer(r"^\s*-\s*\[(.+?)\]", text, re.MULTILINE):
        cat = m.group(1).strip().rstrip(":")
        if cat and cat not in fact_categories:
            fact_categories.append(cat)
    parts: list[str] = []
    if headers:
        parts.extend(headers)
    if terms:
        parts.append("terms: " + ", ".join(terms))
    if fact_categories:
        parts.append("facts: " + ", ".join(fact_categories))
    if parts:
        return " — ".join(parts)
    return ""


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
    value = str(text or "")
    cap = max(0, int(limit or 0))
    if len(value) <= cap:
        return value
    suffix = f"\n{marker}"
    if cap <= len(suffix):
        return suffix[-cap:] if cap else ""
    return value[: cap - len(suffix)].rstrip() + suffix


_PROFILE_QUERY_STOP_WORDS = frozenset({
    "about", "actually", "again", "also", "and", "are", "can", "could",
    "does", "from", "generate", "have", "image", "into", "know", "me",
    "my", "of", "please", "quick", "really", "tell", "that", "the",
    "this", "what", "where", "which", "who", "with", "you", "your",
})

_PROFILE_FACT_CATEGORY_TERMS = {
    "access": "access",
    "account": "access",
    "accounts": "access",
    "credential": "credential",
    "credentials": "credential",
    "key": "credential",
    "keys": "credential",
    "secret": "credential",
    "secrets": "credential",
    "token": "credential",
    "tokens": "credential",
    "deploy": "deploy",
    "deployment": "deploy",
    "deployments": "deploy",
    "host": "server",
    "hosts": "server",
    "project": "project",
    "projects": "project",
    "repo": "repo",
    "repos": "repo",
    "repository": "repo",
    "repositories": "repo",
    "server": "server",
    "servers": "server",
    "vps": "server",
}
_PROFILE_FACT_LOOKUP_STOP_WORDS = _PROFILE_QUERY_STOP_WORDS | frozenset({
    "all", "any", "at", "available", "be", "been", "being", "belong", "belongs",
    "check", "checkout", "checkouts", "code", "codebase", "configured", "config",
    "configuration", "current", "declared", "details", "did", "directories", "directory",
    "do", "done", "each", "entries", "entry", "every", "exist", "exists", "fact",
    "facts", "file", "files", "find", "folder", "folders", "for", "get", "give",
    "had", "has", "how", "i", "in", "information", "is", "it",
    "its", "known", "latest", "list", "live", "local", "location", "locations", "look", "looking",
    "method", "methods", "mine", "name", "names", "need", "now", "on", "our", "owned",
    "path", "paths", "real", "setup", "source",
    "recent", "recorded", "remote", "saved", "show", "should", "status", "stored", "their", "them",
    "there", "they", "to", "today", "tonight", "use", "used", "uses", "using", "value", "values", "want",
    "was", "we", "were", "when", "why", "will", "would",
})


def _profile_query_terms(query: str) -> tuple[str, ...]:
    """Return the small set of operator terms useful for profile excerpts."""
    words = re.findall(r"[a-z0-9][a-z0-9_.-]{1,}", str(query or "").lower())
    return tuple(dict.fromkeys(word for word in words if word not in _PROFILE_QUERY_STOP_WORDS))[:12]


def _profile_fact_lookup_terms(query: str) -> tuple[str, ...]:
    """Keep category aliases and real entity terms for fact lookup matching."""
    words = re.findall(r"[a-z0-9][a-z0-9_.-]{1,}", str(query or "").lower())
    meaningful = (
        word
        for word in words
        if word in _PROFILE_FACT_CATEGORY_TERMS
        or word not in _PROFILE_FACT_LOOKUP_STOP_WORDS
    )
    return tuple(dict.fromkeys(meaningful))[:12]


def _relevant_profile_excerpt(text: str, query_terms: tuple[str, ...]) -> str:
    """Select query-matching profile lines with their owning headings.

    The profile remains file-backed and bounded; this only prevents an exact
    project/term question from receiving the unrelated head of a long file.
    """
    if not query_terms:
        return ""
    patterns = tuple(re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", re.IGNORECASE) for term in query_terms)
    selected: list[str] = []
    current_heading = ""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            current_heading = stripped
        if not stripped or not any(pattern.search(stripped) for pattern in patterns):
            continue
        if current_heading and current_heading != stripped and current_heading not in selected:
            selected.append(current_heading)
        if stripped not in selected:
            selected.append(stripped)
    return "\n".join(selected)


_BEHAVIOR_RULE_RE = re.compile(
    r"^- \[([^\]]+)\]\s+(.+?)\s*(?:<!--\s*insight:[a-f0-9]{12}\s*-->)?\s*$",
    re.IGNORECASE,
)
_BEHAVIOR_CATEGORY_PRIORITY = {
    "chat": ("communication", "behavior", "scope", "evidence", "workflow", "design", "focus"),
    "lookup": ("behavior", "communication", "scope", "evidence", "workflow", "design", "focus"),
    "profile": ("behavior", "communication", "scope", "evidence", "workflow", "design", "focus"),
    "work": ("scope", "evidence", "workflow", "communication", "behavior", "design", "focus"),
}


def active_behavior_rules_excerpt(
    text: str,
    *,
    query_terms: tuple[str, ...] = (),
    policy: str = "work",
    max_chars: int = 600,
) -> str:
    """Return complete accepted behavior rules ranked for this profile purpose."""
    active = False
    rows: list[tuple[int, str, str]] = []
    for index, raw in enumerate(str(text or "").splitlines()):
        stripped = raw.strip()
        if stripped == "## Active Learned Rules":
            active = True
            continue
        if active and stripped.startswith("## "):
            active = False
        if not active:
            continue
        match = _BEHAVIOR_RULE_RE.match(stripped)
        if match:
            rows.append((index, match.group(1).casefold(), f"- [{match.group(1)}] {match.group(2).strip()}"))
    if not rows or max_chars <= 0:
        return ""

    patterns = tuple(
        re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", re.IGNORECASE)
        for term in query_terms
    )
    category_order = _BEHAVIOR_CATEGORY_PRIORITY.get(
        str(policy or "work").casefold(),
        _BEHAVIOR_CATEGORY_PRIORITY["work"],
    )
    category_rank = {category: index for index, category in enumerate(category_order)}

    def rank(row: tuple[int, str, str]) -> tuple[int, int, int]:
        index, category, line = row
        matches = sum(1 for pattern in patterns if pattern.search(line))
        return (-matches, category_rank.get(category, len(category_rank)), -index)

    selected = ["## Active Learned Rules"]
    for _index, _category, line in sorted(rows, key=rank):
        candidate = "\n".join([*selected, line])
        if len(candidate) > max_chars:
            continue
        selected.append(line)
    return "\n".join(selected) if len(selected) > 1 else ""


def _query_matched_profile_fact_lines(profile_facts: list[Any], query_terms: tuple[str, ...]) -> str:
    """Select only operational fact rows relevant to an explicit lookup.

    Named terms win over broad categories so asking about one project does not
    expand every ``[project]`` row. A category-only lookup may receive that
    category's bounded rows; ordinary chat never calls this selector.
    """
    operational = [item for item in profile_facts if getattr(item, "category", "") != "preference"]
    requested_categories = {
        _PROFILE_FACT_CATEGORY_TERMS[term]
        for term in query_terms
        if term in _PROFILE_FACT_CATEGORY_TERMS
    }
    entity_terms = tuple(
        term for term in query_terms
        if term not in _PROFILE_FACT_LOOKUP_STOP_WORDS
        and term not in _PROFILE_FACT_CATEGORY_TERMS
    )
    if entity_terms:
        matched = [
            item for item in operational
            if (
                (not requested_categories or item.category in requested_categories)
                and _relevant_profile_excerpt(
                    str(getattr(item, "fact", "")),
                    entity_terms,
                )
            )
        ]
        # Reuse the existing fact ranker so a late, specific subject outranks
        # earlier rows sharing only the product name.
        return _query_matched_work_fact_lines(matched, entity_terms)

    # A category-only question may receive that category's bounded rows. If the
    # query contained a real entity term but no row matched it, never fall back
    # to every row in the category: unknown names must stay unknown. Generic
    # nouns such as ``path`` are deliberately not category aliases because the
    # facts schema has no path category and mapping them to project leaked every
    # unrelated project fact.
    return "\n".join(
        f"- [{item.category}] {item.fact}"
        for item in operational
        if item.category in requested_categories
    )


def _query_matched_work_fact_lines(profile_facts: list[Any], query_terms: tuple[str, ...]) -> str:
    """Rank complete fact rows for ordinary work without duplicating raw excerpts."""
    if not query_terms:
        return ""
    patterns = tuple(
        re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", re.IGNORECASE)
        for term in query_terms
    )
    matched: list[tuple[int, int, int, Any]] = []
    for index, item in enumerate(profile_facts):
        haystack = f"{getattr(item, 'category', '')} {getattr(item, 'fact', '')}"
        score = sum(1 for pattern in patterns if pattern.search(haystack))
        if score:
            preference_rank = 0 if getattr(item, "category", "") == "preference" else 1
            matched.append((-score, preference_rank, index, item))
    return "\n".join(
        f"- [{item.category}] {item.fact}"
        for _score, _preference_rank, _index, item in sorted(matched)
    )


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
    return text.strip(" .;:-")


def _learning_tokens(value: str) -> set[str]:
    aliases = {
        "completed": "complete",
        "completion": "complete",
        "duplicates": "duplicate",
        "duplicated": "duplicate",
        "files": "file",
        "sources": "source",
    }
    stopwords = {"a", "an", "and", "for", "of", "the", "to", "with"}
    return {
        aliases.get(token, token)
        for token in re.findall(r"[a-z0-9]+", _normalize_learning_insight(value))
        if token not in stopwords
    }


def _learning_insights_duplicate(left: str, right: str) -> bool:
    left_norm = _normalize_learning_insight(left)
    right_norm = _normalize_learning_insight(right)
    if left_norm == right_norm:
        return True
    left_tokens = _learning_tokens(left_norm)
    right_tokens = _learning_tokens(right_norm)
    if min(len(left_tokens), len(right_tokens)) < 5:
        return False
    overlap = len(left_tokens.intersection(right_tokens))
    return (
        overlap / min(len(left_tokens), len(right_tokens)) >= 0.85
        and overlap / len(left_tokens.union(right_tokens)) >= 0.7
    )


def _learning_line_norms(line: str) -> list[str]:
    match = re.match(r"^-\s*(.+)$", str(line or ""))
    if not match:
        return []
    body = re.sub(r"<!--.*?-->", "", match.group(1)).strip()
    body = re.sub(r"^\[[^]]+\]\s*", "", body)
    if re.match(r"^source\s*:", body, flags=re.IGNORECASE):
        return []
    keyed = re.match(
        r"^(?:core_traits|current_focus|communication_style|evolution|tool_selection|scoping|taskboard)\s*:\s*(.+)$",
        body,
        flags=re.IGNORECASE,
    )
    if keyed:
        body = keyed.group(1).strip()
    return [
        norm
        for part in _split_learning_items(body)
        if (norm := _normalize_learning_insight(part))
    ]


def _deduplicate_profile_learning_text(text: str) -> tuple[str, dict[str, int]]:
    lines = str(text or "").splitlines()
    header_re = re.compile(r"^## \S+T\S+Z\s+—\s+profile learning")
    entry_re = re.compile(r"^-\s*(?:core_traits|current_focus|communication_style|evolution):\s*(.+)$")
    sections: list[list[str]] = [[]]
    for line in lines:
        if line.startswith("## "):
            sections.append([line])
        else:
            sections[-1].append(line)

    canonical: list[str] = []
    for section in sections:
        if section and header_re.match(section[0]):
            continue
        for line in section:
            canonical.extend(_learning_line_norms(line))

    seen = list(canonical)
    entries_before = len(canonical)
    entries_after = len(canonical)
    duplicates_removed = 0
    sections_removed = 0
    kept_sections: list[list[str]] = []
    for section in sections:
        if not section or not header_re.match(section[0]):
            kept_sections.append(section)
            continue
        kept = [section[0]]
        original_entries = 0
        kept_entries = 0
        for line in section[1:]:
            match = entry_re.match(line)
            if not match:
                kept.append(line)
                continue
            original_entries += 1
            entries_before += 1
            body = re.sub(r"<!--.*?-->", "", match.group(1)).strip()
            norm = _normalize_learning_insight(body)
            if any(_learning_insights_duplicate(norm, prior) for prior in seen):
                duplicates_removed += 1
                continue
            seen.append(norm)
            kept.append(line)
            kept_entries += 1
            entries_after += 1
        if original_entries and not kept_entries:
            sections_removed += 1
            continue
        kept_sections.append(kept)

    output: list[str] = []
    for section in kept_sections:
        output.extend(section)
    audit = {
        "entries_before": entries_before,
        "entries_after": entries_after,
        "duplicates_removed": duplicates_removed,
        "sections_removed": sections_removed,
    }
    return "\n".join(output).rstrip() + "\n", audit


def _deduplicate_behavior_learning_text(text: str) -> tuple[str, int]:
    seen: list[str] = []
    kept: list[str] = []
    removed = 0
    for line in str(text or "").splitlines():
        match = re.match(r"^-\s*\[[^]]+\]\s*(.+?)\s*<!--\s*insight:[a-f0-9]{12}\s*-->\s*$", line)
        if match:
            norm = _normalize_learning_insight(match.group(1))
            if any(_learning_insights_duplicate(norm, prior) for prior in seen):
                removed += 1
                continue
            seen.append(norm)
        kept.append(line)
    return "\n".join(kept).rstrip() + "\n", removed


def _learning_fingerprint(norm: str) -> str:
    import hashlib
    return hashlib.sha1(str(norm or "").encode("utf-8", errors="ignore")).hexdigest()[:12]


def _existing_learning_norms(existing: str) -> set[str]:
    norms: set[str] = set()
    for line in str(existing or "").splitlines():
        norms.update(_learning_line_norms(line))
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
    if any(word in low for word in ("design dna", "user design", "visual", "rendering", "rounded", "radius", "interface", "ui language")):
        return "design"
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
