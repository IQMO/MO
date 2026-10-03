"""Optional default-deny state synchronization over Git + OpenSSH.

Only manifest entries marked ``replicate`` are ever staged.  The
repository uses a separate git-dir while the canonical ``~/.mo`` files remain the
work tree, so synchronization does not introduce a shadow profile/session store.
No database or conversation-continuity record is transferred by this coordinator.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

from ..runtime.subprocess_flags import apply_windows_hidden_process_flags
from ..profile import profile_transaction_lock
from ..utils.atomic_write import atomic_write_json, atomic_write_text
from ..utils.text_safety import contains_secret_value
from .device import device_identity
from .layout import sync_entries
from .paths import mo_home, resolve_state_path


SYNC_LOG = "sync/last-status.json"
VALIDATION_CACHE = "sync/validation-cache.json"
DEFAULT_BRANCH = "state"
MAX_TREE_FILES = 10_000
MAX_TREE_BYTES = 50_000_000

# Validation-only tombstones for paths removed from the state layout.  These
# are never sync candidates and are accepted only when this device already
# tracks the path while its canonical working-tree copy is absent.  That lets
# the synchronizer commit the deletion without reopening the retired path as
# valid profile state.
RETIRED_SYNC_TOMBSTONES = ("memory/profile/identity.md",)

# Older releases placed a runtime byte-lock inside the replicated skill tree.
# Accept only this exact legacy location from an existing remote, exclude it
# from profile content, and remove it from the next committed tree.  The marker
# can be removed after supported remotes have completed one sync migration.
LEGACY_SYNC_ARTIFACTS = ("skills/.mo-skills.lock",)


class StateSyncError(RuntimeError):
    pass


@runtime_checkable
class StateSyncBackend(Protocol):
    """Neutral coordinator boundary; product code does not depend on Git internals."""

    def status(self) -> dict[str, Any]: ...
    def baseline_established(self) -> bool: ...
    def initialize(self) -> None: ...
    def sync_once(self) -> "SyncResult": ...


@dataclass(frozen=True)
class SyncSettings:
    enabled: bool = False
    remote: str = ""
    branch: str = DEFAULT_BRANCH
    interval_seconds: float = 30.0
    max_file_bytes: int = 2_000_000
    git_dir: str = ""
    auto_sync: bool = False

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "SyncSettings":
        block = (config or {}).get("consistent_everywhere")
        block = block if isinstance(block, dict) else {}
        sync = block.get("sync") if isinstance(block.get("sync"), dict) else {}
        return cls(
            enabled=block.get("enabled") is True and sync.get("enabled", True) is True,
            remote=str(sync.get("git_remote") or "").strip(),
            branch=_safe_branch(sync.get("branch") or DEFAULT_BRANCH),
            interval_seconds=max(5.0, float(sync.get("interval_seconds", 30) or 30)),
            max_file_bytes=max(1_024, int(sync.get("max_file_bytes", 2_000_000) or 2_000_000)),
            git_dir=str(sync.get("git_dir") or "").strip(),
            auto_sync=sync.get("auto_sync") is True,
        )


@dataclass
class SyncResult:
    ok: bool
    state: str
    changed: bool = False
    pushed: bool = False
    pulled: bool = False
    paths: tuple[str, ...] = ()
    detail: str = ""
    created_at: float = field(default_factory=time.time)


class GitStateSync:
    """One canonical-worktree, exact-path Git synchronizer."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.settings = SyncSettings.from_config(self.config)
        self.home = mo_home(self.config)
        configured = Path(self.settings.git_dir).expanduser() if self.settings.git_dir else self.home / "sync" / "repo.git"
        self.git_dir = configured.resolve(strict=False)
        self.device: dict[str, str] | None = None
        self._changed_paths: tuple[str, ...] = ()
        self._pulled_paths: tuple[str, ...] = ()

    @property
    def tracked_paths(self) -> tuple[str, ...]:
        return tuple(entry.relpath for entry in sync_entries("replicate"))

    @property
    def tracked_entries(self) -> dict[str, str]:
        return {entry.relpath: entry.kind for entry in sync_entries("replicate")}

    @property
    def status_path(self) -> Path:
        return Path(resolve_state_path(SYNC_LOG, self.config))

    @property
    def validation_cache_path(self) -> Path:
        return Path(resolve_state_path(VALIDATION_CACHE, self.config))

    def status(self) -> dict[str, Any]:
        initialized = (self.git_dir / "HEAD").is_file()
        return {
            "enabled": self.settings.enabled,
            "configured": bool(self.settings.remote),
            "initialized": initialized,
            "baseline_established": self.baseline_established(),
            "branch": self.settings.branch,
            "tracked_paths": list(self.tracked_paths),
            "git_available": shutil.which("git") is not None,
            "ssh_available": shutil.which("ssh") is not None,
            "database_sync": False,
        }

    def baseline_established(self) -> bool:
        """Return whether the local state repository owns a real profile commit."""
        if not (self.git_dir / "HEAD").is_file() or shutil.which("git") is None:
            return False
        try:
            return self._has_head()
        except (OSError, StateSyncError):
            return False

    def initialize(self) -> None:
        if not self.settings.enabled:
            raise StateSyncError("consistent_everywhere is disabled")
        if not self.settings.remote:
            raise StateSyncError("consistent_everywhere.sync.git_remote is not configured")
        if not _valid_remote(self.settings.remote):
            raise StateSyncError("state sync remote must use Git-over-SSH or a local filesystem path")
        if shutil.which("git") is None or shutil.which("ssh") is None:
            raise StateSyncError("Git and OpenSSH are required")
        self.home.mkdir(parents=True, exist_ok=True)
        self.git_dir.parent.mkdir(parents=True, exist_ok=True)
        if not (self.git_dir / "HEAD").is_file():
            self._run_raw(["git", "init", "--bare", str(self.git_dir)])
            self._git("symbolic-ref", "HEAD", f"refs/heads/{self.settings.branch}")
        # This repository transfers exact private-state bytes across platforms.
        # Own the conversion policy instead of inheriting a user's global Git
        # autocrlf or attributes configuration.
        self._git("config", "core.bare", "false")
        self._git("config", "core.worktree", str(self.home))
        self._git("config", "core.autocrlf", "false")
        attributes = self.git_dir / "info" / "attributes"
        expected_attributes = "* -text -filter -ident -working-tree-encoding\n"
        try:
            current_attributes = attributes.read_text(encoding="utf-8")
        except OSError:
            current_attributes = ""
        if current_attributes != expected_attributes:
            atomic_write_text(attributes, expected_attributes)
        excludes = self.git_dir / "info" / "exclude"
        try:
            current_excludes = excludes.read_text(encoding="utf-8")
        except OSError:
            current_excludes = ""
        legacy_pattern = "/skills/.mo-skills.lock"
        if legacy_pattern not in current_excludes.splitlines():
            prefix = current_excludes.rstrip("\n")
            atomic_write_text(excludes, f"{prefix}\n{legacy_pattern}\n" if prefix else f"{legacy_pattern}\n")
        self._ensure_remote()

    def sync_once(self) -> SyncResult:
        self._changed_paths = ()
        self._pulled_paths = ()
        try:
            self.initialize()
            if not self._has_head() or self._tracked_worktree_dirty():
                self.validate_candidates()
            pulled = self._fast_forward_from_remote()
            if self._tracked_worktree_dirty():
                self.validate_candidates()
            changed = self._commit_local_changes()
            pushed = self._push_if_ahead()
            paths = tuple(sorted(set(self._changed_paths) | set(self._pulled_paths)))
            result = SyncResult(
                True,
                "clean",
                changed=changed,
                pulled=pulled,
                pushed=pushed,
                paths=paths,
            )
        except StateSyncError as exc:
            result = SyncResult(False, "blocked", detail=str(exc)[:500])
        except Exception as exc:
            result = SyncResult(False, "error", detail=f"{type(exc).__name__}: sync operation failed")
        self._write_status(result)
        return result

    def validate_candidates(self) -> None:
        """Fail closed on links, limits, secrets, or undeclared operational files."""
        root = self.home.resolve(strict=False)
        count = 0
        total_bytes = 0
        for rel in self.tracked_paths:
            source = root / rel
            if _path_is_link(source):
                raise StateSyncError(f"symlink is not syncable: {rel}")
            target = source.resolve(strict=False)
            if not _inside(target, root):
                raise StateSyncError(f"sync path escapes state home: {rel}")
            if not target.exists():
                continue
            for path in _candidate_files(target):
                count += 1
                if count > MAX_TREE_FILES:
                    raise StateSyncError("sync candidates contain too many files")
                if _path_is_link(path) or not _inside(path.resolve(strict=False), root):
                    raise StateSyncError(f"symlink is not syncable: {path.relative_to(root)}")
                if path.name == ".mo-skills.lock":
                    raise StateSyncError(
                        f"undeclared operational sync entry blocked: {path.relative_to(root)}"
                    )
                size = path.stat().st_size
                total_bytes += size
                if size > self.settings.max_file_bytes:
                    raise StateSyncError(f"sync candidate exceeds size limit: {path.relative_to(root)}")
                if total_bytes > MAX_TREE_BYTES:
                    raise StateSyncError("sync candidates exceed the total size limit")
                if _secret_shaped_name(path.name):
                    raise StateSyncError(f"secret-shaped sync path blocked: {path.relative_to(root)}")
                if _text_candidate(path):
                    try:
                        content = path.read_text(encoding="utf-8", errors="strict")
                    except (OSError, UnicodeError):
                        raise StateSyncError(f"unreadable text sync candidate: {path.relative_to(root)}") from None
                    if contains_secret_value(content):
                        raise StateSyncError(f"secret material detected in sync candidate: {path.relative_to(root)}")
                elif not _safe_binary_candidate(path):
                    raise StateSyncError(f"unsupported sync file type: {path.relative_to(root)}")

    def _fast_forward_from_remote(self) -> bool:
        fetch = self._git("fetch", "origin", self.settings.branch, check=False)
        if fetch.returncode != 0:
            # A new empty remote/branch is valid during first initialization.
            probe = self._git(
                "ls-remote", "--exit-code", "origin", f"refs/heads/{self.settings.branch}",
                check=False,
            )
            if not self._has_head() and probe.returncode == 2 and not probe.stdout.strip():
                return False
            raise StateSyncError("remote fetch failed")
        remote_ref = f"refs/remotes/origin/{self.settings.branch}"
        if not self._ref_exists(remote_ref):
            return False
        self._validate_tree(remote_ref, retired_tombstones=self._retired_deletions())
        if not self._has_head():
            with profile_transaction_lock(self.home / "memory" / "mo.db"):
                if self._has_local_candidates() and not self._remote_tree_matches_local(
                    remote_ref,
                    ignore_legacy_artifacts=True,
                ):
                    raise StateSyncError(
                        "remote state exists while this device has unsynchronized local sync paths"
                    )
                self._pulled_paths = self._tree_paths(remote_ref)
                self._git("reset", "--hard", remote_ref)
            return True
        if self._is_ancestor(remote_ref, "HEAD"):
            return False
        if not self._is_ancestor("HEAD", remote_ref):
            raise StateSyncError("state history diverged; automatic conflict resolution is disabled")
        with profile_transaction_lock(self.home / "memory" / "mo.db"):
            retired_deletions = self._retired_deletions()
            if self._tracked_worktree_dirty(include_retired=False):
                local_paths = self._tracked_worktree_paths(include_retired=False)
                remote_paths = self._diff_paths("HEAD", remote_ref)
                overlap = tuple(sorted(set(local_paths).intersection(remote_paths)))
                relevant = overlap or local_paths
                shown = ", ".join(relevant[:4]) or "curated profile paths"
                more = len(relevant) - min(len(relevant), 4)
                suffix = f" (+{more} more)" if more else ""
                if overlap:
                    raise StateSyncError(
                        f"remote state conflicts with local changes: {shown}{suffix}"
                    )
                raise StateSyncError(
                    f"remote state is newer while local sync paths are dirty: {shown}{suffix}"
                )
            self._pulled_paths = self._diff_paths("HEAD", remote_ref)
            self._git("merge", "--ff-only", remote_ref)
            # Fast-forward can momentarily restore a retired blob from the old
            # layout. Preserve the already-established local tombstone so the
            # following commit removes it from the new remote tip as well.
            for relpath in retired_deletions:
                target = self.home / relpath
                if target.is_file() or _path_is_link(target):
                    target.unlink()
                elif target.exists():
                    raise StateSyncError(f"retired sync path is not a file: {relpath}")
        return True

    def _commit_local_changes(self) -> bool:
        with profile_transaction_lock(self.home / "memory" / "mo.db"):
            return self._commit_local_changes_unlocked()

    def _commit_local_changes_unlocked(self) -> bool:
        # Compare the exact manifest, not `git status`: user/home ignore rules
        # may hide an allowlisted new profile or skill file.  The comparison is
        # also the cheap clean-tree gate that avoids rescanning unchanged data.
        if self._has_head() and self._remote_tree_matches_local("HEAD"):
            return False
        # Revalidate inside the same profile transaction used for staging so a
        # terminal writer cannot change prose between the scan and Git index.
        self.validate_candidates()
        stageable = tuple(
            rel for rel in self.tracked_paths
            if (self.home / rel).exists() or self._path_is_tracked(rel)
        ) + self._retired_deletions()
        if not stageable:
            return False
        # The manifest and validation above are the authority. A user-level or
        # home-level ignore rule must not silently omit an explicitly syncable
        # profile/skill path from the exact state tree. The one exclusion is a
        # retired runtime lock that older versions placed under ``skills``.
        pathspecs = stageable + tuple(f":(exclude){path}" for path in LEGACY_SYNC_ARTIFACTS)
        self._git("add", "-f", "-A", "--", *pathspecs)
        for relpath in LEGACY_SYNC_ARTIFACTS:
            if self._path_is_tracked(relpath):
                self._git("rm", "--cached", "--ignore-unmatch", "--", relpath)
        staged = self._git("diff", "--cached", "--quiet", "--", *stageable, check=False)
        if staged.returncode == 0:
            return False
        if staged.returncode != 1:
            raise StateSyncError("unable to inspect staged state changes")
        self._changed_paths = tuple(
            path for path in self._git(
                "diff", "--cached", "--name-only", "-z", "--", *stageable,
            ).stdout.split("\0") if path
        )
        tree = self._git("write-tree").stdout.strip()
        if not tree:
            raise StateSyncError("unable to validate staged state tree")
        self._validate_tree(tree)
        self.device = self.device or device_identity(self.config)
        label = self.device.get("label") or self.device["device_id"][:12]
        self._git(
            "-c", "user.name=MO State Sync",
            "-c", "user.email=state-sync@local.invalid",
            "commit", "-m", f"MO profile sync from {label}",
        )
        return True

    def _push_if_ahead(self) -> bool:
        if not self._has_head():
            return False
        proc = self._git("push", "origin", f"HEAD:refs/heads/{self.settings.branch}", check=False)
        if proc.returncode != 0:
            raise StateSyncError("state push failed")
        output = " ".join((proc.stdout, proc.stderr)).lower()
        return "everything up-to-date" not in output and "everything up to date" not in output

    def _ensure_remote(self) -> None:
        current = self._git("remote", "get-url", "origin", check=False)
        if current.returncode == 0:
            if current.stdout.strip() != self.settings.remote:
                self._git("remote", "set-url", "origin", self.settings.remote)
        else:
            self._git("remote", "add", "origin", self.settings.remote)

    def _tracked_worktree_dirty(self, *, include_retired: bool = True) -> bool:
        paths = self.tracked_paths + (RETIRED_SYNC_TOMBSTONES if include_retired else ())
        proc = self._git(
            "status", "--porcelain", "--", *paths,
        )
        return bool(proc.stdout.strip())

    def _tracked_worktree_paths(self, *, include_retired: bool = True) -> tuple[str, ...]:
        """Return exact dirty manifest paths for actionable conflict reports."""
        paths = self.tracked_paths + (RETIRED_SYNC_TOMBSTONES if include_retired else ())
        raw = self._git(
            "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths,
        ).stdout
        entries = raw.split("\0")
        dirty: list[str] = []
        index = 0
        while index < len(entries):
            item = entries[index]
            index += 1
            if len(item) < 4 or item[2] != " ":
                continue
            state = item[:2]
            path = item[3:].replace("\\", "/").strip("/")
            if path:
                dirty.append(path)
            # Porcelain v1 -z emits the original path as a second NUL field for
            # renames/copies. The destination above is the conflict path.
            if "R" in state or "C" in state:
                index += 1
        return tuple(dict.fromkeys(dirty))

    def _has_head(self) -> bool:
        return self._git("rev-parse", "--verify", "HEAD", check=False).returncode == 0

    def _has_local_candidates(self) -> bool:
        """Return whether first attach would overwrite allowlisted local content."""
        for rel in self.tracked_paths:
            target = self.home / rel
            if target.is_file() or _path_is_link(target):
                return True
            if target.is_dir() and any(True for _path in _candidate_files(target)):
                return True
        return False

    def _remote_tree_matches_local(
        self,
        treeish: str,
        *,
        ignore_legacy_artifacts: bool = False,
    ) -> bool:
        """Return whether local canonical files exactly match the selected state tree."""
        remote: dict[str, str] = {}
        entries = [item for item in self._git("ls-tree", "-r", "-z", treeish).stdout.split("\0") if item]
        for item in entries:
            meta, sep, path = item.partition("\t")
            fields = meta.split()
            if not sep or len(fields) < 3:
                return False
            relpath = path.replace("\\", "/").strip("/")
            if ignore_legacy_artifacts and relpath in LEGACY_SYNC_ARTIFACTS:
                continue
            remote[relpath] = fields[2]
        local: dict[str, Path] = {}
        for rel in self.tracked_paths:
            target = self.home / rel
            if not target.exists():
                continue
            for path in _candidate_files(target):
                if not path.is_file() or _path_is_link(path):
                    return False
                local[path.resolve(strict=False).relative_to(self.home.resolve(strict=False)).as_posix()] = path
        if set(local) != set(remote):
            return False
        for relpath, path in local.items():
            object_id = self._git(
                "hash-object", "--no-filters", str(path),
            ).stdout.strip()
            if not object_id or object_id != remote[relpath]:
                return False
        return True

    def _ref_exists(self, ref: str) -> bool:
        return self._git("show-ref", "--verify", "--quiet", ref, check=False).returncode == 0

    def _tree_paths(self, treeish: str) -> tuple[str, ...]:
        return tuple(
            path for path in self._git("ls-tree", "-r", "--name-only", "-z", treeish).stdout.split("\0")
            if path
        )

    def _diff_paths(self, left: str, right: str) -> tuple[str, ...]:
        paths = self.tracked_paths + RETIRED_SYNC_TOMBSTONES
        return tuple(
            path for path in self._git(
                "diff", "--name-only", "-z", left, right, "--", *paths,
            ).stdout.split("\0") if path
        )

    def _path_is_tracked(self, relpath: str) -> bool:
        return bool(self._git("ls-files", "--", relpath).stdout.strip())

    def _retired_deletions(self) -> tuple[str, ...]:
        """Return retired paths that are tracked but already absent canonically."""
        if not self._has_head():
            return ()
        return tuple(
            relpath
            for relpath in RETIRED_SYNC_TOMBSTONES
            if not (self.home / relpath).exists() and self._path_is_tracked(relpath)
        )

    def _validate_tree(self, treeish: str, *, retired_tombstones: tuple[str, ...] = ()) -> None:
        """Reject undeclared, linked, oversized, secret, or executable remote content before checkout."""
        tree_id = self._git("rev-parse", f"{treeish}^{{tree}}").stdout.strip()
        cache = self._read_validation_cache()
        validated_trees = [str(item) for item in cache.get("trees", []) if isinstance(item, str)]
        if tree_id and tree_id in validated_trees and not retired_tombstones:
            return
        validated_blobs = {str(item) for item in cache.get("blobs", []) if isinstance(item, str)}
        newly_validated: set[str] = set()
        proc = self._git("ls-tree", "-r", "-l", "-z", treeish)
        entries = [item for item in proc.stdout.split("\0") if item]
        if len(entries) > MAX_TREE_FILES:
            raise StateSyncError("state tree contains too many files")
        total_bytes = 0
        used_retired_tombstone = False
        used_legacy_artifact = False
        for item in entries:
            meta, sep, path = item.partition("\t")
            fields = meta.split()
            if not sep or len(fields) != 4:
                raise StateSyncError("state tree contains an invalid entry")
            mode, object_type, object_id, raw_size = fields
            relpath = path.replace("\\", "/").strip("/")
            if object_type != "blob" or mode not in {"100644", "100755"}:
                raise StateSyncError(f"linked or special sync entry blocked: {relpath[:160]}")
            if mode == "100755":
                raise StateSyncError(f"executable sync entry blocked: {relpath[:160]}")
            if not _allowed_tree_path(relpath, self.tracked_entries) and relpath not in retired_tombstones:
                raise StateSyncError(f"undeclared sync entry blocked: {relpath[:160]}")
            used_retired_tombstone = used_retired_tombstone or relpath in retired_tombstones
            try:
                size = int(raw_size)
            except ValueError:
                raise StateSyncError("state tree contains an invalid file size") from None
            if size < 0 or size > self.settings.max_file_bytes:
                raise StateSyncError(f"sync candidate exceeds size limit: {relpath[:160]}")
            total_bytes += size
            if total_bytes > MAX_TREE_BYTES:
                raise StateSyncError("state tree exceeds the total size limit")
            leaf = relpath.rsplit("/", 1)[-1]
            if leaf == ".mo-skills.lock":
                if relpath not in LEGACY_SYNC_ARTIFACTS:
                    raise StateSyncError(f"undeclared operational sync entry blocked: {relpath[:160]}")
                content = self._git("show", f"{treeish}:{relpath}").stdout
                if size != 1 or content != "\0":
                    raise StateSyncError(f"invalid legacy operational sync entry blocked: {relpath[:160]}")
                used_legacy_artifact = True
                continue
            if _secret_shaped_name(leaf):
                raise StateSyncError(f"secret-shaped sync path blocked: {relpath[:160]}")
            virtual_path = Path(relpath)
            if _text_candidate(virtual_path):
                if object_id not in validated_blobs:
                    content = self._git("show", f"{treeish}:{relpath}").stdout
                    if contains_secret_value(content):
                        raise StateSyncError(f"secret material detected in sync candidate: {relpath[:160]}")
                    newly_validated.add(object_id)
            elif not _safe_binary_candidate(virtual_path):
                raise StateSyncError(f"unsupported sync file type: {relpath[:160]}")
            else:
                newly_validated.add(object_id)
        # Trees containing migration-only paths must be reconsidered until the
        # synchronizer has committed their removal.
        if tree_id and not used_retired_tombstone and not used_legacy_artifact:
            validated_trees.append(tree_id)
        blobs = list((validated_blobs | newly_validated))[-20_000:]
        self._write_validation_cache({"version": 1, "trees": validated_trees[-200:], "blobs": blobs})

    def _read_validation_cache(self) -> dict[str, Any]:
        path = self.validation_cache_path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(raw, dict) or int(raw.get("version") or 0) != 1:
            return {}
        return raw

    def _write_validation_cache(self, data: dict[str, Any]) -> None:
        path = self.validation_cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, data, indent=2, ensure_ascii=False)

    def _is_ancestor(self, left: str, right: str) -> bool:
        return self._git("merge-base", "--is-ancestor", left, right, check=False).returncode == 0

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self._run_raw(
            [
                "git",
                "-C",
                str(self.home),
                "--git-dir",
                str(self.git_dir),
                "--work-tree",
                str(self.home),
                *args,
            ],
            check=check,
        )

    @staticmethod
    def _run_raw(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        run_kwargs: dict[str, Any] = {
            "text": True,
            "encoding": "utf-8",
            "errors": "strict",
            "capture_output": True,
            "timeout": 60,
        }
        # Profile synchronization normally runs inside the resident Desktop or
        # headless service. Keep its short Git children on the same hidden
        # background-process path instead of exposing child process UI.
        apply_windows_hidden_process_flags(run_kwargs)
        try:
            proc = subprocess.run(args, **run_kwargs)
        except UnicodeDecodeError:
            raise StateSyncError("Git produced non-UTF-8 output") from None
        if check and proc.returncode != 0:
            raise StateSyncError(_safe_git_error(proc))
        return proc

    def _write_status(self, result: SyncResult) -> None:
        path = self.status_path
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, asdict(result), indent=2, ensure_ascii=False)


def render_sync_status(config: dict[str, Any] | None = None) -> str:
    backend = GitStateSync(config)
    status = backend.status()
    lines = [
        "Consistent Everywhere:",
        f"  enabled: {status['enabled']}",
        f"  remote configured: {status['configured']}",
        f"  state repository initialized: {status['initialized']}",
        f"  committed profile baseline: {status['baseline_established']}",
        f"  Git/OpenSSH available: {status['git_available']}/{status['ssh_available']}",
        "  mutable databases: not synchronized (safe P1 boundary)",
        "  paths: " + ", ".join(status["tracked_paths"]),
    ]
    path = backend.status_path
    if path.is_file():
        try:
            last = json.loads(path.read_text(encoding="utf-8"))
            lines.append(f"  last result: {last.get('state', 'unknown')} at {last.get('created_at', 0)}")
        except (OSError, json.JSONDecodeError):
            pass
    return "\n".join(lines)


def _candidate_files(path: Path) -> Iterable[Path]:
    candidate_root = path if path.is_dir() else path.parent
    if _path_is_link(path):
        yield path
        return
    if _legacy_sync_artifact_file(path, candidate_root):
        return
    if path.is_file():
        yield path
        return
    for root, dirs, files in os.walk(path, followlinks=False):
        base = Path(root)
        for name in list(dirs):
            child = base / name
            if _path_is_link(child):
                yield child
                dirs.remove(name)
        for name in files:
            child = base / name
            if _legacy_sync_artifact_file(child, candidate_root) and not _path_is_link(child):
                continue
            yield child


def _legacy_sync_artifact_file(path: Path, candidate_root: Path) -> bool:
    return (
        candidate_root.name == "skills"
        and path.parent == candidate_root
        and path.name == ".mo-skills.lock"
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _path_is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    try:
        return bool(callable(is_junction) and is_junction())
    except OSError:
        return True


def _secret_shaped_name(name: str) -> bool:
    low = str(name or "").lower()
    return low in {".env", "credentials.json", "id_rsa", "id_ed25519"} or low.endswith((".pem", ".key", ".p12", ".pfx"))


def _text_candidate(path: Path) -> bool:
    return path.suffix.lower() in {
        "", ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml",
        ".py", ".ps1", ".sh", ".bat", ".cmd", ".css", ".js", ".html", ".svg", ".csv",
    }


def _safe_binary_candidate(path: Path) -> bool:
    return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}


def _allowed_tree_path(path: str, allowed: dict[str, str]) -> bool:
    if (
        not path
        or path.startswith("/")
        or any(ord(ch) < 32 for ch in path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        return False
    return any(path == rel or kind == "dir" and path.startswith(rel.rstrip("/") + "/") for rel, kind in allowed.items())


def _safe_branch(value: Any) -> str:
    branch = "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in "-._/").strip("./")
    return branch[:120] or DEFAULT_BRANCH


def _valid_remote(value: str) -> bool:
    remote = str(value or "").strip()
    if not remote or any(ord(ch) < 32 for ch in remote) or remote.startswith("-"):
        return False
    lowered = remote.lower()
    if lowered.startswith(("http://", "https://", "git://", "ftp://")):
        return False
    if lowered.startswith("ssh://") or re.match(r"^[^@\s]+@[^:\s]+:.+$", remote):
        return True
    try:
        return Path(remote).expanduser().is_absolute()
    except OSError:
        return False


def _safe_git_error(proc: subprocess.CompletedProcess[str]) -> str:
    text = " ".join((proc.stderr or "", proc.stdout or "")).strip()
    # Never echo a configured remote URL or credential-bearing command.
    line = text.splitlines()[-1] if text else f"git exited {proc.returncode}"
    line = re.sub(r"[A-Za-z][A-Za-z0-9+.-]*://\S+", "<remote>", line)
    line = re.sub(r"\S+@[^\s:]+:\S+", "<remote>", line)
    return line[:300]
