"""High-level source intake: turn a classified SourceRef into fetched text files
plus a populated source manifest. Inert and capped; reuses fetch.py for all I/O.

Each path returns ``(files, manifest)`` where ``files`` is a ``{relpath: text}``
map and ``manifest`` is a ``source_manifest`` dict (approval-gated by construction).
"""
from __future__ import annotations

import json
from typing import Any

from . import fetch
from .manifest import new_source_manifest
from .sources import SourceRef


def _finalize(ref: SourceRef, files: dict[str, str], *, fetch_method: str,
              license: str = "", warnings: list[str] | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    manifest = new_source_manifest(
        source_kind=ref.kind, source_url=ref.url, source_ref=ref.ref,
        source_hash=fetch.source_hash(files), fetch_method=fetch_method, license=license,
    )
    catalog = fetch.build_catalog(files)
    manifest["files"] = [e["path"] for e in catalog["files"]]
    manifest["content_digests"] = {e["path"]: e["digest"] for e in catalog["files"]}
    manifest["byte_count"] = catalog["byte_count"]
    manifest["warnings"] = list(warnings or [])
    if ref.host:
        manifest["origin_allowlist"] = [ref.host]
    return files, manifest


def _intake_github(ref: SourceRef, *, opener, network_allowed) -> tuple[dict[str, str], dict[str, Any]]:
    files: dict[str, str] = {}
    warnings: list[str] = []
    license = ""
    branch = ref.ref or "main"
    # Metadata (description, license, default branch, stars) — advisory.
    status, meta_raw = fetch.http_get(
        f"https://api.github.com/repos/{ref.owner}/{ref.repo}",
        opener=opener, network_allowed=network_allowed,
    )
    if status == 200 and meta_raw:
        try:
            meta = json.loads(meta_raw)
            branch = ref.ref or str(meta.get("default_branch") or "main")
            license = str((meta.get("license") or {}).get("spdx_id") or "") if isinstance(meta.get("license"), dict) else ""
            desc = str(meta.get("description") or "")
            stars = meta.get("stargazers_count")
            files["_repo_metadata.md"] = (
                f"# {ref.owner}/{ref.repo}\n\n{desc}\n\n"
                f"- default_branch: {branch}\n- license: {license or 'unknown'}\n- stars: {stars}\n"
            )
            if not license:
                warnings.append("license unknown — surface before promotion")
        except Exception:
            warnings.append("repo metadata parse failed")
    else:
        warnings.append("repo metadata unavailable (no network approval or fetch failed)")

    # README + dependency/important files via raw content.
    base = ref.subpath.strip("/") + "/" if (ref.kind == "github_tree" and ref.subpath) else ""
    for rel in ("README.md", "readme.md", "pyproject.toml", "requirements.txt", "package.json", "AGENTS.md"):
        status, text = fetch.http_get(
            f"https://raw.githubusercontent.com/{ref.owner}/{ref.repo}/{branch}/{base}{rel}",
            opener=opener, network_allowed=network_allowed,
        )
        if status == 200 and text.strip():
            files[rel] = text
    if not any(k.lower().startswith("readme") for k in files):
        warnings.append("no README found")
    return _finalize(ref, files, fetch_method="github_api+raw", license=license, warnings=warnings)


def _parse_llms_txt(body: str, base_url: str) -> list[str]:
    """Extract same-origin doc URLs from an llms.txt body (markdown links + bare URLs)."""
    urls: list[str] = []
    for token in body.replace("(", " ").replace(")", " ").split():
        if token.startswith(("http://", "https://")) and fetch.same_origin(base_url, token):
            urls.append(token.rstrip(".,"))
    # de-dup, cap
    seen: dict[str, None] = {}
    for u in urls:
        seen.setdefault(u, None)
    return list(seen)[:fetch.MAX_FILES]


def _intake_docs(ref: SourceRef, *, opener, network_allowed) -> tuple[dict[str, str], dict[str, Any]]:
    files: dict[str, str] = {}
    warnings: list[str] = []
    # Resolve an llms.txt: explicit llms_txt source, or discover for a docs_site.
    llms_url = ref.url if ref.kind == "llms_txt" else ""
    if not llms_url and ref.kind == "docs_site":
        root = f"{ref.url.rstrip('/')}"
        for probe in (f"{root}/llms.txt", f"{root}/.well-known/llms.txt"):
            status, body = fetch.http_get(probe, opener=opener, network_allowed=network_allowed)
            if status == 200 and body.strip():
                llms_url = probe
                files["llms.txt"] = body
                break
    if llms_url:
        if "llms.txt" not in files:
            status, body = fetch.http_get(llms_url, opener=opener, network_allowed=network_allowed)
            if status == 200:
                files["llms.txt"] = body
        for page_url in _parse_llms_txt(files.get("llms.txt", ""), llms_url):
            if len(files) >= fetch.MAX_FILES:
                break
            status, text = fetch.http_get(page_url, opener=opener, network_allowed=network_allowed)
            if status == 200 and text.strip():
                files[page_url.rsplit("/", 1)[-1] or "page"] = text
    else:
        # Plain docs page, single fetch.
        status, body = fetch.http_get(ref.url, opener=opener, network_allowed=network_allowed)
        if status == 200 and body.strip():
            files["page.html"] = body
        else:
            warnings.append("docs page unavailable (no network approval or fetch failed)")
    if not files:
        warnings.append("no documentation fetched")
    return _finalize(ref, files, fetch_method="llms_txt" if llms_url else "docs_http", warnings=warnings)


def intake(ref: SourceRef, *, opener=None, network_allowed: bool = False) -> tuple[dict[str, str], dict[str, Any]]:
    """Fetch a classified source into (files, source_manifest). Inert + capped."""
    if ref.kind in ("github_repo", "github_tree"):
        return _intake_github(ref, opener=opener, network_allowed=network_allowed)
    if ref.kind in ("docs_site", "llms_txt"):
        return _intake_docs(ref, opener=opener, network_allowed=network_allowed)
    if ref.kind == "local_path":
        files = fetch.read_local_source(ref.local_path)
        return _finalize(ref, files, fetch_method="local_copy",
                         warnings=[] if files else ["no readable text files at local path"])
    return _finalize(ref, {}, fetch_method="none", warnings=[f"unsupported source kind: {ref.kind}"])
