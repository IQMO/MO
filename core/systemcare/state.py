"""Device-local persistence and operation coordination for SystemCare."""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from core.state.paths import resolve_state_path
from core.state.sqlite import connect_state_db
from core.utils.atomic_write import atomic_write_json, atomic_write_text

from .config import normalized_systemcare_preferences
from .models import Calibration, Plan, Receipt, ScanResult


DB_SCHEMA_VERSION = 1
OBSERVATION_FRESHNESS_SECONDS = 300
_THREAD_OPERATION_LOCK = threading.RLock()


class SystemCareOperationBusy(RuntimeError):
    """Raised when another process or thread owns SystemCare work."""


class SystemCareState:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config if config is not None else {}
        self.db_path = Path(resolve_state_path("memory/systemcare.sqlite", self.config))
        self.run_dir = Path(resolve_state_path("run/systemcare", self.config))
        self.log_path = Path(resolve_state_path("logs/systemcare.jsonl", self.config))
        self.lock_path = self.run_dir / "operation.lock"
        self.active_path = self.run_dir / "active.json"
        self.cancel_path = self.run_dir / "cancel.request"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        connection = connect_state_db(self.db_path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calibrations (
                    calibration_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    refresh_after REAL NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_systemcare_calibration_created
                    ON calibrations(created_at DESC);
                CREATE TABLE IF NOT EXISTS scans (
                    scan_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_systemcare_scans_created
                    ON scans(created_at DESC);
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    scan_id TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_systemcare_plans_created
                    ON plans(created_at DESC);
                CREATE TABLE IF NOT EXISTS receipts (
                    receipt_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_systemcare_receipts_created
                    ON receipts(created_at DESC);
                CREATE TABLE IF NOT EXISTS observations (
                    context TEXT NOT NULL,
                    target TEXT NOT NULL,
                    section TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(context, target, section)
                );
                CREATE TABLE IF NOT EXISTS backups (
                    step_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )
            db.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(DB_SCHEMA_VERSION),),
            )

    def save_calibration(self, calibration: Calibration) -> None:
        payload = _json(calibration.to_dict(include_paths=True))
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO calibrations
                   (calibration_id, device_id, created_at, refresh_after, fingerprint, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    calibration.calibration_id,
                    calibration.device_id,
                    calibration.created_at,
                    calibration.refresh_after,
                    calibration.fingerprint,
                    payload,
                ),
            )
            db.execute(
                """DELETE FROM calibrations WHERE calibration_id NOT IN
                   (SELECT calibration_id FROM calibrations ORDER BY created_at DESC LIMIT 4)"""
            )

    def latest_calibration(self) -> Calibration | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM calibrations ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return Calibration.from_dict(_loads(row["payload"])) if row else None

    def save_scan(self, scan: ScanResult) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO scans(scan_id, mode, state, created_at, payload)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    scan.scan_id,
                    scan.mode.value,
                    scan.state.value,
                    scan.started_at,
                    _json(scan.to_dict(include_private=True)),
                ),
            )
        self.prune()

    def scan(self, scan_id: str) -> ScanResult | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM scans WHERE scan_id = ?", (str(scan_id),)).fetchone()
        return ScanResult.from_dict(_loads(row["payload"])) if row else None

    def latest_scan(self, *, complete_only: bool = False) -> ScanResult | None:
        sql = "SELECT payload FROM scans"
        params: tuple[Any, ...] = ()
        if complete_only:
            sql += " WHERE state IN ('ready', 'completed')"
        sql += " ORDER BY created_at DESC LIMIT 1"
        with self._connect() as db:
            row = db.execute(sql, params).fetchone()
        return ScanResult.from_dict(_loads(row["payload"])) if row else None

    def recent_scans_for_history(self, limit: int = 12) -> list[ScanResult]:
        """The latest scans WITHOUT their saved candidates, for the history's summary rows only:
        rebuilding thousands of private candidates to show counts held every snapshot ~1 s."""
        bounded = max(1, min(50, int(limit or 12)))
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM scans ORDER BY created_at DESC LIMIT ?", (bounded,)
            ).fetchall()
        scans = []
        for row in rows:
            payload = _loads(row["payload"])
            payload["findings"] = [{key: value for key, value in finding.items() if key != "candidates"}
                                   for finding in payload.get("findings") or [] if isinstance(finding, dict)]
            scans.append(ScanResult.from_dict(payload))
        return scans

    def save_plan(self, plan: Plan) -> None:
        actual = plan.with_digest() if not plan.digest else plan
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO plans
                   (plan_id, scan_id, digest, created_at, expires_at, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    actual.plan_id,
                    actual.scan_id,
                    actual.digest,
                    actual.created_at,
                    actual.expires_at,
                    _json(actual.to_dict(include_private=True)),
                ),
            )

    def plan(self, plan_id: str) -> Plan | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM plans WHERE plan_id = ?", (str(plan_id),)).fetchone()
        return Plan.from_dict(_loads(row["payload"])) if row else None

    def latest_plan(self) -> Plan | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM plans ORDER BY created_at DESC LIMIT 1").fetchone()
        return Plan.from_dict(_loads(row["payload"])) if row else None

    def save_receipt(self, receipt: Receipt) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO receipts
                   (receipt_id, plan_id, state, created_at, payload)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    receipt.receipt_id,
                    receipt.plan_id,
                    receipt.state.value,
                    receipt.created_at,
                    _json(receipt.to_dict()),
                ),
            )
        self.prune()

    def receipt(self, receipt_id: str) -> Receipt | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM receipts WHERE receipt_id = ?", (str(receipt_id),)).fetchone()
        return Receipt.from_dict(_loads(row["payload"])) if row else None

    def latest_receipt(self) -> Receipt | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM receipts ORDER BY created_at DESC LIMIT 1").fetchone()
        return Receipt.from_dict(_loads(row["payload"])) if row else None

    def receipt_for_plan(self, plan_id: str) -> Receipt | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM receipts WHERE plan_id = ? ORDER BY created_at DESC LIMIT 1",
                (str(plan_id),),
            ).fetchone()
        return Receipt.from_dict(_loads(row["payload"])) if row else None

    def save_backup(self, step_id: str, payload: Mapping[str, Any]) -> None:
        """Persist originals before mutation. Unrestored recovery rows are never pruned."""
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO backups VALUES (?, ?, ?)",
                       (step_id, time.time(), _json(dict(payload))))

    def backup(self, step_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM backups WHERE step_id=?", (step_id,)).fetchone()
        return _loads(row["payload"]) if row else None

    def recovery_rows(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("""SELECT step_id,payload FROM backups
                WHERE json_extract(payload,'$.state')!='restored'
                  AND coalesce(json_extract(payload,'$.reversible'),1)=1
                ORDER BY created_at DESC LIMIT 200""").fetchall()
        return [{"step_id": row["step_id"], **_loads(row["payload"])} for row in rows]

    def active_game(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("""SELECT step_id,payload FROM backups
                WHERE json_extract(payload,'$.action')='game_on'
                  AND json_extract(payload,'$.state')!='restored'
                ORDER BY created_at DESC LIMIT 1""").fetchone()
        return {"step_id": row["step_id"], **_loads(row["payload"])} if row else None

    def latest_game(self) -> dict[str, Any] | None:
        """Return the latest durable Game Session journal, including restored sessions."""
        with self._connect() as db:
            row = db.execute("""SELECT step_id,payload FROM backups
                WHERE json_extract(payload,'$.action')='game_on'
                ORDER BY created_at DESC LIMIT 1""").fetchone()
        return {"step_id": row["step_id"], **_loads(row["payload"])} if row else None

    def recent_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM receipts ORDER BY created_at DESC LIMIT ?",
                              (max(1, min(200, int(limit))),)).fetchall()
        return [_loads(row["payload"]) for row in rows]

    def prune(self) -> None:
        settings = normalized_systemcare_preferences(self.config)
        retention_days = settings["retention_days"]
        scan_limit = settings["history_limit"]
        cutoff = time.time() - retention_days * 86_400
        with self._connect() as db:
            db.execute(
                """DELETE FROM scans WHERE created_at < ? AND scan_id NOT IN
                   (SELECT scan_id FROM scans ORDER BY created_at DESC LIMIT ?)""",
                (cutoff, min(scan_limit, 8)),
            )
            db.execute(
                """DELETE FROM scans WHERE scan_id NOT IN
                   (SELECT scan_id FROM scans ORDER BY created_at DESC LIMIT ?)""",
                (scan_limit,),
            )
            protected = """SELECT DISTINCT p.plan_id FROM plans p,json_each(p.payload,'$.steps') s
                JOIN backups b ON b.step_id=json_extract(s.value,'$.step_id')
                WHERE json_extract(b.payload,'$.state')!='restored'
                  AND coalesce(json_extract(b.payload,'$.reversible'),1)=1"""
            db.execute("DELETE FROM plans WHERE expires_at < ? AND plan_id NOT IN (" + protected + ")", (time.time() - 86_400,))
            db.execute(
                """DELETE FROM receipts WHERE receipt_id NOT IN
                   (SELECT receipt_id FROM receipts ORDER BY created_at DESC LIMIT ?)
                   AND plan_id NOT IN (""" + protected + ")",
                (scan_limit,),
            )
            # Completed originals follow retention once no retained receipt needs
            # them. Unrestored reversible state remains protected indefinitely.
            db.execute("""DELETE FROM backups WHERE created_at < ?
                AND (json_extract(payload,'$.state')='restored'
                     OR json_extract(payload,'$.reversible')=0)
                AND step_id NOT IN (
                    SELECT s.value FROM receipts r,json_each(r.payload,'$.applied_steps') s
                    UNION SELECT s.value FROM receipts r,json_each(r.payload,'$.failed_steps') s
                    UNION SELECT s.value FROM receipts r,json_each(r.payload,'$.skipped_steps') s
                )""", (cutoff,))

    @contextmanager
    def operation(self, operation_id: str, kind: str, *, context: str = "", section: str = "") -> Iterator[None]:
        """Serialize scan/apply work across MO processes and publish bounded status."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with _nonblocking_operation_lock(self.lock_path):
            # Clear a stale request before publishing the new operation. Once
            # active.json is visible, a concurrent cancel must never be erased.
            self.clear_cancel()
            scope = {"context": context if context in {"machine", "mo", "server", "projects"} else "",
                     "section": section if section.isascii() and section.isidentifier() and len(section) <= 32 else ""}
            payload = {
                "operation_id": str(operation_id)[:80],
                "kind": str(kind)[:32],
                "pid": os.getpid(),
                "started_at": time.time(),
                **scope,
            }
            atomic_write_json(self.active_path, payload, indent=2, ensure_ascii=False)
            started = time.monotonic()
            cpu_started = time.process_time()
            self.append_log("operation_started", operation_id=operation_id, kind=kind, **scope)
            outcome = "returned"
            try:
                from core.runtime.backend_monitor import monitor_context, monitor_phase
                with monitor_context(operation_id=operation_id, kind=kind, **scope), monitor_phase("systemcare_operation"):
                    yield
            except BaseException:
                outcome = "raised"
                raise
            finally:
                self.append_log("operation_finished", operation_id=operation_id, kind=kind,
                                outcome=outcome, elapsed_ms=int((time.monotonic() - started) * 1000),
                                process_cpu_ms=int((time.process_time() - cpu_started) * 1000), **scope)
                self.clear_cancel(operation_id)
                try:
                    current = self.active_operation()
                    if current.get("operation_id") == operation_id:
                        self.active_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def active_operation(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.active_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}
            pid = int(raw.get("pid") or 0)
            if pid > 0 and not process_is_alive(pid):
                return {**raw, "stale": True}
            return raw
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            return {}

    def request_cancel(self, operation_id: str = "") -> bool:
        active = self.active_operation()
        if not active or active.get("stale"):
            return False
        target = str(operation_id or active.get("operation_id") or "").strip()
        if not target or target != str(active.get("operation_id") or ""):
            return False
        atomic_write_text(self.cancel_path, target + "\n", encoding="utf-8")
        return True

    def cancel_requested(self, operation_id: str) -> bool:
        try:
            return self.cancel_path.read_text(encoding="utf-8").strip() == str(operation_id)
        except OSError:
            return False

    def clear_cancel(self, operation_id: str = "") -> None:
        try:
            if not self.cancel_path.exists():
                return
            current = self.cancel_path.read_text(encoding="utf-8").strip()
            if not operation_id or current == str(operation_id):
                self.cancel_path.unlink(missing_ok=True)
        except OSError:
            pass

    def save_observation(self, context: str, target: str, section: str, payload: Mapping[str, Any]) -> None:
        encoded = _json(payload)
        if len(encoded.encode("utf-8")) > 1_000_000:
            raise ValueError("SystemCare observation exceeds its bounded size")
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)",
                       (context, target, section, float(payload.get("at") or time.time()), encoded))
            db.execute("DELETE FROM observations WHERE rowid NOT IN (SELECT rowid FROM observations ORDER BY observed_at DESC LIMIT 96)")

    def observations(self, context: str, target: str = "") -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute("SELECT section,observed_at,payload FROM observations WHERE context=? AND target=?",
                              (context, target)).fetchall()
        return {row[0]: {**_loads(row[2]), "observed_at": row[1], "stale": time.time() - row[1] > OBSERVATION_FRESHNESS_SECONDS} for row in rows}

    def append_log(self, event: str, **fields: Any) -> None:
        """Write a value-safe diagnostic row; callers must not pass paths/content."""
        row = {"at": time.time(), "event": str(event or "")[:64]}
        for key, value in list(fields.items())[:16]:
            safe_key = str(key or "")[:48]
            if isinstance(value, (bool, int, float)):
                row[safe_key] = value
            else:
                row[safe_key] = " ".join(str(value or "").split())[:160]
        try:
            from core.runtime.backend_monitor import get_monitor
            get_monitor().emit("systemcare_operation", row)
        except Exception:
            pass  # Diagnostic transport cannot alter the durable action owner.
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            encoded = _json(row) + "\n"
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
            if self.log_path.stat().st_size > 1_000_000:
                lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-1_000:]
                atomic_write_text(self.log_path, "\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            return


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str) -> dict[str, Any]:
    try:
        raw = json.loads(str(value or "{}"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            try:
                code = ctypes.c_ulong()
                return bool(ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(code)) and code.value == 259)
            finally:
                ctypes.windll.kernel32.CloseHandle(process)
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


@contextmanager
def _nonblocking_operation_lock(path: Path) -> Iterator[None]:
    """Claim the SystemCare operation lane without making a caller wait."""
    if not _THREAD_OPERATION_LOCK.acquire(blocking=False):
        raise SystemCareOperationBusy("another SystemCare operation is active")
    handle = None
    locked = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except (BlockingIOError, OSError) as exc:
            raise SystemCareOperationBusy("another SystemCare operation is active") from exc
        yield
    finally:
        if handle is not None:
            if locked:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()
        _THREAD_OPERATION_LOCK.release()
