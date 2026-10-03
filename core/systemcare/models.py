"""Strict, JSON-safe value objects for MO SystemCare."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


CALIBRATION_SCHEMA_VERSION = 2
MAX_FINDINGS_PER_SCAN = 256
MAX_CANDIDATES_PER_FINDING = 5_000
MAX_EVIDENCE_ITEMS = 32
MAX_TEXT = 320


class ScanMode(str, Enum):
    SAFE = "safe"
    ADVANCED = "advanced"


class OperationState(str, Enum):
    IDLE = "idle"
    CALIBRATING = "calibrating"
    SCANNING = "scanning"
    READY = "ready"
    PLANNED = "planned"
    APPLYING = "applying"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Domain(str, Enum):
    CALIBRATION = "calibration"
    STORAGE = "storage"
    STARTUP = "startup"
    HEALTH = "health"
    PERFORMANCE = "performance"
    REGISTRY = "registry"
    ORGANIZATION = "organization"


class Severity(str, Enum):
    GOOD = "good"
    INFO = "info"
    REVIEW = "review"
    ATTENTION = "attention"


class RiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class UndoQuality(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _number(value: Any, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    try:
        return max(minimum, min(maximum, int(value or 0)))
    except (OverflowError, TypeError, ValueError):
        return minimum


def _float(value: Any, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(value or 0.0))
    except (OverflowError, TypeError, ValueError):
        return minimum


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _enum(enum_type: type[Enum], value: Any, fallback: Enum) -> Any:
    try:
        return enum_type(str(value or fallback.value))
    except ValueError:
        return fallback


def new_id(prefix: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", str(prefix or "item").lower()).strip("-")[:20] or "item"
    return f"{clean}-{uuid.uuid4().hex[:20]}"


def canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", errors="strict")).hexdigest()


@dataclass(frozen=True)
class Candidate:
    """One exact private file candidate, rooted by a calibrated location key."""

    candidate_id: str
    root_key: str
    relative_path: str
    size_bytes: int
    modified_ns: int
    fingerprint: str

    def __post_init__(self) -> None:
        root = str(self.root_key or "").strip().lower()
        relative = str(self.relative_path or "").replace("\\", "/").strip("/")
        parts = [part for part in relative.split("/") if part]
        if not re.fullmatch(r"[a-z0-9_]{1,48}", root):
            raise ValueError("candidate root key is invalid")
        if not parts or any(part in {".", ".."} for part in parts) or ":" in relative:
            raise ValueError("candidate relative path is invalid")
        if len(relative) > 1_024:
            raise ValueError("candidate relative path is too long")
        if not re.fullmatch(r"[a-f0-9]{16,64}", str(self.fingerprint or "")):
            raise ValueError("candidate fingerprint is invalid")
        object.__setattr__(self, "root_key", root)
        object.__setattr__(self, "relative_path", relative)
        object.__setattr__(self, "size_bytes", _number(self.size_bytes))
        object.__setattr__(self, "modified_ns", _number(self.modified_ns))

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": _text(self.candidate_id, 80),
            "root_key": self.root_key,
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "modified_ns": self.modified_ns,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Candidate":
        row = _mapping(value)
        return cls(
            candidate_id=_text(row.get("candidate_id"), 80),
            root_key=_text(row.get("root_key"), 48),
            relative_path=str(row.get("relative_path") or ""),
            size_bytes=_number(row.get("size_bytes")),
            modified_ns=_number(row.get("modified_ns")),
            fingerprint=_text(row.get("fingerprint"), 64).lower(),
        )


@dataclass(frozen=True)
class Calibration:
    calibration_id: str
    device_id: str
    created_at: float
    refresh_after: float
    fingerprint: str
    os_name: str
    os_version: str
    os_build: str
    architecture: str
    cpu_logical: int
    cpu_name: str
    memory_bytes: int
    machine_model: str
    gpu_names: tuple[str, ...]
    windows_edition: str
    system_drive: str
    drive_roots: tuple[str, ...]
    paths: Mapping[str, str]
    capabilities: Mapping[str, bool]
    schema_version: int = CALIBRATION_SCHEMA_VERSION

    def to_dict(self, *, include_paths: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "calibration_id": _text(self.calibration_id, 80),
            "device_id": _text(self.device_id, 80),
            "created_at": float(self.created_at),
            "refresh_after": float(self.refresh_after),
            "fingerprint": _text(self.fingerprint, 64),
            "os_name": _text(self.os_name, 80),
            "os_version": _text(self.os_version, 80),
            "os_build": _text(self.os_build, 80),
            "architecture": _text(self.architecture, 40),
            "cpu_logical": _number(self.cpu_logical, maximum=4_096),
            "cpu_name": _text(self.cpu_name, 160),
            "memory_bytes": _number(self.memory_bytes),
            "machine_model": _text(self.machine_model, 160),
            "gpu_names": [_text(item, 160) for item in self.gpu_names[:8]],
            "windows_edition": _text(self.windows_edition, 120),
            "system_drive": _text(self.system_drive, 12),
            "drive_roots": [_text(item, 24) for item in self.drive_roots[:64]],
            "capabilities": {
                _text(key, 64): bool(enabled)
                for key, enabled in list(self.capabilities.items())[:64]
            },
        }
        if include_paths:
            payload["paths"] = {
                _text(key, 48): str(path)
                for key, path in list(self.paths.items())[:64]
            }
        else:
            payload["path_keys"] = sorted(_text(key, 48) for key in self.paths)[:64]
        return payload

    def public_summary(self) -> dict[str, Any]:
        payload = self.to_dict(include_paths=False)
        payload.pop("device_id", None)
        payload.pop("fingerprint", None)
        payload.pop("drive_roots", None)
        payload["fixed_volume_count"] = len(self.drive_roots)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Calibration":
        row = _mapping(value)
        return cls(
            schema_version=_number(row.get("schema_version"), minimum=1, maximum=100),
            calibration_id=_text(row.get("calibration_id"), 80),
            device_id=_text(row.get("device_id"), 80),
            created_at=_float(row.get("created_at")),
            refresh_after=_float(row.get("refresh_after")),
            fingerprint=_text(row.get("fingerprint"), 64),
            os_name=_text(row.get("os_name"), 80),
            os_version=_text(row.get("os_version"), 80),
            os_build=_text(row.get("os_build"), 80),
            architecture=_text(row.get("architecture"), 40),
            cpu_logical=_number(row.get("cpu_logical"), maximum=4_096),
            cpu_name=_text(row.get("cpu_name"), 160),
            memory_bytes=_number(row.get("memory_bytes")),
            machine_model=_text(row.get("machine_model"), 160),
            gpu_names=tuple(_text(item, 160) for item in list(row.get("gpu_names") or [])[:8]),
            windows_edition=_text(row.get("windows_edition"), 120),
            system_drive=_text(row.get("system_drive"), 12),
            drive_roots=tuple(_text(item, 24) for item in list(row.get("drive_roots") or [])[:64]),
            paths={_text(key, 48): str(path) for key, path in _mapping(row.get("paths")).items()},
            capabilities={_text(key, 64): bool(enabled) for key, enabled in _mapping(row.get("capabilities")).items()},
        )


@dataclass(frozen=True)
class Finding:
    finding_id: str
    scan_id: str
    rule_id: str
    domain: Domain
    title: str
    summary: str
    severity: Severity = Severity.INFO
    item_count: int = 0
    measured_bytes: int = 0
    reclaimable_bytes: int = 0
    selectable: bool = False
    default_selected: bool = False
    requires_elevation: bool = False
    risk: RiskLevel = RiskLevel.NONE
    undo: UndoQuality = UndoQuality.NOT_APPLICABLE
    action: str = "review"
    evidence: Mapping[str, Any] = field(default_factory=dict)
    candidates: tuple[Candidate, ...] = ()

    def __post_init__(self) -> None:
        if len(self.candidates) > MAX_CANDIDATES_PER_FINDING:
            raise ValueError("finding has too many candidates")
        object.__setattr__(self, "title", _text(self.title, 100))
        object.__setattr__(self, "summary", _text(self.summary, 280))
        object.__setattr__(self, "item_count", _number(self.item_count, maximum=10_000_000))
        object.__setattr__(self, "measured_bytes", _number(self.measured_bytes))
        object.__setattr__(self, "reclaimable_bytes", _number(self.reclaimable_bytes))
        object.__setattr__(self, "default_selected", bool(self.default_selected and self.selectable))

    def to_dict(self, *, include_private: bool = True) -> dict[str, Any]:
        payload = {
            "finding_id": _text(self.finding_id, 80),
            "scan_id": _text(self.scan_id, 80),
            "rule_id": _text(self.rule_id, 100),
            "domain": self.domain.value,
            "title": self.title,
            "summary": self.summary,
            "severity": self.severity.value,
            "item_count": self.item_count,
            "measured_bytes": self.measured_bytes,
            "reclaimable_bytes": self.reclaimable_bytes,
            "selectable": bool(self.selectable),
            "default_selected": bool(self.default_selected),
            "requires_elevation": bool(self.requires_elevation),
            "risk": self.risk.value,
            "undo": self.undo.value,
            "action": _text(self.action, 64),
            "evidence": _bounded_evidence(self.evidence),
            "candidate_count": len(self.candidates),
        }
        if include_private:
            payload["candidates"] = [item.to_dict() for item in self.candidates]
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Finding":
        row = _mapping(value)
        return cls(
            finding_id=_text(row.get("finding_id"), 80),
            scan_id=_text(row.get("scan_id"), 80),
            rule_id=_text(row.get("rule_id"), 100),
            domain=_enum(Domain, row.get("domain"), Domain.HEALTH),
            title=_text(row.get("title"), 100),
            summary=_text(row.get("summary"), 280),
            severity=_enum(Severity, row.get("severity"), Severity.INFO),
            item_count=_number(row.get("item_count"), maximum=10_000_000),
            measured_bytes=_number(row.get("measured_bytes")),
            reclaimable_bytes=_number(row.get("reclaimable_bytes")),
            selectable=bool(row.get("selectable")),
            default_selected=bool(row.get("default_selected")),
            requires_elevation=bool(row.get("requires_elevation")),
            risk=_enum(RiskLevel, row.get("risk"), RiskLevel.NONE),
            undo=_enum(UndoQuality, row.get("undo"), UndoQuality.NOT_APPLICABLE),
            action=_text(row.get("action"), 64) or "review",
            evidence=_bounded_evidence(_mapping(row.get("evidence"))),
            candidates=tuple(
                Candidate.from_dict(item)
                for item in list(row.get("candidates") or [])[:MAX_CANDIDATES_PER_FINDING]
                if isinstance(item, Mapping)
            ),
        )


@dataclass(frozen=True)
class ProgressEvent:
    operation_id: str
    phase: str
    domain: Domain
    label: str
    completed_weight: int
    total_weight: int
    domain_fraction: float
    state: str
    sequence: int
    at: float = field(default_factory=time.time)
    checks: tuple[Mapping[str, Any], ...] = ()

    @property
    def fraction(self) -> float:
        return 0.0 if self.total_weight <= 0 else max(0.0, min(1.0, self.completed_weight / self.total_weight))

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": _text(self.operation_id, 80),
            "phase": _text(self.phase, 40),
            "domain": self.domain.value,
            "label": _text(self.label, 140),
            "completed_weight": _number(self.completed_weight, maximum=1_000_000),
            "total_weight": _number(self.total_weight, maximum=1_000_000),
            "fraction": self.fraction,
            "domain_fraction": max(0.0, min(1.0, float(self.domain_fraction))),
            "state": _text(self.state, 32),
            "sequence": _number(self.sequence, maximum=10_000_000),
            "at": float(self.at),
            "checks": [_bounded_evidence(check) for check in self.checks[:MAX_FINDINGS_PER_SCAN]],
        }


@dataclass(frozen=True)
class ScanResult:
    scan_id: str
    mode: ScanMode
    state: OperationState
    calibration_id: str
    started_at: float
    completed_at: float
    findings: tuple[Finding, ...] = ()
    domains: Mapping[str, str] = field(default_factory=dict)
    error: str = ""
    checks: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if len(self.findings) > MAX_FINDINGS_PER_SCAN:
            raise ValueError("scan has too many findings")

    @property
    def reclaimable_bytes(self) -> int:
        return sum(item.reclaimable_bytes for item in self.findings)

    @property
    def review_count(self) -> int:
        return sum(1 for item in self.findings if item.severity in {Severity.REVIEW, Severity.ATTENTION})

    def to_dict(self, *, include_private: bool = True) -> dict[str, Any]:
        return {
            "scan_id": _text(self.scan_id, 80),
            "mode": self.mode.value,
            "state": self.state.value,
            "calibration_id": _text(self.calibration_id, 80),
            "started_at": float(self.started_at),
            "completed_at": float(self.completed_at),
            "findings": [item.to_dict(include_private=include_private) for item in self.findings],
            "domains": {_text(key, 48): _text(value, 32) for key, value in list(self.domains.items())[:32]},
            "reclaimable_bytes": self.reclaimable_bytes,
            "review_count": self.review_count,
            "error": _text(self.error, 300),
            "checks": [_bounded_evidence(check) for check in self.checks[:MAX_FINDINGS_PER_SCAN]],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScanResult":
        row = _mapping(value)
        return cls(
            scan_id=_text(row.get("scan_id"), 80),
            mode=_enum(ScanMode, row.get("mode"), ScanMode.SAFE),
            state=_enum(OperationState, row.get("state"), OperationState.FAILED),
            calibration_id=_text(row.get("calibration_id"), 80),
            started_at=_float(row.get("started_at")),
            completed_at=_float(row.get("completed_at")),
            findings=tuple(
                Finding.from_dict(item)
                for item in list(row.get("findings") or [])[:MAX_FINDINGS_PER_SCAN]
                if isinstance(item, Mapping)
            ),
            domains={_text(key, 48): _text(state, 32) for key, state in _mapping(row.get("domains")).items()},
            error=_text(row.get("error"), 300),
            checks=tuple(_bounded_evidence(check) for check in list(row.get("checks") or [])[:MAX_FINDINGS_PER_SCAN]
                         if isinstance(check, Mapping)),
        )


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    finding_id: str
    rule_id: str
    title: str
    action: str
    risk: RiskLevel
    undo: UndoQuality
    expected_bytes: int
    requires_elevation: bool
    requires_restart: bool
    candidates: tuple[Candidate, ...]
    subject: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_private: bool = True) -> dict[str, Any]:
        payload = {
            "step_id": _text(self.step_id, 80),
            "finding_id": _text(self.finding_id, 80),
            "rule_id": _text(self.rule_id, 100),
            "title": _text(self.title, 100),
            "action": _text(self.action, 64),
            "risk": self.risk.value,
            "undo": self.undo.value,
            "expected_bytes": _number(self.expected_bytes),
            "requires_elevation": bool(self.requires_elevation),
            "requires_restart": bool(self.requires_restart),
            "candidate_count": len(self.candidates),
        }
        if include_private:
            payload["candidates"] = [item.to_dict() for item in self.candidates]
        if self.subject:
            payload["subject"] = dict(self.subject) if include_private else {k: self.subject[k] for k in ("kind", "name", "drive", "owner") if k in self.subject}
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlanStep":
        row = _mapping(value)
        return cls(
            step_id=_text(row.get("step_id"), 80),
            finding_id=_text(row.get("finding_id"), 80),
            rule_id=_text(row.get("rule_id"), 100),
            title=_text(row.get("title"), 100),
            action=_text(row.get("action"), 64),
            risk=_enum(RiskLevel, row.get("risk"), RiskLevel.NONE),
            undo=_enum(UndoQuality, row.get("undo"), UndoQuality.NOT_APPLICABLE),
            expected_bytes=_number(row.get("expected_bytes")),
            requires_elevation=bool(row.get("requires_elevation")),
            requires_restart=bool(row.get("requires_restart")),
            subject=_mapping(row.get("subject")),
            candidates=tuple(
                Candidate.from_dict(item)
                for item in list(row.get("candidates") or [])[:MAX_CANDIDATES_PER_FINDING]
                if isinstance(item, Mapping)
            ),
        )


@dataclass(frozen=True)
class Plan:
    plan_id: str
    scan_id: str
    calibration_id: str
    created_at: float
    expires_at: float
    steps: tuple[PlanStep, ...]
    digest: str = ""
    context: str = "machine"
    target: str = ""

    def digest_payload(self) -> dict[str, Any]:
        payload = {
            "plan_id": self.plan_id,
            "scan_id": self.scan_id,
            "calibration_id": self.calibration_id,
            "created_at": round(float(self.created_at), 6),
            "expires_at": round(float(self.expires_at), 6),
            "steps": [step.to_dict(include_private=True) for step in self.steps],
        }
        if self.context != "machine" or self.target:
            payload.update(context=self.context, target=self.target)
        return payload

    def with_digest(self) -> "Plan":
        return Plan(
            plan_id=self.plan_id,
            scan_id=self.scan_id,
            calibration_id=self.calibration_id,
            created_at=self.created_at,
            expires_at=self.expires_at,
            steps=self.steps,
            digest=canonical_digest(self.digest_payload()),
            context=self.context,
            target=self.target,
        )

    def to_dict(self, *, include_private: bool = True) -> dict[str, Any]:
        payload = self.digest_payload()
        payload["steps"] = [step.to_dict(include_private=include_private) for step in self.steps]
        payload["digest"] = self.digest or canonical_digest(self.digest_payload())
        payload["expected_bytes"] = sum(step.expected_bytes for step in self.steps)
        payload["has_non_undo"] = any(step.undo == UndoQuality.NONE for step in self.steps)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Plan":
        row = _mapping(value)
        return cls(
            plan_id=_text(row.get("plan_id"), 80),
            scan_id=_text(row.get("scan_id"), 80),
            calibration_id=_text(row.get("calibration_id"), 80),
            created_at=_float(row.get("created_at")),
            expires_at=_float(row.get("expires_at")),
            steps=tuple(PlanStep.from_dict(item) for item in list(row.get("steps") or [])[:MAX_FINDINGS_PER_SCAN] if isinstance(item, Mapping)),
            digest=_text(row.get("digest"), 64).lower(),
            context=_text(row.get("context") or "machine", 24),
            target=str(row.get("target") or "")[:1024],
        )


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    plan_id: str
    state: OperationState
    created_at: float
    completed_at: float
    applied_steps: tuple[str, ...]
    skipped_steps: tuple[str, ...]
    failed_steps: tuple[str, ...]
    reclaimed_bytes: int
    verified: bool
    rollback_available: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": _text(self.receipt_id, 80),
            "plan_id": _text(self.plan_id, 80),
            "state": self.state.value,
            "created_at": float(self.created_at),
            "completed_at": float(self.completed_at),
            "applied_steps": [_text(item, 80) for item in self.applied_steps[:MAX_FINDINGS_PER_SCAN]],
            "skipped_steps": [_text(item, 80) for item in self.skipped_steps[:MAX_FINDINGS_PER_SCAN]],
            "failed_steps": [_text(item, 80) for item in self.failed_steps[:MAX_FINDINGS_PER_SCAN]],
            "reclaimed_bytes": _number(self.reclaimed_bytes),
            "verified": bool(self.verified),
            "rollback_available": bool(self.rollback_available),
            "detail": _text(self.detail, 300),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Receipt":
        row = _mapping(value)
        return cls(
            receipt_id=_text(row.get("receipt_id"), 80),
            plan_id=_text(row.get("plan_id"), 80),
            state=_enum(OperationState, row.get("state"), OperationState.FAILED),
            created_at=_float(row.get("created_at")),
            completed_at=_float(row.get("completed_at")),
            applied_steps=tuple(_text(item, 80) for item in list(row.get("applied_steps") or [])[:MAX_FINDINGS_PER_SCAN]),
            skipped_steps=tuple(_text(item, 80) for item in list(row.get("skipped_steps") or [])[:MAX_FINDINGS_PER_SCAN]),
            failed_steps=tuple(_text(item, 80) for item in list(row.get("failed_steps") or [])[:MAX_FINDINGS_PER_SCAN]),
            reclaimed_bytes=_number(row.get("reclaimed_bytes")),
            verified=bool(row.get("verified")),
            rollback_available=bool(row.get("rollback_available")),
            detail=_text(row.get("detail"), 300),
        )


def _bounded_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, raw in list(value.items())[:MAX_EVIDENCE_ITEMS]:
        safe_key = _text(key, 64)
        if not safe_key:
            continue
        if isinstance(raw, bool):
            clean[safe_key] = raw
        elif isinstance(raw, int):
            clean[safe_key] = _number(raw)
        elif isinstance(raw, float):
            clean[safe_key] = max(0.0, raw)
        elif isinstance(raw, (list, tuple)):
            clean[safe_key] = [_text(item, 120) for item in list(raw)[:32]]
        elif raw is None:
            clean[safe_key] = None
        else:
            clean[safe_key] = _text(raw, 200)
    return clean


def finding_ids(findings: Iterable[Finding]) -> tuple[str, ...]:
    return tuple(item.finding_id for item in findings)
