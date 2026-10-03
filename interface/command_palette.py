"""MO — Command palette: tabbed panel triggered by / key.

REGISTRY GUARD: palette categories come from `interface/command_registry.py`.
Do not reintroduce hardcoded command/category lists here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .command_registry import (
    DEFAULT_PALETTE_CATEGORY,
    _command_hidden,
    build_palette_categories,
    palette_children,
    resolve_slash_command,
    slash_aliases,
    slash_command_description,
    slash_command_with_desc,
)

DEFAULT_CATEGORY = DEFAULT_PALETTE_CATEGORY


# Cache catalog projections between extension configuration/reload boundaries;
# never rebuild them on animation frames or keep the old catalog after /reload.
_PALETTE_CATEGORIES_CACHE: list[tuple[str, list[tuple[str, str]]]] | None = None
_SLASH_DESCRIPTIONS_CACHE: dict[str, str] | None = None
_CATALOG_REVISION = -1


def _refresh_catalog_revision() -> None:
    from core.local_extensions import configuration_revision

    global _CATALOG_REVISION, _PALETTE_CATEGORIES_CACHE, _SLASH_DESCRIPTIONS_CACHE, _QUERY_ITEMS_CACHE
    revision = configuration_revision()
    if revision != _CATALOG_REVISION:
        _PALETTE_CATEGORIES_CACHE = None
        _SLASH_DESCRIPTIONS_CACHE = None
        _QUERY_ITEMS_CACHE = None
        _CATALOG_REVISION = revision


def _current_palette_categories() -> list[tuple[str, list[tuple[str, str]]]]:
    """Return runtime palette categories, including admitted local extensions.

    Cached until extension configuration changes."""
    global _PALETTE_CATEGORIES_CACHE
    _refresh_catalog_revision()
    if _PALETTE_CATEGORIES_CACHE is None:
        _PALETTE_CATEGORIES_CACHE = build_palette_categories()
    return _PALETTE_CATEGORIES_CACHE


def _slash_descriptions() -> dict[str, str]:
    global _SLASH_DESCRIPTIONS_CACHE
    _refresh_catalog_revision()
    if _SLASH_DESCRIPTIONS_CACHE is None:
        _SLASH_DESCRIPTIONS_CACHE = {command: description for command, description in slash_command_with_desc()}
    return _SLASH_DESCRIPTIONS_CACHE


@dataclass(frozen=True)
class PaletteItem:
    """One selectable command-palette row."""

    value: str
    label: str
    desc: str = ""
    kind: str = "command"  # command | insert | submenu


_QUERY_ITEMS_CACHE: list[PaletteItem] | None = None


def _query_items() -> list[PaletteItem]:
    """Cache command roots and alias search keys, not duplicate capabilities."""
    global _QUERY_ITEMS_CACHE
    _refresh_catalog_revision()
    if _QUERY_ITEMS_CACHE is not None:
        return _QUERY_ITEMS_CACHE

    items: list[PaletteItem] = []
    seen: set[str] = set()
    for category, rows in _current_palette_categories():
        if category == "Recent":
            continue
        for value, desc in rows:
            if value in seen:
                continue
            seen.add(value)
            items.append(PaletteItem(value=value, label=value, desc=desc))

    for alias, target in slash_aliases().items():
        if alias in seen:
            continue
        desc = slash_command_description(target)
        meta = f"-> {target}" if not desc else f"-> {target} · {desc}"
        seen.add(alias)
        items.append(PaletteItem(value=target, label=alias, desc=meta))

    _QUERY_ITEMS_CACHE = items
    return items


def palette_children_for_item(item: PaletteItem, agent: Any) -> list[PaletteItem]:
    """Submenu rows for a palette command. Static children come from the registry
    (single source — see command_registry.palette_children); dynamic argument
    rows are shared with inline Tab completion by the registry layer."""
    value = item.value.strip()
    return [PaletteItem(v, label, desc, kind) for v, label, desc, kind in palette_children(value, agent=agent)]


class CommandPalette:
    """Tabbed command palette state for the TUI."""

    def __init__(self):
        self.open = False
        self.remote = False
        self.category_idx = DEFAULT_CATEGORY
        self.selected_idx = 0
        self._recent: list[str] = []
        self._stack: list[tuple[str, list[PaletteItem]]] = []
        self._query = ""
        self._clear_result()

    @property
    def result_active(self) -> bool:
        return bool(self._result_title)

    def _clear_result(self) -> None:
        self._result_title = ""
        self._result_text = ""
        self._result_kind = "report"
        self._result_scroll = 0
        self._result_columns = None
        self._result_actions: list[PaletteItem] = []

    def show_result(self, command: str, text: str, *, kind: str = "report", actions: list[PaletteItem] | None = None) -> None:
        """Present command feedback inside the command surface, never chat."""
        self.open = True
        if not self.remote and not actions:
            self._stack = []
        self._clear_result()
        self._result_actions = list(actions or [])
        self._query = ""
        self.selected_idx = max(0, len(self._result_actions) - 1)
        self._result_title = str(command or "command").strip()
        self._result_text = str(text or "")
        self._result_kind = str(kind or "report")
        self._result_scroll = 0

    def toggle(self):
        if self.open:
            self.close()
        else:
            self.show()

    def show(self):
        self.remote = False
        self.open = True
        self.category_idx = DEFAULT_CATEGORY
        self.selected_idx = 0
        self._stack = []
        self._query = ""
        self._clear_result()

    def close(self):
        self.remote = False
        self.open = False
        self._stack = []
        self._query = ""
        self._clear_result()

    @property
    def query_active(self) -> bool:
        return len(self._query) > 1

    def set_query(self, text: str):
        query = str(text or "").strip().lower()
        if any(ch.isspace() for ch in query):
            query = ""
        if query == self._query and not self.result_active:
            return
        self._clear_result()
        self._query = query
        self._stack = []
        self.selected_idx = 0

    @property
    def in_submenu(self) -> bool:
        return bool(self._stack)

    def enter_submenu(self, title: str, items: list[PaletteItem | tuple[str, str]]):
        self.open = True
        self._clear_result()
        self._stack.append((title, [self._coerce_item(item) for item in items]))
        self.selected_idx = 0

    def show_command_args(self, title: str, items: list[PaletteItem]):
        """Show a command's arg choices as the current view (replacing any prior
        arg view), so typing ``/cmd arg`` keeps navigating instead of running the
        raw command. Rebuilt live from the typed arg by the input handler."""
        self.open = True
        self._clear_result()
        self._stack = [(title, [self._coerce_item(item) for item in items])]
        self.selected_idx = 0

    def back(self) -> bool:
        if self.result_active:
            self._clear_result()
            self.selected_idx = 0
            return True
        if not self._stack:
            return False
        if self.remote and len(self._stack) == 1:
            self.close()
            return True
        self._stack.pop()
        self.selected_idx = 0
        return True

    def move_selection(self, delta: int):
        if self.result_active:
            lines = self._result_lines_for_width(self._result_columns)
            self._result_scroll = min(max(0, self._result_scroll + delta), max(0, len(lines) - 1))
            return
        items = self._current_items()
        if not items:
            return
        self.selected_idx = (self.selected_idx + delta) % len(items)

    def move_category(self, delta: int):
        if self.result_active:
            if self._result_actions:
                self.selected_idx = (self.selected_idx + delta) % len(self._result_actions)
            else:
                self._clear_result()
            return
        if self.query_active and not self._stack:
            self.move_selection(delta)
            return
        if self._stack:
            if delta < 0:
                self.back()
            return
        total = len(_current_palette_categories())
        if total == 0:
            return
        self.category_idx = (self.category_idx + delta) % total
        self.selected_idx = 0

    def selected_item(self) -> PaletteItem | None:
        items = self._current_items()
        if not items:
            return None
        return items[min(self.selected_idx, len(items) - 1)]

    def select(self) -> str:
        """Return the selected command string and close palette."""
        item = self.selected_item()
        self.close()
        if not item:
            return ""
        self._record_recent(item.value)
        return item.value

    def record_command(self, cmd: str):
        self._record_recent(cmd)

    def _record_recent(self, cmd: str):
        root = resolve_slash_command(cmd.lower())
        if root in ("/help", "/exit") or not root.startswith("/"):
            return
        if _command_hidden(root):
            return
        if root in self._recent:
            self._recent.remove(root)
        self._recent.insert(0, root)
        self._recent = self._recent[:8]

    @staticmethod
    def _coerce_item(item: PaletteItem | tuple[str, str]) -> PaletteItem:
        if isinstance(item, PaletteItem):
            return item
        value, desc = item
        return PaletteItem(value=value, label=value, desc=desc)

    def _current_items(self) -> list[PaletteItem]:
        if self.result_active:
            return self._result_actions
        if self._stack:
            return self._stack[-1][1]
        if self.remote:
            return []
        if self.query_active:
            return self._query_matches()
        categories = _current_palette_categories()
        if not categories:
            return []
        idx = self.category_idx % len(categories)
        name, items = categories[idx]
        if name == "Recent":
            descriptions = _slash_descriptions()
            return [
                PaletteItem(cmd, cmd, descriptions[cmd])
                for cmd in self._recent
                if cmd in descriptions and not _command_hidden(cmd)
            ]
        return [self._coerce_item(item) for item in items]

    def _visible_items(self, columns: int | None) -> tuple[list[PaletteItem], int, int]:
        """Return the item page used by the palette renderer and diagnostics."""
        items = self._current_items()
        page_size = 7 if columns is not None and columns < 60 else 8
        start = max(0, self.selected_idx - page_size + 1)
        return items, start, min(len(items), start + page_size)

    def _query_matches(self) -> list[PaletteItem]:
        query = self._query
        # A slash query ("/mo") means "commands NAMED /mo…", so match names/aliases
        # only. Matching the description too pulled in every command whose text
        # mentions "mo"/"MO" (most of them), burying the real name matches. A plain
        # keyword query ("theme") still searches descriptions.
        slash_query = query.startswith("/")
        prefix: list[PaletteItem] = []
        contains: list[PaletteItem] = []
        for item in _query_items():
            value = item.value.lower()
            label = item.label.lower()
            desc = item.desc.lower()
            if value.startswith(query) or label.startswith(query):
                prefix.append(item)
            elif query in value or query in label or (not slash_query and query in desc):
                contains.append(item)
        matched: list[PaletteItem] = []
        seen: set[str] = set()
        for item in prefix + contains:
            if item.value not in seen:
                matched.append(PaletteItem(item.value, item.value, item.desc, item.kind))
                seen.add(item.value)
        return matched

    def _result_lines_for_width(self, columns: int | None) -> list[str]:
        raw_lines = self._result_text.splitlines() or [""]
        if columns is None:
            return raw_lines
        from .transcript_view import fragment_line_text, wrap_fragment_line

        width = max(8, int(columns or 0) - 2)
        lines: list[str] = []
        for line in raw_lines:
            lines.extend(fragment_line_text(row) for row in wrap_fragment_line([("", line)], width))
        return lines

    def get_fragments(self, *, columns: int | None = None) -> list[tuple[str, str]]:
        """Return prompt_toolkit formatted text fragments for the palette.

        The palette is a fixed-height surface inside the terminal.  Bound every
        physical row to the live terminal width here instead of letting the host
        hard-cut fragments midway through a word or hint.  ``columns=None`` keeps
        the state object usable by narrow unit harnesses that do not own a TUI.
        """
        if not self.open:
            return [("", "")]

        fragments: list[tuple[str, str]] = []

        def append_line(parts: list[tuple[str, str]]) -> None:
            rendered = parts
            if columns is not None:
                from .activity import fit_fragments_to_cells

                rendered, _used = fit_fragments_to_cells(parts, max(1, int(columns or 0)))
            fragments.extend(rendered)
            fragments.append(("", "\n"))

        def selected_window(parts: list[tuple[str, str]], selected: int) -> list[tuple[str, str]]:
            """Keep the selected tab/action visible inside a narrow terminal."""
            from .transcript_view import cell_width, fit_cells

            if columns is None or sum(cell_width(text) for _, text in parts) <= columns:
                return parts
            selected = min(max(0, selected), len(parts) - 1)
            available = max(1, columns - 4)
            start = 0
            while start < selected and sum(cell_width(text) for _, text in parts[start:selected + 1]) > available:
                start += 1
            visible = [("class:palette-hint", "‹ " if start else "  ")]
            for idx in range(start, len(parts)):
                style, text = parts[idx]
                width = cell_width(text)
                if width > available:
                    if idx <= selected:
                        visible.append((style, fit_cells(text, available)))
                    visible.append(("class:palette-hint", " ›"))
                    break
                visible.append((style, text))
                available -= width
            return visible

        # Header
        if self.result_active:
            title = f" COMMANDS › {self._result_title} "
        elif self._stack:
            title = f" COMMANDS › {self._stack[-1][0]} "
        elif self.query_active:
            title = f" COMMANDS › {self._query} "
        else:
            title = " COMMANDS "
        hint = "  ↑/↓ select  Tab/S-Tab tabs  Enter open/run  Esc close"
        if self.result_active:
            hint = "  ↑/↓ scroll  Tab/←/→ choose  Enter select  Esc back" if self._result_actions else "  ↑/↓ scroll  Esc commands"
        elif self.query_active and not self._stack:
            hint = "  ↑/↓ select  Tab/→ next  S-Tab/← previous  Enter open/run  Esc close"
        elif self._stack:
            hint = "  ↑/↓ select  Enter open/run  Esc back/close"
        narrow = columns is not None and columns < 110
        if self.result_active and self._result_actions:
            append_line([("class:palette-title", title)])
            append_line([("class:palette-hint", f" Action {self.selected_idx + 1}/{len(self._result_actions)} · Tab/←/→ · Enter")])
            append_line([("class:palette-hint", " ↑/↓ scroll · Esc back")])
        elif narrow:
            append_line([("class:palette-title", title)])
            short_hint = " ↑/↓ select · Tab tabs · Enter · Esc"
            if self.result_active:
                short_hint = " ↑/↓ scroll · Esc commands"
            elif self._stack or self.query_active:
                short_hint = " ↑/↓ select · Enter open/run · Esc"
            append_line([("class:palette-hint", short_hint)])
        else:
            append_line([("class:palette-title", title), ("class:palette-hint", hint)])

        if self.result_active:
            self._result_columns = columns
            lines = self._result_lines_for_width(columns)
            page_size = 6 if self._result_actions else 9
            max_scroll = max(0, len(lines) - page_size)
            self._result_scroll = min(self._result_scroll, max_scroll)
            end = min(len(lines), self._result_scroll + page_size)
            style = "class:notification-critical" if self._result_kind == "error" else "class:palette-desc"
            for line in lines[self._result_scroll:end]:
                append_line([(style, f"  {line}")])
            if len(lines) > page_size:
                append_line([
                    ("class:palette-hint", f"  lines {self._result_scroll + 1}-{end} of {len(lines)}"),
                ])
            if self._result_actions:
                append_line(selected_window([
                    ("class:palette-selected" if i == self.selected_idx else "class:palette-command", f" [{item.label}] ")
                    for i, item in enumerate(self._result_actions)
                ], self.selected_idx))
            return fragments

        # Category tabs
        if not self._stack and not self.query_active:
            tab_fragments: list[tuple[str, str]] = []
            categories = _current_palette_categories()
            selected_tab = self.category_idx % len(categories) if categories else 0
            for idx, (name, _items) in enumerate(categories):
                if idx == selected_tab:
                    tab_fragments.append(("class:palette-selected", f" {name}   "))
                else:
                    tab_fragments.append(("class:palette-category", f" {name}   "))
            append_line(selected_window(tab_fragments, selected_tab))

        # Items
        items, visible_start, visible_end = self._visible_items(columns)
        if not items:
            append_line([("class:palette-hint", "  no commands")])
            return fragments

        two_line_selection = columns is not None and columns < 60
        page_size = 7 if two_line_selection else 8

        for i in range(visible_start, visible_end):
            item = items[i]
            selected = (i == self.selected_idx)
            marker = "▶ " if selected else "  "
            suffix = " ›" if item.kind == "submenu" else ""
            label = f"{item.label}{suffix}"
            if selected and two_line_selection:
                append_line([("class:palette-selected", f"{marker}{label}")])
                append_line([("class:palette-desc", f"  … {item.desc}")])
            elif selected:
                append_line([("class:palette-selected", f"{marker}{label:<18} … {item.desc}")])
            else:
                append_line([
                    ("class:palette-command", f"  {label:<18}"),
                    ("class:palette-desc", f" … {item.desc}"),
                ])

        if len(items) > page_size:
            append_line([("class:palette-hint", f"  showing {visible_start + 1}-{visible_end}/{len(items)}")])

        return fragments
