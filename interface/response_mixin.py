"""Response transcript helpers for `MoTui`."""
from __future__ import annotations

from .response import response_block_fragment_lines, response_line_fragments
from .terminal_metrics import TerminalMetricsMixin


class ResponseMixin(TerminalMetricsMixin):
    def _add_response_line(self, line: str):
        """Append a response line with lightweight report typography."""
        self._add_fragments_line(response_line_fragments(line))

    def _response_columns(self) -> int:
        return max(20, self._terminal_columns() - 1)

    def _add_response_block(self, text: str, hide_marker: bool = False):
        """Append assistant text as one canonical transcript/output block."""
        hide = hide_marker or getattr(self, "_last_speaker", "") == "MO"
        # One block commit keeps all wrapped response rows together while retaining
        # the normal blank before the first marked MO block of a turn.
        lines = response_block_fragment_lines(
            text,
            columns=self._response_columns(),
            hide_marker=hide,
        )
        self._add_fragments_block(lines, blank_before=not hide)
        self._last_speaker = "MO"
