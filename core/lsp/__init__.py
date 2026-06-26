"""MO LSP bridge — local, operator-configured language-server diagnostics.

Phase 1 (this package): a hand-rolled LSP client + lazy manager that captures
live diagnostics from configured language servers. Additive and inert until an
operator sets `lsp.servers`; nothing in the turn loop calls it yet. The evidence
hook — turning post-edit diagnostics into MO task evidence and blocking
"fixed/clean" claims on files the server still flags — is a separate, later wiring.
"""
from __future__ import annotations

from .client import LspClient, LspError, path_to_uri
from .manager import LspManager, language_for, summarize_diagnostics

__all__ = [
    "LspClient",
    "LspError",
    "LspManager",
    "language_for",
    "summarize_diagnostics",
    "path_to_uri",
]
