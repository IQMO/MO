"""Classify an operator-provided source string into a typed reference.

Pure parsing — no network, no filesystem mutation. Determines which intake path
(github_repo / github_tree / docs_site / llms_txt / local_path) a source belongs
to and extracts the fields later stages need. Rejects path traversal and unsafe
local references up front.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_GITHUB_SHORTHAND = re.compile(r"^(?P<owner>[A-Za-z0-9][\w.-]*)/(?P<repo>[A-Za-z0-9][\w.-]*)$")
_LLMS_TXT = re.compile(r"/llms(?:-full)?\.txt$", re.IGNORECASE)


@dataclass
class SourceRef:
    kind: str          # github_repo | github_tree | docs_site | llms_txt | local_path | unknown
    raw: str
    url: str = ""
    host: str = ""
    owner: str = ""
    repo: str = ""
    ref: str = ""       # branch / tag / commit for github_tree
    subpath: str = ""   # path within repo for github_tree
    local_path: str = ""
    reason: str = ""    # why it was classified unknown / rejected

    @property
    def ok(self) -> bool:
        return self.kind != "unknown"


def _classify_github(parsed, raw: str) -> SourceRef | None:
    if parsed.netloc.lower() not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return SourceRef("unknown", raw, reason="github url missing owner/repo")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        return SourceRef(
            "github_tree", raw, url=raw, host="github.com",
            owner=owner, repo=repo, ref=parts[3], subpath="/".join(parts[4:]),
        )
    return SourceRef("github_repo", raw, url=raw, host="github.com", owner=owner, repo=repo)


def is_safe_local_path(raw: str) -> bool:
    """Reject null bytes and parent-escaping references before any fs access."""
    value = str(raw or "")
    if "\x00" in value:
        return False
    if ".." in Path(value).parts:
        return False
    return True


def classify_source(raw: str) -> SourceRef:
    value = str(raw or "").strip()
    if not value:
        return SourceRef("unknown", raw, reason="empty source")

    # GitHub shorthand: owner/repo (not a local path, no scheme).
    m = _GITHUB_SHORTHAND.match(value)
    if m and not os.path.exists(value):
        return SourceRef("github_repo", value, url=f"https://github.com/{value}",
                         host="github.com", owner=m.group("owner"), repo=m.group("repo"))

    # URLs.
    if value.lower().startswith(("http://", "https://")):
        parsed = urlparse(value)
        gh = _classify_github(parsed, value)
        if gh is not None:
            return gh
        if _LLMS_TXT.search(parsed.path):
            return SourceRef("llms_txt", value, url=value, host=parsed.netloc)
        if parsed.netloc:
            return SourceRef("docs_site", value, url=value, host=parsed.netloc)
        return SourceRef("unknown", value, reason="url has no host")

    # Local path.
    if not is_safe_local_path(value):
        return SourceRef("unknown", value, reason="unsafe local path (traversal or null byte)")
    expanded = os.path.expanduser(value)
    if os.path.exists(expanded):
        return SourceRef("local_path", value, local_path=os.path.normpath(expanded))

    return SourceRef("unknown", value, reason="unrecognized source (not github/url/existing path)")
