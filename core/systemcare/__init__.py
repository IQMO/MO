"""MO SystemCare's device-local Windows health and maintenance owner.

The package stays import-light. Windows inspection, SQLite state and the
Desktop adapter are loaded only when a SystemCare entry point is called.
"""

from .models import (
    Calibration,
    Candidate,
    Finding,
    Plan,
    PlanStep,
    ProgressEvent,
    Receipt,
    ScanMode,
    ScanResult,
)

__all__ = [
    "Calibration",
    "Candidate",
    "Finding",
    "Plan",
    "PlanStep",
    "ProgressEvent",
    "Receipt",
    "ScanMode",
    "ScanResult",
]
