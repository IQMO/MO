"""Response/proposal transcript helpers for `MoTui`."""
from __future__ import annotations

from .ghost import proposal_chat_text
from .response import response_block_fragment_lines, response_line_fragments
from .terminal_metrics import TerminalMetricsMixin


class ResponseMixin(TerminalMetricsMixin):
    @staticmethod
    def _proposal_chat_text(proposal: str) -> str:
        return proposal_chat_text(proposal)

    def _add_response_line(self, line: str):
        """Append a response line with lightweight report typography."""
        self._add_fragments_line(response_line_fragments(line))

    def _response_columns(self) -> int:
        return max(20, self._terminal_columns() - 1)

    def _add_response_block(self, text: str):
        """Append assistant text with compact inline marker."""
        hide = getattr(self, "_last_speaker", "") == "MO"
        for fragments in response_block_fragment_lines(text, columns=self._response_columns(), hide_marker=hide):
            self._add_fragments_line(fragments)
        self._last_speaker = "MO"
