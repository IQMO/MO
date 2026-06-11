"""Response/proposal transcript helpers for `MoTui`."""
from __future__ import annotations

from .ghost import proposal_chat_text
from .input import terminal_columns
from .response import response_block_fragment_lines, response_line_fragments


class ResponseMixin:
    @staticmethod
    def _proposal_chat_text(proposal: str) -> str:
        return proposal_chat_text(proposal)

    def _add_response_line(self, line: str):
        """Append a response line with lightweight report typography."""
        self._add_fragments_line(response_line_fragments(line))

    def _response_columns(self) -> int:
        try:
            cols = int(self._app.output.get_size().columns) if self._app else terminal_columns()
        except Exception:
            cols = terminal_columns()
        return max(20, cols - 1)

    def _add_response_block(self, text: str):
        """Append assistant text with compact inline marker."""
        hide = getattr(self, "_last_speaker", "") == "MO"
        for fragments in response_block_fragment_lines(text, columns=self._response_columns(), hide_marker=hide):
            self._add_fragments_line(fragments)
        self._last_speaker = "MO"
