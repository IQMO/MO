"""Strict ``design/v2`` schema for portable documents.

The document is declarative.  HTML and CSS describe the preview; JavaScript is
off by default and is executed only by the renderer's opaque-origin sandbox.
Runtime state, provider transcripts, credentials, and profile prose never enter
the portable file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .board import BoardState, empty_board, parse_board
from .edits import parse_preview_edits


SCHEMA_ID = "design/v2"
DESIGN_EXTENSION = ".modesign"
MAX_DOCUMENT_BYTES = 2_000_000
MAX_HTML_CHARS = 240_000
MAX_CSS_CHARS = 120_000
MAX_SCRIPT_CHARS = 80_000
MAX_TEXT_CHARS = 4_000
MAX_LIST_ITEMS = 24
# ``dna`` is accepted only as a legacy input key and is discarded on the next
# write. Visual preferences are profile-owned guidance, never artifact state.
_ROOT_KEYS = frozenset({"mo", "meta", "window", "runtime", "design", "handoff", "dna", "board"})
_META_KEYS = frozenset({"id", "title", "summary", "revision", "created_at", "updated_at"})
_WINDOW_KEYS = frozenset({"width", "height", "min_width", "min_height", "resizable"})
_RUNTIME_KEYS = frozenset({"allow_scripts"})
_DESIGN_KEYS = frozenset({"html", "css", "script", "edits"})
_HANDOFF_KEYS = frozenset({
    "objective", "acceptance", "decisions", "constraints", "project_root",
    "context_query", "files", "symbols",
})
_LEGACY_DNA_KEYS = frozenset({"mode", "hints"})


class DesignValidationError(ValueError):
    """A safe validation error suitable for a tool or renderer boundary."""


@dataclass(frozen=True)
class DesignMeta:
    id: str
    title: str
    summary: str = ""
    revision: int = 1
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class DesignWindow:
    width: int = 1180
    height: int = 760
    min_width: int = 860
    min_height: int = 560
    resizable: bool = True


@dataclass(frozen=True)
class DesignRuntime:
    allow_scripts: bool = False


@dataclass(frozen=True)
class DesignContent:
    html: str = ""
    css: str = ""
    script: str = ""
    edits: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class DesignHandoff:
    objective: str = ""
    acceptance: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    project_root: str = ""
    context_query: str = ""
    files: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class DesignDocument:
    meta: DesignMeta
    window: DesignWindow = field(default_factory=DesignWindow)
    runtime: DesignRuntime = field(default_factory=DesignRuntime)
    design: DesignContent = field(default_factory=DesignContent)
    handoff: DesignHandoff = field(default_factory=DesignHandoff)
    board: BoardState = field(default_factory=empty_board)

    def to_dict(self) -> dict[str, Any]:
        row = {
            "mo": SCHEMA_ID,
            "meta": {
                "id": self.meta.id,
                "title": self.meta.title,
                "summary": self.meta.summary,
                "revision": self.meta.revision,
                "created_at": self.meta.created_at,
                "updated_at": self.meta.updated_at,
            },
            "window": {
                "width": self.window.width,
                "height": self.window.height,
                "min_width": self.window.min_width,
                "min_height": self.window.min_height,
                "resizable": self.window.resizable,
            },
            "runtime": {"allow_scripts": self.runtime.allow_scripts},
            "design": {
                "html": self.design.html,
                "css": self.design.css,
                "script": self.design.script,
            },
            "handoff": {
                "objective": self.handoff.objective,
                "acceptance": list(self.handoff.acceptance),
                "decisions": list(self.handoff.decisions),
                "constraints": list(self.handoff.constraints),
                "project_root": self.handoff.project_root,
                "context_query": self.handoff.context_query,
                "files": list(self.handoff.files),
                "symbols": list(self.handoff.symbols),
            },
        }
        row["board"] = self.board.to_dict()
        if self.design.edits:
            row["design"]["edits"] = [dict(edit, style=dict(edit["style"])) for edit in self.design.edits]
        return row


def parse_design(source: str | bytes | dict[str, Any]) -> DesignDocument:
    """Parse and validate one complete MO Design document."""
    if isinstance(source, bytes):
        if len(source) > MAX_DOCUMENT_BYTES:
            raise DesignValidationError("MO Design document is larger than 2 MiB")
        text = source.decode("utf-8", errors="strict")
        raw = _load_yaml(text)
    elif isinstance(source, str):
        if len(source.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise DesignValidationError("MO Design document is larger than 2 MiB")
        raw = _load_yaml(source)
    elif isinstance(source, dict):
        raw = dict(source)
    else:
        raise DesignValidationError("MO Design document must be YAML text or an object")
    if not isinstance(raw, dict):
        raise DesignValidationError("MO Design document root must be an object")
    _only_keys(raw, _ROOT_KEYS, "document")
    if str(raw.get("mo") or "") != SCHEMA_ID:
        raise DesignValidationError(f"MO Design document must declare mo: {SCHEMA_ID}")

    meta = _mapping(raw.get("meta"), "meta", required=True)
    window = _mapping(raw.get("window"), "window")
    runtime = _mapping(raw.get("runtime"), "runtime")
    design = _mapping(raw.get("design"), "design", required=True)
    handoff = _mapping(raw.get("handoff"), "handoff")
    legacy_dna = _mapping(raw.get("dna"), "dna")
    _only_keys(meta, _META_KEYS, "meta")
    _only_keys(window, _WINDOW_KEYS, "window")
    _only_keys(runtime, _RUNTIME_KEYS, "runtime")
    _only_keys(design, _DESIGN_KEYS, "design")
    _only_keys(handoff, _HANDOFF_KEYS, "handoff")
    _only_keys(legacy_dna, _LEGACY_DNA_KEYS, "dna")

    design_id = _design_id(meta.get("id"))
    title = _text(meta.get("title"), "meta.title", required=True, limit=160)
    revision = _integer(meta.get("revision", 1), "meta.revision", 1, 1_000_000)
    allow_scripts = _boolean(runtime.get("allow_scripts", False), "runtime.allow_scripts")
    script = _text(design.get("script", ""), "design.script", limit=MAX_SCRIPT_CHARS)
    if script and not allow_scripts:
        raise DesignValidationError("design.script requires runtime.allow_scripts: true")

    try:
        board = parse_board(raw.get("board"))
        edits = parse_preview_edits(design.get("edits"))
    except ValueError as exc:
        raise DesignValidationError(str(exc)) from None

    return DesignDocument(
        meta=DesignMeta(
            id=design_id,
            title=title,
            summary=_text(meta.get("summary", ""), "meta.summary", limit=MAX_TEXT_CHARS),
            revision=revision,
            created_at=_text(meta.get("created_at", ""), "meta.created_at", limit=64),
            updated_at=_text(meta.get("updated_at", ""), "meta.updated_at", limit=64),
        ),
        window=DesignWindow(
            width=_integer(window.get("width", 1180), "window.width", 640, 3840),
            height=_integer(window.get("height", 760), "window.height", 420, 2160),
            min_width=_integer(window.get("min_width", 860), "window.min_width", 480, 3840),
            min_height=_integer(window.get("min_height", 560), "window.min_height", 320, 2160),
            resizable=_boolean(window.get("resizable", True), "window.resizable"),
        ),
        runtime=DesignRuntime(allow_scripts=allow_scripts),
        design=DesignContent(
            html=_text(design.get("html", ""), "design.html", limit=MAX_HTML_CHARS),
            css=_text(design.get("css", ""), "design.css", limit=MAX_CSS_CHARS),
            script=script,
            edits=edits,
        ),
        handoff=DesignHandoff(
            objective=_text(handoff.get("objective", ""), "handoff.objective", limit=MAX_TEXT_CHARS),
            acceptance=_strings(handoff.get("acceptance"), "handoff.acceptance"),
            decisions=_strings(handoff.get("decisions"), "handoff.decisions"),
            constraints=_strings(handoff.get("constraints"), "handoff.constraints"),
            project_root=_text(handoff.get("project_root", ""), "handoff.project_root", limit=1024),
            context_query=_text(handoff.get("context_query", ""), "handoff.context_query", limit=1000),
            files=_strings(handoff.get("files"), "handoff.files", item_limit=1024),
            symbols=_strings(handoff.get("symbols"), "handoff.symbols", item_limit=240),
        ),
        board=board,
    )


def render_design(document: DesignDocument) -> str:
    """Serialize a validated document as deterministic, human-editable YAML."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - core requirement
        raise DesignValidationError("PyYAML is required to write MO Design documents") from exc
    text = yaml.safe_dump(
        document.to_dict(),
        sort_keys=False,
        allow_unicode=True,
        width=1000,
        default_flow_style=False,
    )
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise DesignValidationError("MO Design document is too large")
    return text


def _load_yaml(text: str) -> Any:
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError as exc:  # pragma: no cover - core requirement
        raise DesignValidationError("PyYAML is required to read MO Design documents") from exc
    except Exception as exc:
        mark = getattr(exc, "problem_mark", None)
        suffix = ""
        if mark is not None:
            suffix = f" at line {int(mark.line) + 1}, column {int(mark.column) + 1}"
        raise DesignValidationError(f"invalid MO Design YAML{suffix}") from None


def _mapping(value: Any, label: str, *, required: bool = False) -> dict[str, Any]:
    if value is None and not required:
        return {}
    if not isinstance(value, dict):
        raise DesignValidationError(f"{label} must be an object")
    return dict(value)


def _only_keys(value: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = sorted(str(key) for key in value if key not in allowed)
    if extra:
        raise DesignValidationError(f"{label} contains unsupported field: {extra[0]}")


def _text(value: Any, label: str, *, required: bool = False, limit: int = MAX_TEXT_CHARS) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise DesignValidationError(f"{label} must be text")
    if len(value) > limit:
        raise DesignValidationError(f"{label} is too large")
    if required and not value.strip():
        raise DesignValidationError(f"{label} is required")
    if "\x00" in value:
        raise DesignValidationError(f"{label} contains an invalid character")
    return value


def _strings(value: Any, label: str, *, item_limit: int = MAX_TEXT_CHARS) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        raise DesignValidationError(f"{label} must be a list with at most {MAX_LIST_ITEMS} items")
    return tuple(_text(item, f"{label} item", required=True, limit=item_limit) for item in value)


def _integer(value: Any, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise DesignValidationError(f"{label} must be an integer from {low} through {high}")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise DesignValidationError(f"{label} must be true or false")
    return value


def _design_id(value: Any) -> str:
    text = _text(value, "meta.id", required=True, limit=64).lower()
    if len(text) < 3 or any(not (char.isalnum() or char == "-") for char in text):
        raise DesignValidationError("meta.id must use 3-64 lowercase letters, numbers, or hyphens")
    return text
