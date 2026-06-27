"""Isolated next-generation terminal UX surface for MO Agent.

The package is default-off and isolated from the production ``interface/`` tree.
Run ``python -m UX`` or ``python mo.py --ux`` for live UX runtime mode while
default ``python mo.py`` remains on the current interface. Use
``python -m UX --preview`` for the local-only preview backend.
"""
from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
