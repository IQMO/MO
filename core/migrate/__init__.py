"""MO Migration — move a user from a peer agent (OpenClaw/Hermes/…) into MO.

Import lazily (only when a user opts into migration); this package stays off the
agent startup chain. Tier 1 (prove-it) is read-only inspection; Tier 2/3 add the
approval-gated, idempotent, safe writes.
"""
from __future__ import annotations

from .apply import ASSETS, ApplyResult, apply_asset, render_apply
from .inspect import InspectResult, inspect_source, render_inspect
from .plan import Coverage, build_coverage, render_coverage
from .registry import (
    KNOWN_SOURCES,
    SourceAgent,
    SourceFile,
    known_source_keys,
    resolve_source,
)

__all__ = [
    "KNOWN_SOURCES",
    "SourceAgent",
    "SourceFile",
    "known_source_keys",
    "resolve_source",
    "InspectResult",
    "inspect_source",
    "render_inspect",
    "Coverage",
    "build_coverage",
    "render_coverage",
    "ASSETS",
    "ApplyResult",
    "apply_asset",
    "render_apply",
]
