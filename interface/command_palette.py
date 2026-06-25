"""MO — Command palette: tabbed panel triggered by / key.

LEGACY GUARD: palette categories come from `interface/command_registry.py`.
Do not reintroduce hardcoded command/category lists here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .command_registry import DEFAULT_PALETTE_CATEGORY, PALETTE_CATEGORIES, SLASH_COMMANDS

DEFAULT_CATEGORY = DEFAULT_PALETTE_CATEGORY  # Tasks


@dataclass(frozen=True)
class PaletteItem:
    """One selectable command-palette row."""

    value: str
    label: str
    desc: str = ""
    kind: str = "command"  # command | insert | submenu


def model_palette_items(agent: Any) -> list[PaletteItem]:
    items: list[PaletteItem] = []
    providers = list(getattr(agent, "providers", []) or [])
    active = int(getattr(agent, "provider_index", -1) or 0)
    for index, provider in enumerate(providers):
        name = str(getattr(provider, "name", "provider") or "provider")
        model = str(getattr(provider, "model", "model") or "model")
        desc = f"{name} / {model}"
        if index == active:
            desc = f"current · {desc}"
        items.append(PaletteItem(f"/model {index + 1}", f"[{index + 1}] {name}", desc))
    return items


def palette_children_for_item(item: PaletteItem, agent: Any) -> list[PaletteItem]:
    value = item.value.strip()
    if value == "/session":
        return []
    if value == "/model":
        return model_palette_items(agent)
    if value == "/think":
        return [PaletteItem(f"/think {level}", level, f"set reasoning {level}") for level in ("high", "medium", "low")]
    if value == "/goal":
        return [
            PaletteItem("/goal ", "new goal…", "type autonomous goal", "insert"),
            PaletteItem("/goal status", "status", "show goal progress"),
            PaletteItem("/goal stop", "stop", "stop active goal"),
        ]
    if value == "/prt":
        return [
            PaletteItem("/prt", "review codebase", "run a deep codebase review"),
            PaletteItem("/prt fix", "fix findings", "run review and auto-fix findings"),
            PaletteItem("/prt ", "review files…", "review specific files", "insert"),
        ]
    if value == "/profile":
        return [
            PaletteItem("/profile", "show", "show profile"),
            PaletteItem("/profile name ", "name…", "type operator name", "insert"),
            PaletteItem("/profile tools ", "tools…", "type preferred tools", "insert"),
            PaletteItem("/profile provider ", "provider…", "type favorite provider/model", "insert"),
            PaletteItem("/profile mine", "review", "review safe learning updates"),
        ]
    return []


class CommandPalette:
    """Tabbed command palette state for the TUI."""

    def __init__(self):
        self.open = False
        self.category_idx = DEFAULT_CATEGORY
        self.selected_idx = 0
        self._recent: list[str] = []
        self._stack: list[tuple[str, list[PaletteItem]]] = []

    def toggle(self):
        if self.open:
            self.close()
        else:
            self.show()

    def show(self):
        self.open = True
        self.category_idx = DEFAULT_CATEGORY
        self.selected_idx = 0
        self._stack = []

    def close(self):
        self.open = False
        self._stack = []

    @property
    def in_submenu(self) -> bool:
        return bool(self._stack)

    def enter_submenu(self, title: str, items: list[PaletteItem | tuple[str, str]]):
        self.open = True
        self._stack.append((title, [self._coerce_item(item) for item in items]))
        self.selected_idx = 0

    def back(self) -> bool:
        if not self._stack:
            return False
        self._stack.pop()
        self.selected_idx = 0
        return True

    def move_selection(self, delta: int):
        items = self._current_items()
        if not items:
            return
        self.selected_idx = (self.selected_idx + delta) % len(items)

    def move_category(self, delta: int):
        if self._stack:
            if delta < 0:
                self.back()
            return
        total = len(PALETTE_CATEGORIES)
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
        root = cmd.split()[0] if cmd else cmd
        if root in ("/help", "/exit") or not root.startswith("/"):
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
        if self._stack:
            return self._stack[-1][1]
        if not PALETTE_CATEGORIES:
            return []
        idx = self.category_idx % len(PALETTE_CATEGORIES)
        name, items = PALETTE_CATEGORIES[idx]
        if name == "Recent":
            return [PaletteItem(cmd, cmd, SLASH_COMMANDS.get(cmd, "")) for cmd in self._recent if cmd in SLASH_COMMANDS]
        return [self._coerce_item(item) for item in items]

    def get_fragments(self) -> list[tuple[str, str]]:
        """Return prompt_toolkit formatted text fragments for the palette."""
        if not self.open:
            return [("", "")]

        fragments: list[tuple[str, str]] = []

        # Header
        title = " COMMANDS " if not self._stack else f" COMMANDS › {self._stack[-1][0]} "
        hint = "  ↑/↓ select  Tab/S-Tab tabs  Enter open/run  Esc close\n"
        if self._stack:
            hint = "  ↑/↓ select  Enter open/run  Esc back/close\n"
        fragments.append(("class:palette-title", title))
        fragments.append(("class:palette-hint", hint))

        # Category tabs
        if not self._stack:
            for idx, (name, _items) in enumerate(PALETTE_CATEGORIES):
                if idx == self.category_idx:
                    fragments.append(("class:palette-selected", f" {name} "))
                else:
                    fragments.append(("class:palette-category", f" {name} "))
                fragments.append(("class:palette-hint", "  "))
            fragments.append(("", "\n"))

        # Items
        items = self._current_items()
        if not items:
            fragments.append(("class:palette-hint", "  no commands\n"))
            return fragments

        visible_start = max(0, self.selected_idx - 7)
        visible_end = min(len(items), visible_start + 8)

        for i in range(visible_start, visible_end):
            item = items[i]
            selected = (i == self.selected_idx)
            marker = "▶ " if selected else "  "
            suffix = " ›" if item.kind == "submenu" else ""
            label = f"{item.label}{suffix}"
            if selected:
                fragments.append(("class:palette-selected", f"{marker}{label:<18} … {item.desc}\n"))
            else:
                fragments.append(("class:palette-command", f"  {label:<18}"))
                fragments.append(("class:palette-desc", f" … {item.desc}\n"))

        if len(items) > 8:
            fragments.append(("class:palette-hint", f"  showing {visible_start + 1}-{visible_end}/{len(items)}\n"))

        return fragments
