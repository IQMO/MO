"""MO LSP bridge — local, operator-configured language-server diagnostics.

The hand-rolled client and lazy manager capture configured local diagnostics.
The typed manager result distinguishes disabled/unsupported, clean, diagnostics,
unavailable, timeout, and read failure; the post-edit gate consumes that truth.
"""
from __future__ import annotations

from .client import LspClient, LspError, path_to_uri
from .manager import LspDiagnosticResult, LspManager, language_for, summarize_diagnostics

__all__ = [
    "LspClient",
    "LspError",
    "LspManager",
    "LspDiagnosticResult",
    "language_for",
    "summarize_diagnostics",
    "path_to_uri",
]
