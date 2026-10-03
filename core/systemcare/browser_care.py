"""Closed-profile browser history care; credentials and site/session data stay owned by browsers."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from .catalog import rule_for_id


_HISTORY_TABLES = frozenset({
    "urls", "visits", "visit_source", "keyword_search_terms", "segments", "segment_usage",
    "content_annotations", "context_annotations", "visited_links", "clusters", "clusters_and_visits",
    "cluster_keywords", "cluster_visit_duplicates", "history_sync_metadata", "history_metadata",
    "journeys_metadata", "clusters_and_keywords", "cluster_content_annotations",
})
_DOWNLOAD_TABLES = frozenset({"downloads", "downloads_url_chains", "downloads_slices"})


def _owner(adapter: Any, subject: dict[str, Any]) -> Path:
    browser, profile = str(subject.get("browser", "")), str(subject.get("profile", ""))
    if browser not in {"chrome", "edge", "brave"} or not (profile == "Default" or re.fullmatch(r"Profile \d+", profile)):
        raise ValueError("Select a supported exact browser profile")
    if subject.get("category") not in {"history", "downloads"}:
        raise ValueError("Credentials, site data and sessions are protected")
    rule = rule_for_id("storage.browser_" + browser)
    if adapter._owner_busy(rule, fresh=True):
        raise ValueError("Close this browser before inspecting or changing its history")
    root = Path(adapter._discover_paths()[rule.root_key])
    path = root / rule.relative_root / profile / "History"
    adapter._check_unlinked_owner(root, path)
    if not path.is_file() or path.stat().st_size > 128 * 1024**2:
        raise ValueError("History database is unavailable or exceeds the reviewed read budget")
    for suffix in ("-wal", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("Browser has uncheckpointed history; let its native owner close it cleanly")
    return path


def _connect(path: Path, *, write: bool = False) -> sqlite3.Connection:
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=1)
    db.execute("PRAGMA trusted_schema=OFF")
    db.execute("PRAGMA cache_size=-2048")
    return db


def _tables(db: sqlite3.Connection, category: str) -> tuple[str, ...]:
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # A new schema is inspect-only until its ownership is understood. Never
    # silently clear a guessed subset and report complete history removal.
    if tables - _HISTORY_TABLES - _DOWNLOAD_TABLES - {"meta", "sqlite_sequence"}:
        raise ValueError("This history schema needs its browser's native cleanup owner")
    if db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]:
        raise ValueError("History triggers require their native browser owner")
    required = {"urls", "visits"} if category == "history" else {"downloads"}
    if not required <= tables:
        raise ValueError("Complete supported history evidence is unavailable")
    allowed = _HISTORY_TABLES if category == "history" else _DOWNLOAD_TABLES
    return tuple(sorted(tables & allowed))


def capture_browser(adapter: Any, subject: dict[str, Any]) -> dict[str, Any]:
    path = _owner(adapter, subject)
    with closing(_connect(path)) as db:
        return _snapshot(path, db, subject["category"])


def _snapshot(path: Path, db: sqlite3.Connection, category: str) -> dict[str, Any]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    tables = _tables(db, category)
    counts = {table: db.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0] for table in tables}
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError("History changed during inspection")
    return {"path": str(path), "bytes": after.st_size, "mtime_ns": after.st_mtime_ns,
            "file_id": after.st_ino, "sha256": digest.hexdigest(), "counts": counts}


def inspect_browsers(adapter: Any, *, cancelled: Callable[[], bool]) -> dict[str, Any]:
    rows, unavailable = [], []
    deadline, remaining_bytes, bounded = time.monotonic() + 10, 256 * 1024**2, False
    paths = adapter._discover_paths()
    for browser in ("chrome", "edge", "brave"):
        rule = rule_for_id("storage.browser_" + browser)
        base = Path(paths.get(rule.root_key, "")) / rule.relative_root
        if not base.is_dir():
            continue
        from itertools import islice
        try:
            profiles = list(islice(base.iterdir(), 65))
        except OSError:
            unavailable.append({"browser": browser, "detail": "Profile inventory is inaccessible"})
            continue
        for profile in profiles:
            if cancelled():
                from .windows import ScanCancelled
                raise ScanCancelled("browser inspection cancelled")
            if not (profile.name == "Default" or re.fullmatch(r"Profile \d+", profile.name)):
                continue
            if not (profile / "History").is_file():
                continue
            for category in ("history", "downloads"):
                if time.monotonic() >= deadline or remaining_bytes <= 0:
                    bounded = True
                    break
                subject = {"kind": "browser", "browser": browser, "profile": profile.name, "category": category}
                try:
                    original = capture_browser(adapter, subject)
                except (OSError, ValueError, sqlite3.Error) as exc:
                    unavailable.append({"browser": browser, "profile": profile.name, "category": category,
                                        "detail": str(exc)})
                    continue
                primary = "visits" if category == "history" else "downloads"
                remaining_bytes -= original["bytes"]
                rows.append({"name": browser.title() + " · " + profile.name + " · " + category,
                             "state": "Review" if original["counts"][primary] else "empty", "subject": subject,
                             "records": original["counts"][primary], "eligible": bool(sum(original["counts"].values())),
                             "detail": "Local " + category + " records only; passwords, bookmarks, cookies, site data, sessions and downloaded files stay protected"})
    return {"state": "partial", "rows": rows, "unavailable": unavailable, "bounded": bounded, "at": time.time(),
            "detail": "Closed supported Chromium profiles, at most 65 entries per browser; 10-second / 256 MiB discovery budget plus the current bounded database read. Local history and download-history are separate; cloud/sync copies and Firefox Places remain with their native owners. No credential or site/session cleanup."}


def clear_browser(adapter: Any, subject: dict[str, Any], expected: dict[str, Any], *, cancelled: Callable[[], bool]) -> str:
    path = _owner(adapter, subject)
    with closing(_connect(path, write=True)) as db:
        db.execute("BEGIN EXCLUSIVE")
        try:
            if _snapshot(path, db, subject["category"]) != expected:
                raise ValueError("Browser history changed after review")
            tables = _tables(db, subject["category"])
            for table in tables:
                if cancelled():
                    from .windows import ScanCancelled
                    raise ScanCancelled("history cleanup cancelled before commit")
                db.execute('DELETE FROM "' + table + '"')
            if any(db.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0] for table in tables):
                raise RuntimeError("Selected history records remain")
            if adapter._owner_busy(rule_for_id("storage.browser_" + subject["browser"]), fresh=True):
                raise ValueError("Browser reopened during cleanup; the transaction is rolled back")
            if cancelled():
                from .windows import ScanCancelled
                raise ScanCancelled("history cleanup cancelled before commit")
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return "Selected local " + subject["category"] + " records removed; protected browser data and downloaded files retained. Cloud/sync copies are not changed."
