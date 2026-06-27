"""MO skill import pipeline.

Inert acquisition of external sources (GitHub repos, docs sites, llms.txt, local
paths) into provenance-rich, approval-gated skill candidates. Stdlib-only; no
network or execution happens at import time, and imported material is treated as
untrusted data until an operator explicitly promotes it through the existing
``core.skills.write_skill_pack_from_candidate`` path.

See ~/.mo/memory/proposals/VS05-skill-learning-system.md for the design and the
binding integration constraints (one lifecycle, safety-primitive reuse, capped
context budget, structural-only conflict detection, no learned-state fork).
"""
from __future__ import annotations

from .manifest import (
    SCHEMA_VERSION,
    new_skill_evolution,
    new_skill_manifest,
    new_source_manifest,
    read_manifest,
    validate_skill_evolution,
    validate_skill_manifest,
    validate_source_manifest,
    write_manifest,
)
from .risk import RiskFinding, RiskReport, render_risk_report, scan_source_text
from .sources import SourceRef, classify_source, is_safe_local_path

__all__ = [
    "SCHEMA_VERSION",
    "SourceRef",
    "classify_source",
    "is_safe_local_path",
    "new_source_manifest",
    "new_skill_manifest",
    "new_skill_evolution",
    "validate_source_manifest",
    "validate_skill_manifest",
    "validate_skill_evolution",
    "read_manifest",
    "write_manifest",
    "RiskFinding",
    "RiskReport",
    "scan_source_text",
    "render_risk_report",
]
