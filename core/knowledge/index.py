"""Evidence-backed project knowledge manifest and bounded read-only queries.

This module joins existing project inventory, graph status, public Markdown, the
capability ledger, and test paths. It does not create a second graph, infer
behavior, or write project documentation.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from ..graph.structural_graph import graph_status, project_root
from ..graph.search import search as search_graph
from ..mapping.partition import project_file_inventory
from ..runtime.lock import file_byte_lock
from ..state.paths import project_cache_dir
from ..utils.atomic_write import atomic_write_text
from ..utils.markdown import headings, prose_lines
from ..utils.text_utils import DEFAULT_CONTEXT_STOPWORDS

_MANIFEST = "manifest.json"
_MANIFEST_THREAD_LOCK = threading.RLock()
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{1,80}", re.I)


def manifest_path(root: str | Path | None = None) -> Path:
    return project_cache_dir("knowledge", project_root(root)) / _MANIFEST


def _markdown_records(root: Path, files: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for rel in files:
        if not rel.lower().endswith(".md"):
            continue
        path = root / rel
        try:
            content = path.read_bytes()
        except OSError:
            continue
        records.append({
            "path": rel, "headings": headings(prose_lines(content.decode("utf-8", errors="replace")))[:120], "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    return records


def _capability_records(root: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    path = root / "CAPABILITIES.md"
    if not path.is_file():
        return [], []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    mode = ""
    capabilities: list[dict[str, str]] = []
    commands: list[dict[str, str]] = []
    for line_no, line in enumerate(lines, 1):
        if "mo-capabilities:ledger:start" in line:
            mode = "capabilities"; continue
        if "mo-capabilities:ledger:end" in line:
            mode = ""; continue
        if "mo-capabilities:commands:start" in line:
            mode = "commands"; continue
        if "mo-capabilities:commands:end" in line:
            mode = ""; continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        if mode == "capabilities" and len(cells) == 8 and cells[0].startswith("CAP-"):
            capabilities.append({"id": cells[0], "name": cells[1], "owner": cells[2], "line": str(line_no)})
        elif mode == "commands" and len(cells) == 3 and cells[0].startswith("/"):
            commands.append({"name": cells[0], "class": cells[1], "capability": cells[2], "line": str(line_no)})
    return capabilities, commands


def build_manifest(root: str | Path | None = None) -> dict[str, Any]:
    base = project_root(root)
    files, _ = project_file_inventory(base)
    graph = graph_status(base)
    capabilities, commands = _capability_records(base)
    markdown = _markdown_records(base, files)
    tests = [rel for rel in files if rel.startswith(("tests/", "test/")) or rel.rsplit("/", 1)[-1].startswith("test_")]
    authorities = [
        rel for rel in ("README.md", "FAQ.md", "MAP.md", "CAPABILITIES.md", "AGENTS.md", "CHANGELOG.md")
        if (base / rel).is_file()
    ]
    payload = {
        "schema": 1,
        "project": {"root_name": base.name, "root": str(base)},
        "coverage": {"indexed_files": len(files), "markdown_files": len(markdown), "test_files": len(tests)},
        "authorities": authorities,
        "documents": markdown,
        "capabilities": capabilities,
        "commands": commands,
        "tests": tests[:5000],
        "graph": {
            key: graph.get(key)
            for key in ("available", "source_kind", "nodes", "edges", "groups", "stale", "stale_reasons", "git_head", "built_at", "trust", "quality", "confidence_breakdown", "provenance_breakdown")
            if key in graph
        },
    }
    payload["fingerprint"] = hashlib.sha256(
        json.dumps({
            "files": files, "documents": markdown, "capabilities": capabilities,
            "commands": commands, "authorities": authorities,
        }, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


def write_manifest(manifest: dict[str, Any], root: str | Path | None = None) -> Path:
    path = manifest_path(root)
    atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_manifest(root: str | Path | None = None) -> dict[str, Any] | None:
    path = manifest_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("schema") == 1 else None


def _refresh_manifest(root: str | Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return one current manifest while serializing every normal writer."""
    path = manifest_path(root)
    with file_byte_lock(path.with_name(".manifest.lock"), _MANIFEST_THREAD_LOCK):
        current = build_manifest(root)
        saved = load_manifest(root)
        changed = saved is None or saved.get("fingerprint") != current.get("fingerprint")
        if changed:
            write_manifest(current, root)
        return current, {
            "ok": True,
            "status": "built" if saved is None else "refreshed" if changed else "fresh",
            "path": str(path),
            "coverage": current.get("coverage", {}),
        }


def maintain_manifest(root: str | Path | None = None) -> dict[str, Any]:
    """Automatically maintain the private manifest under one project lock."""
    _, outcome = _refresh_manifest(root)
    return outcome


def knowledge_status(root: str | Path | None = None) -> dict[str, Any]:
    manifest = load_manifest(root)
    if manifest is None:
        return {"available": False, "path": str(manifest_path(root)), "reason": "manifest_missing"}
    return _current_status(manifest, root)


def _current_status(manifest: dict[str, Any], root: str | Path | None) -> dict[str, Any]:
    # Readers detect drift but never become another writer or bypass read-only lanes.
    current = build_manifest(root)
    current_graph = current["graph"]
    return {
        "available": True,
        "path": str(manifest_path(root)),
        "fingerprint": manifest.get("fingerprint", ""),
        "manifest_current": manifest.get("fingerprint") == current["fingerprint"],
        "coverage": manifest.get("coverage", {}),
        "graph": current_graph,
        "graph_current": bool(current_graph.get("available") and not current_graph.get("stale")),
    }


def _source_refs(row: dict[str, Any]) -> set[tuple[str, str]]:
    """Comparable references for the existing graph and manifest result shapes."""
    kind = row.get("kind")
    if kind == "document":
        path = str(row.get("path", ""))
        return {(path, f"L{h['line']}-L{h['line']}") for h in row.get("headings", [])} or {(path, "")}
    if kind in {"capability", "command"}:
        return {("CAPABILITIES.md", f"L{row.get('line')}-L{row.get('line')}")}
    location = str(row.get("source_location") or "")
    if re.fullmatch(r"L\d+", location):
        location += "-" + location
    return {(str(row.get("source_file") or "").replace("\\", "/"), location)}


def _rank_manifest_rows(
    manifest: dict[str, Any],
    terms: set[str],
    existing_refs: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    # Match words, not substrings, and rank a document by its best section.
    # Repeating a common word across a long FAQ must not beat a focused owner.
    terms = terms - DEFAULT_CONTEXT_STOPWORDS
    def score_terms(text: str) -> int:
        return len(terms.intersection(token.lower() for token in _TOKEN.findall(str(text))))

    rows: list[dict[str, Any]] = []
    for doc in manifest.get("documents", []):
        headings = [
            {**heading, "score": score_terms(heading.get("title", ""))}
            for heading in doc.get("headings", [])
        ]
        matches = sorted((h for h in headings if h["score"]), key=lambda h: (-h["score"], h["line"]))
        # An explicitly named document/path is stronger direction than a common
        # query word appearing in one of many headings. This keeps requests for
        # ``interface README`` on that source instead of equally scored broad
        # capabilities or unrelated surface guides.
        path_score = score_terms(doc.get("path", ""))
        score = 2 * path_score + max((h["score"] for h in matches), default=0)
        if score:
            path = doc.get("path", "")
            unseen = [h for h in matches if (path, f"L{h['line']}-L{h['line']}") not in existing_refs]
            if (matches and not unseen) or (not matches and (path, "") in existing_refs):
                continue
            rows.append({
                "kind": "document",
                "path": path,
                "score": score,
                "path_score": path_score,
                "headings": unseen[:4],
            })
    for item in (*manifest.get("capabilities", []), *manifest.get("commands", [])):
        text = " ".join(str(value) for value in item.values()).lower()
        score = score_terms(text)
        if score:
            row = {"kind": "capability" if "id" in item else "command", "score": score, **item}
            if not _source_refs(row) <= existing_refs:
                rows.append(row)
    return rows


def _row_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    identity = item.get("path", item.get("source_file", item.get("id", item.get("name", ""))))
    return -float(item.get("score", 0)), str(identity)


def query_manifest(
    query: str, root: str | Path | None = None, *, limit: int = 12,
    existing_graph_hits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Query knowledge, or supplement graph hits already displayed by a caller.

    Supplying displayed hits (including an empty list) skips graph retrieval.
    Current graph references are omitted; stale hits cannot hide current docs.
    Standalone callers keep the combined query.
    """
    manifest = load_manifest(root)
    if manifest is None:
        return {"available": False, "reason": "manifest_missing", "results": []}
    status = _current_status(manifest, root)
    if not status["manifest_current"]:
        return {**status, "query": query, "results": []}
    limit = max(1, min(int(limit), 50))
    terms = {item.lower() for item in _TOKEN.findall(query or "")}
    existing_refs = {
        ref for hit in existing_graph_hits or [] for ref in _source_refs(hit)
    } if status["graph_current"] else set()
    rows = _rank_manifest_rows(manifest, terms, existing_refs)
    if existing_graph_hits is None and status["graph_current"] and terms:
        try:
            seen = {ref for row in rows for ref in _source_refs(row)}
            for item in search_graph(query, cwd=project_root(root), top_n=min(limit, 20)):
                refs = _source_refs(item)
                if refs <= seen:
                    continue
                seen.update(refs)
                rows.append({
                    "kind": "graph", "score": float(item.get("score", 0)),
                    "label": item.get("label", ""), "source_file": item.get("source_file", ""),
                    "source_location": item.get("source_location", ""),
                    "snippet": item.get("doc_snippet", ""),
                })
        except Exception:
            status["graph_query_error"] = "graph_search_failed"
    rows.sort(key=_row_sort_key)
    return {**status, "query": query, "results": rows[:limit]}


def render_knowledge(result: dict[str, Any]) -> str:
    if not result.get("available"):
        return "Knowledge index unavailable: " + str(result.get("reason") or "unknown") + ". Automatic maintenance will retry."
    selected_verified = bool(result.get("selected_sources_verified"))
    lines = [
        "Project knowledge (selected sources verified; orientation, not proof):"
        if selected_verified else
        "Project knowledge (source-linked orientation, not proof):"
    ]
    if result.get("query"):
        lines.append("Query: " + str(result["query"]))
    if selected_verified and result.get("omitted_changed_sources"):
        lines.append("Changed saved sources were omitted; verify their current contents before relying on them.")
    graph = result.get("graph") or {}
    if not selected_verified:
        state = "unavailable" if not graph.get("available") else "stale" if graph.get("stale") else "fresh"
        lines.append(f"Graph: {state}; {graph.get('nodes', 0)} nodes, {graph.get('edges', 0)} edges.")
    if result.get("graph_query_error"):
        lines.append("Graph search unavailable; any source references below are partial.")
    if result.get("manifest_current") is False:
        lines.append("Knowledge sources changed. Automatic maintenance is refreshing them; retry shortly.")
        return "\n".join(lines)
    rows = result.get("results") or []
    if not rows and not result.get("graph_query_error") and not result.get("omitted_changed_sources"):
        lines.append("No indexed knowledge matches.")
    for row in rows:
        if row.get("kind") == "document":
            headings = ", ".join(f"{h.get('title')} (L{h.get('line')})" for h in row.get("headings", [])[:4])
            lines.append(f"- document: {row.get('path')}" + (f" — {headings}" if headings else ""))
        elif row.get("kind") == "capability":
            lines.append(f"- {row.get('id')}: {row.get('name')} — owner {row.get('owner')} (CAPABILITIES.md:{row.get('line')})")
        elif row.get("kind") == "graph":
            lines.append(f"- graph: {row.get('label')} — {row.get('source_file')} {row.get('source_location') or ''}".rstrip())
        else:
            lines.append(f"- command: {row.get('name')} → {row.get('capability')} (CAPABILITIES.md:{row.get('line')})")
    lines.append("Verify selected source and tests before treating an entry as behavior proof.")
    return "\n".join(lines)


def _verified_saved_rows(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    root: str | Path | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Keep only rows whose exact backing document still matches the manifest."""
    base = project_root(root).resolve(strict=False)
    documents = {
        str(document.get("path") or ""): document
        for document in manifest.get("documents", [])
        if isinstance(document, dict)
    }
    freshness: dict[str, bool] = {}

    def current(path_text: str) -> bool:
        if path_text in freshness:
            return freshness[path_text]
        record = documents.get(path_text)
        if not record:
            freshness[path_text] = False
            return False
        try:
            path = (base / path_text).resolve(strict=False)
            path.relative_to(base)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            freshness[path_text] = digest == str(record.get("sha256") or "")
        except (OSError, ValueError):
            freshness[path_text] = False
        return freshness[path_text]

    verified: list[dict[str, Any]] = []
    for row in rows:
        source = str(row.get("path") or "") if row.get("kind") == "document" else "CAPABILITIES.md"
        if current(source):
            verified.append(row)
        if len(verified) >= limit:
            break
    return verified


def build_project_knowledge_context(
    query: str,
    root: str | Path | None = None,
    *,
    max_chars: int = 1200,
    limit: int = 6,
) -> str:
    """Return fast bounded context with every selected source verified in place."""
    manifest = load_manifest(root)
    if manifest is None:
        return ""
    terms = {item.lower() for item in _TOKEN.findall(query or "")}
    if not terms:
        return ""
    candidates = sorted(
        _rank_manifest_rows(manifest, terms, set()),
        key=lambda item: (
            -float(item.get("score", 0)),
            -float(item.get("path_score", 0)),
            _row_sort_key(item)[1],
        ),
    )
    if not candidates:
        return ""
    verified = _verified_saved_rows(manifest, candidates, root, limit=max(1, min(int(limit), 50)))
    result = {
        "available": True,
        # The provider already has the user's query. Repeating a long request in
        # this small context block can consume the entire character budget before
        # the first verified source row, leaving a heading that falsely looks
        # like delivered project knowledge.
        "query": "",
        "results": verified,
        "selected_sources_verified": True,
        "omitted_changed_sources": len(verified) < min(len(candidates), max(1, min(int(limit), 50))),
    }
    text = render_knowledge(result)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    lines: list[str] = []
    used = 0
    for line in text.splitlines():
        added = len(line) + (1 if lines else 0)
        if used + added > max_chars:
            break
        lines.append(line)
        used += added
    bounded = "\n".join(lines)
    if verified and not any(line.startswith("- ") for line in lines):
        # A title with no selected source is not project knowledge and must not
        # produce a delivered-context flag/receipt.
        return ""
    if len(lines) <= 1:
        return ""
    return bounded
