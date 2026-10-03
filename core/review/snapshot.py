"""Temporary source authority for local PRT; never changes the operator checkout."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import stat
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from .diff_review import ReviewTarget


@dataclass(frozen=True)
class ReviewSnapshot:
    root: Path
    original_root: Path
    target: ReviewTarget
    digest: str
    overlay_files: int

    def metadata(self) -> dict:
        return {
            "isolated": True,
            "source_digest": self.digest,
            "test_overlay_files": self.overlay_files,
            "source_revision": self.target.reviewed_oid,
        }


def _git(root: Path, *args: str) -> bytes:
    from core.runtime.subprocess_flags import apply_windows_hidden_process_flags

    options = {"cwd": str(root), "capture_output": True, "check": True, "timeout": 120}
    apply_windows_hidden_process_flags(options)
    return subprocess.run(["git", *args], **options).stdout


def _inventory(root: Path, *, worktree: bool):
    from core.diagnostics.source_inventory import discover_source_paths, load_source_inventory
    from core.tooling.sandbox import secret_read_path_kind

    rows = discover_source_paths(root, include_test_overlay=True)
    if not worktree:
        rows = [(path, origin) for path, origin in rows if origin == "test-overlay"]
    if any(secret_read_path_kind(str(root / path)) for path, _ in rows):
        raise ValueError("review source includes a protected path")
    if any((root / path).is_symlink() for path, _ in rows):
        raise ValueError("review source contains symbolic links that cannot be copied as ordinary files")
    inventory = load_source_inventory(root, paths=rows)
    if inventory.problems:
        raise ValueError("review source could not be copied within its repository boundary")
    digest = hashlib.sha256()
    for document in inventory.documents:
        mode = stat.S_IMODE((root / document.relative_path).stat().st_mode)
        digest.update(f"{document.relative_path}\0{document.origin}\0{document.sha256}\0{mode}\0".encode())
    return inventory, digest.hexdigest()


@contextmanager
def review_snapshot(
    root: Path, diff_ref: str, *, allowed_roots: list[str] | None = None,
    correction_of: dict | None = None,
) -> Iterator[ReviewSnapshot]:
    """Copy a validated candidate, retrying capture changes before any review runs.

    Git objects are shared read-only with a local clone. Its index, worktree,
    graph cache and affected-test state are independent. Ignored maintainer QA
    is copied explicitly and identified separately from committed source.
    """
    from core.graph.structural_graph import invalidate_graph_runtime_caches
    from core.state.paths import project_cache_dir
    from core.tooling.sandbox import path_allowed
    from .diff_review import resolve_review_target

    root = root.resolve()
    repository = Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if repository != root or not path_allowed(str(repository), allowed_roots):
        raise ValueError("isolated review requires the repository within the allowed workspace")
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    if temporary_parent == root or root in temporary_parent.parents:
        raise ValueError("source snapshot temporary directory must be outside the checkout")
    for attempt in range(3):
        target = resolve_review_target(root, diff_ref, correction_of=correction_of)
        inventory, digest = _inventory(root, worktree=target.is_path_review)
        with tempfile.TemporaryDirectory(prefix="mo-prt-source-") as temporary:
            snapshot_root = Path(temporary).resolve() / "source"
            if root == snapshot_root or root in snapshot_root.parents:
                raise ValueError("source snapshot temporary directory must be outside the checkout")
            # No checkout, hooks, original index mutation, commits, or network.
            _git(root, "clone", "--local", "--shared", "--no-checkout", "--", str(root), str(snapshot_root))
            _git(snapshot_root, "config", "core.hooksPath", str(Path(temporary) / "no-hooks"))
            if target.reviewed_oid:
                _git(snapshot_root, "update-ref", "HEAD", target.reviewed_oid)
                _git(snapshot_root, "read-tree", target.reviewed_oid)
            if not target.is_path_review:
                _git(snapshot_root, "checkout-index", "--all", "--force")
            overlay_count = 0
            for document in inventory.documents:
                destination = snapshot_root / document.relative_path
                if not destination.resolve().is_relative_to(snapshot_root):
                    raise ValueError("review overlay destination escapes the snapshot boundary")
                # Committed tests take precedence over the current local overlay.
                if not target.is_path_review and destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(document.data)
                shutil.copymode(root / document.relative_path, destination)
                overlay_count += document.origin == "test-overlay"
            _, after_digest = _inventory(root, worktree=target.is_path_review)
            after_target = resolve_review_target(root, diff_ref, correction_of=correction_of)
            target_changed = (
                target.reviewed_oid != after_target.reviewed_oid
                or target.evidence_digest != after_target.evidence_digest
            )
            if digest != after_digest or target_changed:
                if attempt < 2:
                    continue
                raise ValueError("source changed during all three snapshot captures; rerun PRT when file writes settle")
            del inventory, _  # The review reads the copy, not retained repository byte buffers.
            cache = project_cache_dir("structural_graph", snapshot_root).resolve()
            cache_parent = project_cache_dir("structural_graph", root).resolve().parent
            try:
                source_digest = hashlib.sha256(f"{target.reviewed_oid}\0{digest}".encode()).hexdigest()
                yield ReviewSnapshot(snapshot_root, root, target, source_digest, overlay_count)
            finally:
                invalidate_graph_runtime_caches(snapshot_root)
                # Only this generated snapshot's cache belongs to this review.
                if cache.parent == cache_parent and cache != project_cache_dir("structural_graph", root).resolve() and cache.is_dir():
                    shutil.rmtree(cache)
            return
