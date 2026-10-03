"""Typed slash-command results shared by every command surface."""
from __future__ import annotations

from typing import Any


_RESULT_KINDS = frozenset({"notice", "report", "error", "control"})


class SlashCommandResult(str):
    """Display text plus explicit presentation and control semantics."""

    kind: str
    action: str
    title: str
    choices: tuple[tuple[str, str], ...]

    def __new__(
        cls,
        text: str = "",
        *,
        kind: str = "report",
        action: str = "",
        title: str = "",
        choices: tuple[tuple[str, str], ...] = (),
    ) -> "SlashCommandResult":
        clean_kind = kind if kind in _RESULT_KINDS else "report"
        obj = super().__new__(cls, str(text or ""))
        obj.kind = clean_kind
        obj.action = str(action or "")
        obj.title = str(title or "")
        obj.choices = tuple(choices)
        return obj

    @property
    def text(self) -> str:
        """Display text; control flow is carried separately in ``action``."""
        return str(self)

    @property
    def plain_text(self) -> str:
        """Keep the same actions usable on surfaces without a command menu."""
        if not self.choices:
            return self.text
        return self.text + "\n\nActions:\n" + "\n".join(
            f"- {label}: {value.rstrip()}" for value, label in self.choices
        )


def command_result(value: Any, *, kind: str = "report") -> SlashCommandResult:
    if isinstance(value, SlashCommandResult):
        return value
    return SlashCommandResult(str(value or ""), kind=kind)


def command_control(action: str, text: str = "") -> SlashCommandResult:
    return SlashCommandResult(text, kind="control", action=action)


def command_failure(command: str, _error: BaseException | None = None) -> SlashCommandResult:
    root = str(command or "command").strip().split()[0]
    return SlashCommandResult(
        f"Could not run {root}. Try again; use /doctor if it keeps failing.",
        kind="error",
    )
