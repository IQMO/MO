"""Project-scoped Git evidence and reusable consolidation findings.

The structural graph still owns current code relationships. This derived SQLite
index joins historical changes to source paths; it never certifies behavior or
turns an analyst's finding into project policy. Durable findings stay in the
existing private work/review domain, so deleting a cache cannot erase them.

Project lifecycle maintenance calls this builder in the existing graph worker;
``python -m core.graph.history build`` remains the explicit CLI. Searches never
build implicitly. ``record --input FILE`` accepts a source-backed finding as
JSON; ``search QUERY`` and the normal code-search/context paths retrieve it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from ..diagnostics.source_inventory import discover_source_paths
from ..runtime.lock import file_byte_lock
from ..runtime.subprocess_flags import apply_windows_hidden_process_flags
from ..state.paths import project_cache_dir, resolve_state_path
from ..state.sqlite import connect_state_db
from ..utils.atomic_write import atomic_write_json
from ..utils.file_hash import file_sha256
from ..utils.text_safety import redact_secret_values

SCHEMA_VERSION = "1"
PATCH_CHAR_LIMIT = 262144
_RECORD_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()


def _git(root: Path, *args: str, data: bytes | None = None, ok: tuple[int, ...] = (0,)) -> bytes:
    kwargs: dict[str, Any] = {"cwd": root, "input": data, "capture_output": True, "timeout": 90}
    apply_windows_hidden_process_flags(kwargs)
    # show receives source filenames; check-ignore's stdin is already literal
    # and that command explicitly rejects Git's literal-pathspecs global flag.
    flags = ["--literal-pathspecs"] if args[0] == "show" else []
    result = subprocess.run(["git", "--no-pager", *flags, *args], **kwargs)
    if result.returncode not in ok:
        # Git stderr may contain private paths or remote credentials.
        raise RuntimeError(f"Git {args[0]} failed (exit {result.returncode})")
    return result.stdout


def _root(root: str | Path | None) -> Path:
    base = Path(root or Path.cwd()).resolve()
    return Path(_git(base, "rev-parse", "--show-toplevel").decode().strip()).resolve()


def _locations(root: Path) -> tuple[Path, Path]:
    cache = project_cache_dir("project_history", root)
    records = Path(resolve_state_path("memory/work/reviews/project-history")) / cache.name
    return cache / "history.sqlite", records


def _safe_path(value: str) -> bool:
    from .code_graph import indexable_source_path
    from ..tooling.sandbox import secret_read_path_kind

    path = PurePosixPath(value)
    return bool(
        value and "\\" not in value and ":" not in value and not path.is_absolute()
        and not any(part.casefold() in {"..", ".git", "personal", "operator", "tmp", "tests"} for part in path.parts)
        and not secret_read_path_kind(value) and indexable_source_path(value)
    )


def _allowed_paths(root: Path, paths: list[str]) -> set[str]:
    candidates = sorted({p for p in paths if _safe_path(p)})
    if not candidates:
        return set()
    ignored = _git(
        root, "check-ignore", "--no-index", "-z", "--stdin",
        data=("\0".join(candidates) + "\0").encode(), ok=(0, 1),
    ).decode("utf-8", "replace").split("\0")
    return set(candidates) - set(ignored)


def _connect(path: Path, *, write: bool = False) -> sqlite3.Connection:
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_state_db(path) if write else sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    if write:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commits (
                oid TEXT PRIMARY KEY, parents TEXT, recorded_at INTEGER, message TEXT,
                reachable INTEGER NOT NULL DEFAULT 0, truncated INTEGER NOT NULL DEFAULT 0,
                excluded_paths INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS changes (
                oid TEXT NOT NULL, path TEXT NOT NULL, old_path TEXT NOT NULL, kind TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS changes_path ON changes(path);
            CREATE INDEX IF NOT EXISTS changes_oid ON changes(oid);
            CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
                key UNINDEXED, kind UNINDEXED, text, tokenize='porter unicode61');
        """)
        version = connection.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        if version and version[0] != SCHEMA_VERSION:
            connection.close()
            raise ValueError("History index schema changed; rebuild its derived cache")
        connection.execute("INSERT OR REPLACE INTO meta VALUES ('version', ?)", (SCHEMA_VERSION,))
        connection.commit()
    return connection


def _replace_search(connection: sqlite3.Connection, key: str, kind: str, text: str) -> None:
    connection.execute("DELETE FROM search WHERE key=? AND kind=?", (key, kind))
    connection.execute("INSERT INTO search VALUES (?, ?, ?)", (key, kind, text))


def _commit_metadata(root: Path, head: str) -> list[tuple[str, str, str, str]]:
    raw = _git(root, "log", "--topo-order", "--reverse", "--format=%H%x00%P%x00%ct%x00%B", "-z", head)
    fields = raw.decode("utf-8", "replace").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 4:
        raise ValueError("Incomplete Git commit metadata")
    return [tuple(fields[i:i + 4]) for i in range(0, len(fields), 4)]


def _changes(root: Path, oid: str, parents: str) -> list[tuple[str, str, str]]:
    refs = [parents.split()[0], oid] if parents else [oid]
    fields = _git(root, "diff-tree", "--root", "-r", "-M", "--no-commit-id", "--name-status", "-z", *refs).decode("utf-8", "replace").split("\0")
    rows: list[tuple[str, str, str]] = []
    position = 0
    while position < len(fields) and fields[position]:
        kind, path = fields[position:position + 2]
        position += 2
        old_path = ""
        if kind.startswith(("R", "C")):
            old_path, path = path, fields[position]
            position += 1
        rows.append((path, old_path, kind))
    return rows


def _commit_patch(root: Path, oid: str, paths: list[str]) -> tuple[str, bool]:
    # Windows process arguments are bounded. Read admitted paths in small
    # batches instead of ever reading excluded files with an unrestricted show.
    pieces: list[str] = []
    chars = 0
    truncated = False
    for start in range(0, len(paths), 32):
        patch = _git(
            root, "show", "--format=", "--no-ext-diff", "--no-textconv",
            "--diff-merges=first-parent", "--unified=2", oid, "--", *paths[start:start + 32],
        ).decode("utf-8", "replace")
        safe = redact_secret_values(patch)
        remaining = max(0, PATCH_CHAR_LIMIT - chars)
        pieces.append(safe[:remaining])
        chars += len(safe)
        truncated = truncated or len(safe) > remaining
    return "\n".join(pieces), truncated


def build_history_index(root: str | Path | None = None, *, progress: Callable[[dict[str, int]], None] | None = None) -> dict[str, Any]:
    """Index every reachable commit once; readers retain explicit build coverage."""
    from .code_graph import SOURCE_PATH_POLICY_VERSION

    base = _root(root)
    database, records = _locations(base)
    with file_byte_lock(database.with_suffix(".lock"), _BUILD_LOCK), closing(_connect(database, write=True)) as connection:
        head = _git(base, "rev-parse", "HEAD").decode().strip()
        commits = _commit_metadata(base, head)
        stored_paths = {row[0] for row in connection.execute("SELECT path FROM changes UNION SELECT old_path FROM changes WHERE old_path<>''")}
        excluded_now = stored_paths - _allowed_paths(base, list(stored_paths))
        meta = dict(connection.execute("SELECT key, value FROM meta"))
        affected = set()
        if meta.get("source_policy_version") != SOURCE_PATH_POLICY_VERSION:
            # Newly supported formats can occur in already-indexed commits,
            # including deleted files. Their exclusion counts identify which
            # records need revisiting without rescanning unaffected patches.
            affected.update(row[0] for row in connection.execute("SELECT oid FROM commits WHERE excluded_paths>0"))
        # A newly ignored path must lose its old searchable content too.
        for path in excluded_now:
            affected.update(row[0] for row in connection.execute("SELECT DISTINCT oid FROM changes WHERE path=? OR old_path=?", (path, path)))
        with connection:
            for oid in affected:
                connection.execute("DELETE FROM search WHERE key=? AND kind='commit'", (oid,))
                connection.execute("DELETE FROM changes WHERE oid=?", (oid,))
                connection.execute("DELETE FROM commits WHERE oid=?", (oid,))
        known = {row[0] for row in connection.execute("SELECT oid FROM commits")}
        indexed = 0
        with connection:
            connection.execute("INSERT OR REPLACE INTO meta VALUES ('building_head', ?)", (head,))
        for oid, parents, recorded_at, message in commits:
            if oid in known:
                continue
            changed = _changes(base, oid, parents)
            allowed = _allowed_paths(base, [p for path, old, _ in changed for p in (path, old) if p])
            admitted = [(p, old, kind) for p, old, kind in changed if p in allowed and (not old or old in allowed)]
            paths = sorted({p for path, old, _ in admitted for p in (path, old) if p})
            patch, truncated = _commit_patch(base, oid, paths)
            message = redact_secret_values(message)
            searchable = message + "\n" + "\n".join(paths) + "\n" + patch
            with connection:
                connection.execute("INSERT INTO commits VALUES (?, ?, ?, ?, 0, ?, ?)", (
                    oid, parents, int(recorded_at), message, int(truncated), len(changed) - len(admitted),
                ))
                connection.executemany("INSERT INTO changes VALUES (?, ?, ?, ?)", ((oid, p, old, kind) for p, old, kind in admitted))
                # Known commits were skipped; policy-invalidated rows were
                # removed above. A replacement would scan FTS for an absent key.
                connection.execute("INSERT INTO search VALUES (?, ?, ?)", (oid, "commit", searchable))
            indexed += 1
            if progress and (indexed % 25 == 0 or indexed == len(commits) - len(known)):
                progress({"processed": indexed, "discovered": len(commits)})
        with connection:
            connection.execute("UPDATE commits SET reachable=0")
            connection.executemany("UPDATE commits SET reachable=1 WHERE oid=?", ((row[0],) for row in commits))
            connection.execute("DELETE FROM search WHERE kind='finding'")
            for path in sorted(records.glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                _replace_search(connection, record["id"], "finding", _finding_text(record))
            connection.execute("INSERT OR REPLACE INTO meta VALUES ('indexed_head', ?)", (head,))
            connection.execute("INSERT OR REPLACE INTO meta VALUES ('source_policy_version', ?)", (SOURCE_PATH_POLICY_VERSION,))
            connection.execute("DELETE FROM meta WHERE key='building_head'")
        result = history_status(base)
        result["newly_indexed"] = indexed
        return result


def history_status(root: str | Path | None = None) -> dict[str, Any]:
    from .code_graph import SOURCE_PATH_POLICY_VERSION

    base = _root(root)
    database, _ = _locations(base)
    head = _git(base, "rev-parse", "HEAD").decode().strip()
    result: dict[str, Any] = {
        "available": database.is_file(), "scope": "local HEAD ancestry", "git_head": head,
        "discovered_commits": int(_git(base, "rev-list", "--count", head)),
        "shallow": _git(base, "rev-parse", "--is-shallow-repository").strip() == b"true",
        "indexed_commits": 0, "indexed_head": "", "stale": True,
        "github_discussions_indexed": False, "patch_char_limit": PATCH_CHAR_LIMIT,
    }
    if not database.is_file():
        return result
    with closing(_connect(database)) as connection:
        meta = dict(connection.execute("SELECT key, value FROM meta"))
        result.update(indexed_head=meta.get("indexed_head", ""), building=bool(meta.get("building_head")))
        counts = connection.execute("SELECT count(*), coalesce(sum(truncated),0), coalesce(sum(excluded_paths),0) FROM commits WHERE reachable=1").fetchone()
        result.update(indexed_commits=counts[0], truncated_patches=counts[1], excluded_path_changes=counts[2])
        result["recorded_findings"] = connection.execute("SELECT count(*) FROM search WHERE kind='finding'").fetchone()[0]
        result["source_policy_changed"] = meta.get("source_policy_version") != SOURCE_PATH_POLICY_VERSION
        result["stale"] = result["indexed_head"] != head or result["building"] or result["source_policy_changed"]
    return result


def _current_paths(root: Path) -> set[str]:
    return _allowed_paths(root, [path for path, _ in discover_source_paths(root)])


def _fingerprints(root: Path, paths: list[str]) -> dict[str, str]:
    allowed = _current_paths(root)
    result: dict[str, str] = {}
    for relative in paths:
        path = (root / relative).resolve()
        if relative not in allowed or root not in path.parents or path.relative_to(root).as_posix() not in allowed or not path.is_file():
            raise ValueError("Finding evidence must name existing, admitted project source files")
        result[relative] = file_sha256(path)
    return result


def _finding_text(record: dict[str, Any]) -> str:
    return "\n".join([
        record["owner"], record.get("symbol", ""), record["description"],
        " ".join(record.get("aliases", [])), record["decision"], record.get("intent", ""),
    ])


def _read_reference(reference: dict[str, Any], *, capture: bool = False) -> dict[str, Any]:
    """Resolve only explicitly linked private evidence through its existing owner."""
    if not isinstance(reference, dict):
        raise ValueError("Each source reference must be an object")
    if reference.get("kind") == "user_message":
        from ..session.sessions import read_saved_user_message

        return read_saved_user_message(reference, capture=capture)
    if reference.get("kind") != "taskboard":
        raise ValueError("source_refs kind must be user_message or taskboard")
    from ..tasking.task_board import read_recent_snapshots

    identity = {key: str(reference.get(key) or "") for key in ("session_id", "turn_id", "board_id", "task_id")}
    if not all(identity.values()):
        raise ValueError("A taskboard reference needs session_id, turn_id, board_id and task_id")
    rows = read_recent_snapshots(
        limit=1, updated_at=reference.get("snapshot_updated_at"),
        **{key: identity[key] for key in ("session_id", "turn_id", "board_id")},
    )
    if not rows:
        return {"state": "source_unavailable"}
    task = next((row for row in rows[-1].get("tasks", []) if row.get("id") == identity["task_id"]), None)
    if task is None:
        return {"state": "task_unavailable"}
    digest = hashlib.sha256(json.dumps(task, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    expected = reference.get("content_sha256")
    safe_task = json.loads(redact_secret_values(json.dumps(task)))
    return {
        "state": "source_changed" if expected and expected != digest else "available",
        "reference": {"kind": "taskboard", **identity, "content_sha256": digest,
                      "snapshot_updated_at": rows[-1].get("updated_at")},
        "snapshot_updated_at": rows[-1].get("updated_at"), "task": safe_task,
        "authority": "recorded taskboard evidence, not current completion proof or original user intent",
    }


def trace_finding(identity: str, source: int | None = None, root: str | Path | None = None) -> dict[str, Any]:
    """Inspect a finding; open a private source only with an explicit index."""
    if not re.fullmatch(r"[0-9a-f]{24}", identity) or (source is not None and (type(source) is not int or source < 0)):
        raise ValueError("Trace requires a finding ID and nonnegative source index")
    base = _root(root)
    _, records = _locations(base)
    record = json.loads((records / f"{identity}.json").read_text(encoding="utf-8"))
    references = record.get("source_refs", [])
    if source is None:
        return _finding_result(record, base, _current_paths(base))
    if source >= len(references):
        raise ValueError("Source index does not exist in this finding")
    return {"finding_id": identity, "source_index": source, "cited_reference": references[source],
            **_read_reference(references[source])}


def record_finding(record: dict[str, Any], root: str | Path | None = None) -> dict[str, Any]:
    """Retain sourced analysis, never a completion receipt or inferred policy."""
    if not isinstance(record, dict):
        raise ValueError("A finding must be a JSON object")
    for key in ("aliases", "evidence_paths", "commits", "source_refs"):
        if key in record and not isinstance(record[key], list):
            raise ValueError(f"{key} must be a list")
    base = _root(root)
    database, directory = _locations(base)
    with file_byte_lock(directory / ".records.lock", _RECORD_LOCK):
        identity = str(record.get("id") or "")
        prior = {}
        if identity:
            if not re.fullmatch(r"[0-9a-f]{24}", identity):
                raise ValueError("Use the existing 24-character finding ID when updating a renamed owner")
            destination = directory / f"{identity}.json"
            if not destination.is_file():
                raise ValueError("An explicit finding ID must identify an existing record")
            prior = json.loads(destination.read_text(encoding="utf-8"))
        owner = str(record.get("owner", prior.get("owner", "")) or "").strip()
        symbol = str(record.get("symbol", prior.get("symbol", "")) or "").strip()
        prior_by_owner = [
            row for path in directory.glob("*.json")
            if (row := json.loads(path.read_text(encoding="utf-8"))).get("owner") == owner
            and row.get("symbol", "") == symbol
        ]
        existing_id = prior_by_owner[0]["id"] if prior_by_owner else ""
        identity = identity or existing_id
        if not identity:
            seed = f"{owner}\0{symbol}"
            identity = hashlib.sha256(seed.encode()).hexdigest()[:24]
            suffix = 0
            # A finding keeps its ID when its owner moves. Reusing the former
            # owner is a new analysis, never permission to overwrite that ID.
            while (directory / f"{identity}.json").exists():
                suffix += 1
                identity = hashlib.sha256(f"{seed}\0{suffix}".encode()).hexdigest()[:24]
        if existing_id and existing_id != identity:
            raise ValueError("This owner/symbol already has a finding; update its existing ID")
        destination = directory / f"{identity}.json"
        prior = json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else {}
        description = str(record.get("description", prior.get("description", "")) or "").strip()
        decision = str(record.get("decision", prior.get("decision", "")) or "").strip()
        refresh = "evidence_paths" in record or not prior
        if refresh:
            evidence = sorted({owner, *(str(p) for p in record.get("evidence_paths", []))})
            if not owner or not description or not decision or len(evidence) < 2:
                raise ValueError("A finding needs owner, description, decision and at least two evidence_paths")
            fingerprints = _fingerprints(base, evidence)
            checked_head = _git(base, "rev-parse", "HEAD").decode().strip()
            inventory = sorted(_current_paths(base))
        else:
            # Attaching a source or new wording must not bless stale analysis.
            values = {"owner": owner, "symbol": symbol, "description": description, "decision": decision}
            if any(value != prior.get(key, "") for key, value in values.items()):
                raise ValueError("Changing an analysis requires explicit evidence_paths after checking the source")
            fingerprints = prior["source_fingerprints"]
            checked_head = prior["checked_head"]
            inventory = prior["source_inventory"]
        references = prior.get("source_refs", [])
        if "source_refs" in record:
            if len(record["source_refs"]) > 12:
                raise ValueError("source_refs must contain at most twelve explicit references")
            references = []
            for reference in record["source_refs"]:
                resolved = _read_reference(reference, capture=True)
                if resolved["state"] != "available":
                    raise ValueError(f"Cannot attach unavailable source: {resolved['state']}")
                references.append(resolved["reference"])
        commits = list(dict.fromkeys(str(oid) for oid in record.get("commits", prior.get("commits", []))))
        for oid in commits:
            if not re.fullmatch(r"[0-9a-f]{40,64}", oid):
                raise ValueError("Commit evidence requires a full object ID")
            _git(base, "cat-file", "-e", f"{oid}^{{commit}}")
        intent = str(record.get("intent", prior.get("intent", "")) or "")
        intent_source = str(record.get("intent_source", prior.get("intent_source", "")) or "")
        prior_source = re.fullmatch(r"source_refs\[(\d+)\]", prior.get("intent_source", ""))
        if intent and prior.get("intent") and prior_source and "source_refs" in record:
            index = int(prior_source[1])
            old_references = prior.get("source_refs", [])
            old_source = old_references[index] if index < len(old_references) else {}
            new_source = references[index] if index < len(references) else {}
            # A saved row can move after compaction; its pinned identity cannot.
            keys = ("kind", "session_name", "session_id", "content_sha256")
            if any(old_source.get(key) != new_source.get(key) for key in keys):
                if not {"intent", "intent_source"} <= record.keys():
                    raise ValueError("Changing an intent source requires an explicit intent and intent_source update")
        if intent and not intent_source:
            raise ValueError("Recorded intent requires an explicit intent_source; otherwise leave it unknown")
        if intent_source.startswith("source_refs["):
            match = re.fullmatch(r"source_refs\[(\d+)\]", intent_source)
            if not match or int(match[1]) >= len(references) or references[int(match[1])]["kind"] != "user_message":
                raise ValueError("A transcript intent_source must reference an available original user message")
        aliases = list(dict.fromkeys([*prior.get("aliases", []), *(str(a) for a in record.get("aliases", []))]))
        result = {
            "id": identity, "owner": owner, "symbol": symbol, "description": description,
            "decision": decision, "aliases": aliases, "intent": intent,
            "intent_source": intent_source, "commits": commits,
            "source_refs": references,
            "open_question": str(record.get("open_question", prior.get("open_question", "")) or ""),
            "source_fingerprints": fingerprints, "checked_head": checked_head,
            "source_inventory": inventory, "recorded_at": time.time(),
            "kind": "recorded_analysis",
        }
        if redact_secret_values(json.dumps(result)) != json.dumps(result):
            raise ValueError("Finding contains secret-shaped text; retain only safe source references")
        atomic_write_json(destination, result)
        with closing(_connect(database, write=True)) as connection, connection:
            _replace_search(connection, identity, "finding", _finding_text(result))
    return {"id": identity, "owner": owner, "kind": "recorded_analysis", "evidence_files": len(fingerprints)}


def _finding_result(record: dict[str, Any], root: Path, current_paths: set[str]) -> dict[str, Any]:
    changed = []
    for path, expected in record["source_fingerprints"].items():
        candidate = (root / path).resolve()
        if (
            path not in current_paths
            or root not in candidate.parents
            or candidate.relative_to(root).as_posix() not in current_paths
            or not candidate.is_file()
            or file_sha256(candidate) != expected
        ):
            changed.append(path)
    inventory_changed = set(record.get("source_inventory", [])) != current_paths
    return {
        key: record.get(key) for key in (
            "id", "owner", "symbol", "description", "decision", "aliases", "intent", "intent_source", "commits", "checked_head", "kind", "source_refs", "open_question",
        )
    } | {
        "evidence_paths": list(record["source_fingerprints"]), "changed_evidence": changed,
        "inventory_changed": inventory_changed,
        "evidence_state": "needs_recheck" if changed or inventory_changed else "sources_unchanged",
        "authority": "analyst evidence; current source and user intent remain authoritative",
        "source_reference_state": (
            "not_opened; trace explicitly before relying on recorded intent"
            if record.get("source_refs") else "none_linked; no original source to trace"
        ),
    }


def search_history(query: str, root: str | Path | None = None, *, paths: list[str] | None = None, limit: int = 5, revision: str | None = None, include_findings: bool = True) -> dict[str, Any]:
    """Return bounded sourced history, including explicit freshness and coverage."""
    base = _root(root)
    database, records = _locations(base)
    status = history_status(base)
    result: dict[str, Any] = {"status": status, "findings": [], "commits": []}
    ancestry = None
    if revision is not None:
        pinned = _git(base, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}").decode().strip()
        ancestry = _git(base, "rev-list", pinned).decode().splitlines()
        status.update(reviewed_oid=pinned, revision_discovered_commits=len(ancestry), revision_indexed_commits=0)
        # A current analyst record cannot establish intent/evidence at an older
        # revision. Its source hashes deliberately describe the current tree.
        include_findings = include_findings and pinned == status["git_head"]
    if not database.is_file():
        return result
    limit = max(1, min(20, int(limit)))
    terms = list(dict.fromkeys(re.findall(r"[\w]{2,}", query.lower())))[:32]
    expression = " OR ".join('"' + term + '"' for term in terms)
    with closing(_connect(database)) as connection:
        # TEMP state scopes queries without rewriting the shared HEAD index.
        # Filter before ranking/limits so later commits cannot crowd out ancestors.
        ancestry_filter = ""
        if ancestry is not None:
            connection.execute("CREATE TEMP TABLE query_ancestry (oid TEXT PRIMARY KEY)")
            connection.executemany("INSERT INTO query_ancestry VALUES (?)", ((oid,) for oid in ancestry))
            ancestry_filter = " AND c.oid IN (SELECT oid FROM query_ancestry)"
            status["revision_indexed_commits"] = connection.execute(
                "SELECT count(*) FROM commits c WHERE c.reachable=1" + ancestry_filter
            ).fetchone()[0]
        kinds = ("finding", "commit") if include_findings else ("commit",)
        rows = [row for kind in kinds for row in connection.execute(
            "SELECT key, kind, snippet(search,2,'[',']',' … ',24) AS excerpt FROM search "
            "WHERE search MATCH ? AND kind=? "
            + ("AND key IN (SELECT oid FROM query_ancestry) " if ancestry is not None and kind == "commit" else "")
            + "ORDER BY bm25(search) LIMIT ?", (expression, kind, limit * 4),
        )] if expression else []
        current_paths = _current_paths(base)
        requested_paths = list(dict.fromkeys(path for path in (paths or []) if path))
        graph_paths = requested_paths[:8]
        status.update(owner_paths_considered=len(graph_paths), owner_paths_requested=len(requested_paths))
        owner_finding_ids = []
        for owner in graph_paths if include_findings else []:
            # _finding_text starts with the exact owner line. Reuse its FTS
            # projection, not a second owner index or a scan of every record.
            owner_expression = '^ "' + owner.replace('"', '""') + '"'
            owner_finding_ids.extend(row[0] for row in connection.execute(
                "SELECT key FROM search WHERE search MATCH ? AND kind='finding' "
                "AND substr(text, 1, instr(text, char(10)) - 1)=? ORDER BY key LIMIT ?",
                (owner_expression, owner, limit),
            ))
        lexical_finding_ids = [row["key"] for row in rows if row["kind"] == "finding"]
        finding_ids = []
        for position in range(max(len(owner_finding_ids), len(lexical_finding_ids))):
            for candidates in (owner_finding_ids, lexical_finding_ids):
                if position < len(candidates):
                    finding_ids.append(candidates[position])
        for identity in dict.fromkeys(finding_ids):
            path = records / f"{identity}.json"
            if path.is_file():
                result["findings"].append(_finding_result(json.loads(path.read_text(encoding="utf-8")), base, current_paths))
            if len(result["findings"]) >= limit:
                break
        text_ids = [row["key"] for row in rows if row["kind"] == "commit"]
        related_ids = []
        owner_paths = list(dict.fromkeys([*graph_paths, *(row["owner"] for row in result["findings"])]))[:8]
        # Existing graph hits connect a new description to history even when the
        # commit message used different vocabulary. The graph remains the owner.
        for path in owner_paths:
            related_ids.extend(row[0] for row in connection.execute(
                "SELECT c.oid FROM commits c JOIN changes p ON p.oid=c.oid "
                "WHERE c.reachable=1 AND (p.path=? OR p.old_path=?)" + ancestry_filter
                + " ORDER BY c.recorded_at DESC LIMIT ?", (path, path, limit),
            ))
        # Keep both original lexical context and recent owner changes in a
        # bounded result; a popular phrase must not crowd out the current owner.
        commit_ids = []
        for position in range(max(len(text_ids), len(related_ids))):
            for candidates in (text_ids, related_ids):
                if position < len(candidates):
                    commit_ids.append(candidates[position])
        excerpts = {row["key"]: row["excerpt"] for row in rows if row["kind"] == "commit"}
        for oid in dict.fromkeys(commit_ids):
            commit = connection.execute("SELECT c.* FROM commits c WHERE c.oid=? AND c.reachable=1" + ancestry_filter, (oid,)).fetchone()
            if commit is None:
                continue
            changes = [dict(row) for row in connection.execute("SELECT path, old_path, kind FROM changes WHERE oid=?", (oid,))]
            admitted = _allowed_paths(base, [p for change in changes for p in (change["path"], change["old_path"]) if p])
            if any(change["path"] not in admitted or (change["old_path"] and change["old_path"] not in admitted) for change in changes):
                status.update(stale=True, source_admission_changed=True)
                continue
            current = {row["path"] for row in changes if row["path"] in current_paths}
            for change in changes:
                if change["path"] in current_paths:
                    continue
                # Follow recorded rename edges only; repeated or conflicting
                # path identities remain candidates, never certified ownership.
                frontier, visited = [change["path"]], set()
                while frontier and len(visited) < 100:
                    old = frontier.pop()
                    if old in visited:
                        continue
                    visited.add(old)
                    for renamed in connection.execute(
                        "SELECT p.path, c.oid FROM changes p JOIN commits c ON c.oid=p.oid "
                        "WHERE p.old_path=? AND c.reachable=1" + ancestry_filter, (old,),
                    ):
                        if _git(base, "merge-base", oid, renamed[1]).decode().strip() != oid:
                            continue
                        if renamed[0] in current_paths:
                            current.add(renamed[0])
                        else:
                            frontier.append(renamed[0])
            def path_rank(path: str) -> tuple[int, int, str]:
                matches = sum(term in path.lower() for term in terms)
                return (0 if path in owner_paths else 1, -matches, path)

            result["commits"].append({
                "oid": oid, "message": commit["message"][:1200], "changes": changes[:30],
                "changed_paths_total": len(changes), "current_path_candidates": sorted(current, key=path_rank)[:12],
                "excerpt": excerpts.get(oid, ""), "patch_truncated": bool(commit["truncated"]),
            })
            if len(result["commits"]) >= limit:
                break
    return result


def format_history(result: dict[str, Any], *, max_chars: int = 2200, include_excerpts: bool = False) -> str:
    status = result["status"]
    if not status.get("available"):
        return ""
    lines = [
        "Project history — sourced orientation, not current verification:",
        f"Coverage: {status['indexed_commits']}/{status['discovered_commits']} local commits; "
        f"{'stale' if status['stale'] else 'current'} HEAD inventory; "
        f"{status.get('truncated_patches', 0)} clipped patches; GitHub discussion coverage: none.",
    ]
    if status.get("reviewed_oid"):
        lines.append(f"Reviewed ancestry {status['reviewed_oid']}: {status['revision_indexed_commits']}/{status['revision_discovered_commits']} indexed commits; shallow: {bool(status.get('shallow'))}. Bounded relevance sample, not an exhaustive historical audit.")
    for finding in result["findings"]:
        lines.append(f"Finding {finding['id']} [{finding['evidence_state']}]: {finding['description']} — owner {finding['owner']} {finding['symbol']}")
        if finding.get("changed_evidence") or finding.get("inventory_changed"):
            lines.append(f"  Changed evidence: {', '.join(finding.get('changed_evidence', [])) or 'none'}; source inventory changed: {bool(finding.get('inventory_changed'))}.")
        if finding.get("source_refs"):
            lines.append(f"  {len(finding['source_refs'])} private source references (zero-based, not opened); inspect with python -m core.graph.history trace {finding['id']} --source 0.")
        else:
            lines.append("  No linked user/taskboard sources; do not trace. Inspect the finding with project_history action=inspect.")
        if finding.get("open_question"):
            lines.append(f"  Unresolved intent: {finding['open_question']} Ask the user if source inspection cannot resolve it.")
        lines.append(f"  Decision: {finding['decision']}; evidence: {', '.join(finding['evidence_paths'])}; checked {finding['checked_head'][:12]}.")
    for commit in result["commits"]:
        paths = commit["current_path_candidates"] or [p["path"] for p in commit["changes"][:3]]
        lines.append(f"Commit {commit['oid'][:12]}: {commit['message'].splitlines()[0] if commit['message'] else '(no message)'} — {', '.join(paths)}")
        if include_excerpts:
            lines.append(f"  Recorded rationale (not verified intent): {commit['message'][:1200]}")
            if commit.get("excerpt"):
                lines.append(f"  Historical excerpt: {commit['excerpt']}")
    if len(lines) == 2 and not status.get("source_admission_changed"):
        return ""
    return "\n".join(lines)[:max_chars]


def history_context(query: str, root: str | Path | None = None, *, paths: list[str] | None = None, max_chars: int = 2200) -> str:
    """Read-only optional orientation for existing graph/context consumers."""
    try:
        from .structural_graph import project_root, relevant_node_paths

        base = project_root(root)
        if not _locations(base)[0].is_file():
            return ""
        if paths is None:
            paths = relevant_node_paths(query, cwd=str(base))
        return format_history(search_history(query, base, paths=paths, limit=3), max_chars=max_chars)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        return f"Project history unavailable ({type(exc).__name__}); use current source evidence and inspect history status."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("build")
    commands.add_parser("status")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    record = commands.add_parser("record")
    record.add_argument("--input", type=Path, required=True)
    trace = commands.add_parser("trace", help="Inspect a finding; --source opens one explicitly cited private source")
    trace.add_argument("id")
    trace.add_argument("--source", type=int, help="Zero-based source_refs index; omit to inspect the finding without opening sources")
    args = parser.parse_args(argv)
    try:
        if args.action == "build":
            result = build_history_index(args.root, progress=lambda counts: print(json.dumps(counts), flush=True))
        elif args.action == "status":
            result = history_status(args.root)
        elif args.action == "search":
            result = search_history(args.query, args.root, limit=args.limit)
        elif args.action == "trace":
            result = trace_finding(args.id, args.source, args.root)
        else:
            result = record_finding(json.loads(args.input.read_text(encoding="utf-8")), args.root)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"error": redact_secret_values(str(exc))}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
