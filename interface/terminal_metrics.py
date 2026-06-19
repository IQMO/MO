"""Shared terminal-size helpers for TUI mixins.

Single source for the column/row probe that was previously duplicated across
7+ call sites in the display, response, and transcript mixins (IFDEV05 D1).
Lives in its own module so each consuming mixin — and its bare-mixin test
harness — inherits the helpers directly, without depending on MoTui's full
mixin composition.
"""
from __future__ import annotations


class TerminalMetricsMixin:
    """Provides ``_terminal_columns()`` / ``_terminal_rows()`` to a TUI mixin."""

    def _terminal_columns(self) -> int:
        from .input import terminal_columns
        try:
            app = getattr(self, "_app", None)
            return app.output.get_size().columns if app else terminal_columns()
        except Exception:
            return terminal_columns()

    def _terminal_rows(self) -> int:
        try:
            app = getattr(self, "_app", None)
            return app.output.get_size().rows if app else 24
        except Exception:
            return 24
