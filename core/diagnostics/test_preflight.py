"""Fast preflight for broad maintainer pytest runs.

The full suite is still the authority. This module only fails cheap checks first:
public/private and canonical credential-source boundary guards, then a bounded
collect-only pass.
"""
from __future__ import annotations

import ast
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from .source_inventory import discover_source_paths
    from ..runtime.subprocess_flags import apply_windows_hidden_process_flags
    from ..utils.text_safety import contains_hardcoded_secret_literal
except ImportError:
    # This module is also executed as a plain script by the shell tool's
    # preflight lane (possibly from an unrelated cwd), where no package
    # context exists. Bootstrap the repo root and import absolutely.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.diagnostics.source_inventory import discover_source_paths
    from core.runtime.subprocess_flags import apply_windows_hidden_process_flags
    from core.utils.text_safety import contains_hardcoded_secret_literal


def _run_hidden(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a captured preflight helper without allocating a Windows console."""
    apply_windows_hidden_process_flags(kwargs)
    return subprocess.run(command, **kwargs)


def _pytest_subprocess_env(*, skip_public_private_preflight: bool = False) -> dict[str, str]:
    """Build env for pytest/preflight subprocesses without checkout bytecode."""
    env = os.environ.copy()
    env["PYTHONPYCACHEPREFIX"] = str(Path(tempfile.gettempdir()) / "mo-test-pycache")
    if skip_public_private_preflight:
        env["MO_SKIP_PUBLIC_PRIVATE_PREFLIGHT"] = "1"
    return env


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str


_PRIVATE_REPOSITORY_NAMES = frozenset({
    ".env",
    ".env.local",
    "credentials.json",
    "google-services.json",
    "keystore.properties",
    "local.properties",
    "operator.token",
    "service-account.json",
})
_PRIVATE_REPOSITORY_ROOT_NAMES = frozenset({
    "@automationlog.txt",
    "config.yaml",
    "nul",
})
_PRIVATE_REPOSITORY_SUFFIXES = (
    ".hprof", ".jks", ".key", ".keystore", ".p12", ".pem", ".pfx",
)
_PRIVATE_REPOSITORY_DIRECTORIES = frozenset({
    ".mo", ".pytest_cache", ".ruff_cache", "__pycache__", "captures",
    "device-output", "operator", "personal", "signing", "tmp",
})
_PRIVATE_REPOSITORY_ROOT_DIRECTORIES = frozenset({
    ".agents", ".claude", ".codex", ".models", ".venv", "credentials",
    "desktop_studio", "docs", "logs", "memory",
})
_OVERLAY_MANIFEST_NAME = ".mo-test-overlay.json"
_CANONICAL_SECRET_FILES = {
    "providers": "credentials/providers.env",
    "telegram": "credentials/telegram.env",
    "gmail": "credentials/gmail.env",
}
_NONCANONICAL_SECRET_SOURCE_NAMES = (
    "providers_file",
    "secret_files",
    "telegram_file",
)
_SECRET_ENV_NAME_RE = re.compile(
    r"(?:^|_)(?:"
    r"API_KEY|API_KEY_ENV|API_SECRET|API_SECRET_ENV|PROVIDER_KEY|PROVIDER_KEY_ENV|"
    r"ACCESS_TOKEN|AUTH_TOKEN|BOT_TOKEN|"
    r"CLIENT_SECRET|CREDENTIALS?|PASSWORD|PRIVATE_KEY|SECRET_ENV|TOKEN|TOKEN_ENV"
    r")$",
    re.IGNORECASE,
)
_WINDOWS_PRIVATE_HOME_RE = re.compile(
    r"(?i)\b[A-Z]:[\\/]Users[\\/]([^<>:\"/\\|\r\n]+)[\\/]\.(?:mo|codex|ssh)(?:[\\/]|$)"
)
_POSIX_PRIVATE_HOME_RE = re.compile(
    r"(?i)/(?:home|Users)/([^/\s<>]+?)/\.(?:mo|codex|ssh)(?:/|$)"
)
_MAINTAINER_SESSION_PATH_RE = re.compile(
    r"(?i)(?:^|[\\/])tmp[\\/]maintainers[\\/](?:codex|claude)-[0-9a-f]{8,}"
)
_GENERIC_HOME_NAMES = frozenset({
    "demo", "example", "name", "sample", "user", "username",
})
_MAX_PUBLICATION_SCAN_BYTES = 2 * 1024 * 1024
_PRIVATE_ANDROID_PRODUCT_PATHS = frozenset({
    ".github/workflows/android-release.yml",
    "core/diagnostics/android_release_manifest.py",
    "core/diagnostics/android_release_preflight.py",
})


def repo_root(cwd: str | Path | None = None) -> Path:
    path = Path(cwd or os.getcwd()).resolve()
    try:
        proc = _run_hidden(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            text=True,
            capture_output=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except Exception:
        pass
    return path


def _is_mo_checkout(root: Path) -> bool:
    return (
        (root / "mo.py").is_file()
        and (root / "AGENTS.md").is_file()
        and (root / "core" / "local_extensions.py").is_file()
    )


def _tail(text: str, limit: int = 4000) -> str:
    clean = str(text or "").strip()
    return clean[-limit:] if len(clean) > limit else clean


def _is_private_android_path(value: str) -> bool:
    clean = str(value or "").replace("\\", "/").strip("/")
    parts = tuple(part.lower() for part in clean.split("/") if part)
    return (
        len(parts) >= 2 and parts[:2] == ("clients", "android")
    ) or "/".join(parts) in _PRIVATE_ANDROID_PRODUCT_PATHS


def _is_private_repository_path(value: str) -> bool:
    clean = str(value or "").replace("\\", "/").strip("/")
    parts = tuple(part.lower() for part in clean.split("/") if part)
    if not parts:
        return False
    name = parts[-1]
    directories = parts[:-1]
    return (
        name in _PRIVATE_REPOSITORY_NAMES
        or (len(parts) == 1 and name in _PRIVATE_REPOSITORY_ROOT_NAMES)
        or name.endswith(_PRIVATE_REPOSITORY_SUFFIXES)
        or any(part in _PRIVATE_REPOSITORY_DIRECTORIES for part in directories)
        or parts[0] in _PRIVATE_REPOSITORY_ROOT_DIRECTORIES
        or name.startswith(("bugreport", "logcat", "screenrecord", "tombstone"))
    )


def _tracked_publication_content_violations(
    root: Path,
    tracked_paths: list[str],
) -> list[str]:
    """Return high-confidence, value-free findings in tracked text files."""
    violations: list[str] = []
    for rel in tracked_paths:
        clean = str(rel or "").replace("\\", "/").strip("/")
        if not clean:
            continue
        path = root / clean
        try:
            if not path.is_file() or path.stat().st_size > _MAX_PUBLICATION_SCAN_BYTES:
                continue
            payload = path.read_bytes()
        except OSError:
            continue
        if b"\0" in payload:
            continue
        text = payload.decode("utf-8", errors="replace")
        if contains_hardcoded_secret_literal(text):
            violations.append(f"{clean}: hardcoded secret-shaped literal")
        if _contains_concrete_private_home(text):
            violations.append(f"{clean}: concrete private-home path")
        if _MAINTAINER_SESSION_PATH_RE.search(text):
            violations.append(f"{clean}: maintainer session scratch path")
    return violations


def _contains_concrete_private_home(text: str) -> bool:
    for pattern in (_WINDOWS_PRIVATE_HOME_RE, _POSIX_PRIVATE_HOME_RE):
        for match in pattern.finditer(str(text or "")):
            home_name = match.group(1).strip().lower()
            if (
                home_name not in _GENERIC_HOME_NAMES
                and not any(marker in home_name for marker in ("$", "{", "}", "%"))
            ):
                return True
    return False


def _expr_name(value: ast.AST) -> str:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return ""


def _environment_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    environ = {"environ"}
    getenv = {"getenv"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for name in node.names:
                target = name.asname or name.name
                if name.name == "environ":
                    environ.add(target)
                elif name.name == "getenv":
                    getenv.add(target)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "os"
                and value.attr == "environ"
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        environ.add(target.id)
    return environ, getenv


def _environment_key_expr(
    node: ast.AST,
    *,
    environ_aliases: set[str],
    getenv_aliases: set[str],
) -> ast.AST | None:
    if isinstance(node, ast.Call) and node.args:
        func = node.func
        if isinstance(func, ast.Name) and func.id in getenv_aliases:
            return node.args[0]
        if isinstance(func, ast.Attribute) and func.attr == "getenv":
            return node.args[0]
        if isinstance(func, ast.Attribute) and func.attr in {"get", "setdefault"}:
            owner = func.value
            if (
                isinstance(owner, ast.Name)
                and owner.id in environ_aliases
            ) or (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "os"
                and owner.attr == "environ"
            ):
                return node.args[0]
    if isinstance(node, ast.Subscript):
        owner = node.value
        if (
            isinstance(owner, ast.Name)
            and owner.id in environ_aliases
        ) or (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "os"
            and owner.attr == "environ"
        ):
            return node.slice
    return None


def _secret_environment_read_lines(text: str) -> tuple[int, ...]:
    # Almost every tracked Python file reaches this guard, but only files that
    # name both a secret-shaped key and an environment accessor can contain a
    # relevant read. Avoid building hundreds of irrelevant ASTs on every
    # delivery preflight; the import spelling still contains getenv/environ
    # when callers use an alias.
    if "getenv" not in text and "environ" not in text:
        return ()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ()
    environ_aliases, getenv_aliases = _environment_aliases(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        key = _environment_key_expr(
            node,
            environ_aliases=environ_aliases,
            getenv_aliases=getenv_aliases,
        )
        if key is not None and _SECRET_ENV_NAME_RE.search(_expr_name(key)):
            lines.append(int(getattr(node, "lineno", 0) or 0))
    return tuple(dict.fromkeys(lines))


def _assigned_literal_dict(tree: ast.AST, name: str) -> dict[str, str] | None:
    for node in getattr(tree, "body", ()):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    return None


def _canonical_credential_source_violations(
    root: Path,
    tracked_paths: list[str],
) -> list[str]:
    """Return value-free drift findings without consulting the owner profile."""
    state_root = root / "core" / "state"
    if not state_root.is_dir():
        return []

    violations: list[str] = []
    broker_rel = "core/state/secrets.py"
    broker_path = root / broker_rel
    if not broker_path.is_file():
        violations.append(f"{broker_rel}: canonical credential broker is missing")
    else:
        try:
            broker_tree = ast.parse(broker_path.read_text(encoding="utf-8"))
            mapping = _assigned_literal_dict(broker_tree, "SERVICE_FILE_RELPATHS")
        except (OSError, SyntaxError):
            mapping = None
        if mapping != _CANONICAL_SECRET_FILES:
            violations.append(
                f"{broker_rel}: SERVICE_FILE_RELPATHS must remain the exact canonical service map"
            )

    provider_rel = "core/provider/provider.py"
    provider_path = root / provider_rel
    required_rejections = (
        "_reject_noncanonical_secret_sources(config, path)",
        *_NONCANONICAL_SECRET_SOURCE_NAMES,
    )
    try:
        provider_text = provider_path.read_text(encoding="utf-8")
    except OSError:
        provider_text = ""
    missing_rejections = [
        item for item in required_rejections if item not in provider_text
    ]
    if missing_rejections:
        violations.append(
            f"{provider_rel}: noncanonical credential-source rejection is incomplete"
        )

    allowed_noncanonical_mentions = {
        "core/diagnostics/test_preflight.py",
        provider_rel,
    }
    allowed_env_parsers = {
        broker_rel,
    }
    python_paths = {
        str(rel or "").replace("\\", "/").strip("/")
        for rel in tracked_paths
        if str(rel or "").replace("\\", "/").strip("/").endswith(".py")
    }
    scan_paths = python_paths
    if (root / ".git").exists():
        # Git already owns the tracked-file index. Let its native search reduce
        # the scan to files that can contain one of these exact guard concerns,
        # while still reading dirty worktree content. Temp/non-Git test roots
        # retain the complete Python-path fallback below.
        pattern = "|".join((
            "getenv",
            "environ",
            *_NONCANONICAL_SECRET_SOURCE_NAMES,
            "parse_env_file",
        ))
        try:
            candidates = _run_hidden(
                ["git", "grep", "-l", "-z", "-E", pattern, "--", "*.py"],
                cwd=str(root),
                capture_output=True,
                timeout=20,
            )
        except Exception:
            candidates = None
        if candidates is not None and candidates.returncode in {0, 1}:
            scan_paths = {
                item
                for item in candidates.stdout.decode(
                    "utf-8", errors="replace"
                ).split("\0")
                if item in python_paths
            }

    for clean in sorted(scan_paths):
        path = root / clean
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in _secret_environment_read_lines(text):
            violations.append(
                f"{clean}:{line}: secret-shaped process-environment source is forbidden"
            )
        if clean not in allowed_noncanonical_mentions and any(
            re.search(rf"\b{re.escape(name)}\b", text)
            for name in _NONCANONICAL_SECRET_SOURCE_NAMES
        ):
            violations.append(
                f"{clean}: alternate credential-file configuration is forbidden"
            )
        if clean not in allowed_env_parsers and re.search(r"\bparse_env_file\s*\(", text):
            violations.append(
                f"{clean}: raw credential-file parsing must stay inside the broker or explicit migration"
            )
    return violations


def _test_overlay_digest(tests_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(tests_root.rglob("*.py"), key=lambda item: item.as_posix().lower()):
        relative = path.relative_to(tests_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_head(root: Path) -> tuple[str, str]:
    try:
        proc = _run_hidden(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=20,
        )
    except Exception as exc:
        return "", f"git rev-parse HEAD failed: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return "", "git rev-parse HEAD failed:\n" + _tail(proc.stderr or proc.stdout)
    return (proc.stdout or "").strip(), ""


def write_test_overlay_manifest(root: str | Path | None = None) -> CheckResult:
    root_path = repo_root(root)
    tests_root = root_path / "tests"
    if not tests_root.is_dir():
        return CheckResult(False, "[preflight] cannot write overlay manifest: tests/ is absent")
    head, error = _git_head(root_path)
    if error:
        return CheckResult(False, "[preflight] cannot write overlay manifest: " + error)
    payload = {
        "schema": 1,
        "source_head": head,
        "suite_sha256": _test_overlay_digest(tests_root),
    }
    manifest = tests_root / _OVERLAY_MANIFEST_NAME
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return CheckResult(True, f"[preflight] wrote test overlay manifest for {head[:12]}")


def validate_test_overlay(root: str | Path | None = None) -> CheckResult:
    root_path = repo_root(root)
    if not _is_mo_checkout(root_path):
        return CheckResult(True, "[preflight] test overlay pin skipped: not an MO checkout")
    tests_root = root_path / "tests"
    if not tests_root.is_dir():
        return CheckResult(True, "[preflight] test overlay skipped: tests/ is absent")
    manifest = tests_root / _OVERLAY_MANIFEST_NAME
    if not manifest.is_file():
        return CheckResult(
            False,
            f"[preflight] missing {manifest.name}; the local test overlay has no recorded source baseline. "
            "After reviewing intentional overlay changes, run "
            "`python -m core.diagnostics.test_preflight --write-overlay-manifest`.",
        )
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return CheckResult(False, f"[preflight] invalid {manifest.name}: {type(exc).__name__}: {exc}")
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        return CheckResult(False, f"[preflight] invalid {manifest.name}: expected schema 1")
    head, error = _git_head(root_path)
    if error:
        return CheckResult(False, "[preflight] overlay source check failed: " + error)
    if payload.get("source_head") != head:
        return CheckResult(
            False,
            f"[preflight] stale test overlay: pinned to {payload.get('source_head') or '<missing>'}, "
            f"checkout is {head}. Review the source change, then refresh the overlay manifest.",
        )
    expected_digest = str(payload.get("suite_sha256") or "")
    actual_digest = _test_overlay_digest(tests_root)
    if expected_digest != actual_digest:
        return CheckResult(
            False,
            f"[preflight] test overlay changed without manifest refresh: "
            f"expected {expected_digest or '<missing>'}, got {actual_digest}. "
            "Review the overlay edit, then refresh its manifest.",
        )
    return CheckResult(True, f"[preflight] test overlay: pinned to {head[:12]}")


def run_public_private_guards(root: str | Path | None = None) -> CheckResult:
    root_path = repo_root(root)
    if not _is_mo_checkout(root_path):
        return CheckResult(True, "[preflight] MO boundary guards skipped: not an MO checkout")

    try:
        tracked_paths = [
            relative_path for relative_path, _origin in discover_source_paths(
                root_path, include_untracked=False
            )
        ]
    except Exception as exc:
        return CheckResult(False, f"[preflight] tracked source discovery failed: {type(exc).__name__}")

    tracked_tests = [
        path
        for path in tracked_paths
        if path.replace("\\", "/").strip("/").startswith("tests/")
    ]
    if tracked_tests:
        return CheckResult(
            False,
            "[preflight] tests/ is tracked and would ship:\n"
            + "\n".join(tracked_tests[:20]),
        )
    forbidden = sorted(
        path
        for path in tracked_paths
        if _is_private_android_path(path) or _is_private_repository_path(path)
    )
    if forbidden:
        return CheckResult(
            False,
            "[preflight] private/local artifact is tracked and would ship:\n" + "\n".join(forbidden[:20]),
        )

    publication_content = _tracked_publication_content_violations(
        root_path,
        tracked_paths,
    )
    if publication_content:
        return CheckResult(
            False,
            "[preflight] tracked publication content violates the public boundary:\n"
            + "\n".join(publication_content[:20]),
        )

    credential_drift = _canonical_credential_source_violations(
        root_path,
        tracked_paths,
    )
    if credential_drift:
        return CheckResult(
            False,
            "[preflight] canonical credential-source drift detected:\n"
            + "\n".join(credential_drift[:20]),
        )

    return CheckResult(
        True,
        "[preflight] external-safe tracked-content and public/private guards: ok "
        "(owner profile was not inspected or executed)",
    )


def _count_collected_items(stdout: str) -> int:
    count = 0
    for line in str(stdout or "").splitlines():
        stripped = line.strip()
        if "::" in stripped and not stripped.startswith(("<", "=", "[")):
            count += 1
    return count


def run_collect_only(root: str | Path | None = None, *, timeout: int = 180) -> CheckResult:
    root_path = repo_root(root)
    if not (root_path / "tests").exists():
        return CheckResult(True, "[preflight] collect-only skipped: no local tests overlay")

    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    env = _pytest_subprocess_env(skip_public_private_preflight=True)
    start = time.perf_counter()
    try:
        proc = _run_hidden(
            cmd,
            cwd=str(root_path),
            text=True,
            capture_output=True,
            env=env,
            timeout=max(1, int(timeout or 180)),
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
        return CheckResult(False, "[preflight] collect-only timed out:\n" + _tail(output))
    elapsed = time.perf_counter() - start
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        return CheckResult(False, "[preflight] collect-only failed:\n" + _tail(output))
    count = _count_collected_items(proc.stdout or "")
    return CheckResult(True, f"[preflight] collect-only: ok ({count} item(s), {elapsed:.1f}s)")


def run_preflight(root: str | Path | None = None, *, collect: bool = True, timeout: int = 180) -> CheckResult:
    guards = run_public_private_guards(root)
    if not guards.ok:
        return guards
    overlay = validate_test_overlay(root)
    combined = guards.message + "\n" + overlay.message
    if not overlay.ok:
        return CheckResult(False, combined)
    if not collect:
        return CheckResult(True, combined)
    collected = run_collect_only(root, timeout=timeout)
    if not collected.ok:
        return CheckResult(False, combined + "\n" + collected.message)
    return CheckResult(True, combined + "\n" + collected.message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run fast MO test preflight checks before broad pytest suites.")
    parser.add_argument("--root", default="", help="Project root; defaults to git top-level or cwd.")
    parser.add_argument("--guards-only", action="store_true", help="Run boundary guards only; skip collect-only.")
    parser.add_argument("--collect", action="store_true", help="Run boundary guards and collect-only.")
    parser.add_argument(
        "--write-overlay-manifest",
        action="store_true",
        help="Pin the ignored local tests overlay to the current product HEAD and suite digest.",
    )
    parser.add_argument("--timeout", type=int, default=180, help="Collect-only timeout in seconds.")
    args = parser.parse_args(argv)

    if args.write_overlay_manifest:
        result = write_test_overlay_manifest(args.root or None)
    elif args.guards_only:
        result = run_public_private_guards(args.root or None)
    else:
        result = run_preflight(args.root or None, collect=True, timeout=args.timeout)
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
