"""Deterministic public checks for MO's headless container boundary."""
from __future__ import annotations

from pathlib import Path
import shlex
from typing import Any

import yaml


REQUIRED_IGNORES = frozenset({
    ".git",
    ".agents",
    ".claude",
    ".codex",
    ".models",
    ".env",
    ".mcp.json",
    ".mo",
    ".venv",
    "@AutomationLog.txt",
    "NUL",
    "captures",
    "clients",
    "config.yaml",
    "credentials",
    "credentials.json",
    "device-output",
    "docs",
    "google-services.json",
    "keystore.properties",
    "local.properties",
    "memory",
    "logs",
    "operator.token",
    "personal",
    "operator",
    "service-account.json",
    "signing",
    "tests",
    "tmp",
})


def check_packaging(root: str | Path | None = None) -> list[str]:
    """Return container packaging violations without invoking Docker."""
    project = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    problems: list[str] = []
    dockerfile = _read(project / "Dockerfile", problems)
    dockerignore = _read(project / ".dockerignore", problems)
    compose_text = _read(project / "compose.yaml", problems)
    if problems:
        return problems

    for required in (
        "USER user",
        "MO_STATE_HOME=/home/user/.mo",
        "MO_PROJECT_CWD=/workspace",
        'ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/mo_service.py"]',
    ):
        if required not in dockerfile:
            problems.append(f"Dockerfile missing required boundary: {required}")
    if "COPY . " in dockerfile or "COPY .\n" in dockerfile:
        problems.append("Dockerfile must copy explicit product sources, not the whole checkout")
    for source in _docker_copy_sources(dockerfile, problems):
        if not (project / source).exists():
            problems.append(f"Dockerfile COPY source does not exist: {source}")

    ignored = {
        line.strip()
        for line in dockerignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing_ignores = sorted(REQUIRED_IGNORES - ignored)
    if missing_ignores:
        problems.append(".dockerignore missing private/local paths: " + ", ".join(missing_ignores))

    try:
        compose = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        problems.append(f"compose.yaml is invalid YAML: {type(exc).__name__}")
        return problems
    service = _service(compose)
    if service is None:
        problems.append("compose.yaml must define services.mo")
        return problems
    if service.get("read_only") is not True:
        problems.append("compose service must keep the image filesystem read-only")
    if service.get("privileged") is True:
        problems.append("compose service must never be privileged")
    if service.get("ports"):
        problems.append("default compose service must not publish ports")
    if service.get("network_mode") == "host":
        problems.append("default compose service must not use host networking")
    if set(service.get("cap_drop") or ()) != {"ALL"}:
        problems.append("compose service must drop all Linux capabilities")
    if set(service.get("security_opt") or ()) != {"no-new-privileges:true"}:
        problems.append("compose service must enable no-new-privileges")

    environment = service.get("environment") if isinstance(service.get("environment"), dict) else {}
    expected_environment = {
        "MO_STATE_HOME": "/home/user/.mo",
        "MO_CONFIG": "/home/user/.mo/config.yaml",
        "MO_PROJECT_CWD": "/workspace",
    }
    for name, value in expected_environment.items():
        if environment.get(name) != value:
            problems.append(f"compose environment {name} must be {value}")

    mounts = service.get("volumes") if isinstance(service.get("volumes"), list) else []
    targets = {
        str(item.get("target") or ""): item
        for item in mounts
        if isinstance(item, dict)
    }
    state_mount = targets.get("/home/user/.mo")
    workspace_mount = targets.get("/workspace")
    if not state_mount or state_mount.get("type") != "bind" or not str(state_mount.get("source") or "").startswith("${MO_STATE_DIR:?"):
        problems.append("compose must require one explicit private-state bind at /home/user/.mo")
    if not workspace_mount or workspace_mount.get("type") != "bind":
        problems.append("compose must mount the selected project at /workspace")
    return problems


def _read(path: Path, problems: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        problems.append(f"missing packaging artifact: {path.name}")
        return ""


def _docker_copy_sources(dockerfile: str, problems: list[str]) -> list[str]:
    """Return literal build-context sources from the tracked COPY instructions."""
    sources: list[str] = []
    for line_number, raw_line in enumerate(dockerfile.splitlines(), start=1):
        line = raw_line.strip()
        if not line.upper().startswith("COPY "):
            continue
        try:
            parts = shlex.split(line, posix=True)
        except ValueError:
            problems.append(f"Dockerfile COPY instruction is invalid at line {line_number}")
            continue
        options = [part for part in parts[1:] if part.startswith("--")]
        if any(option.startswith("--from=") for option in options):
            continue
        arguments = [part for part in parts[1:] if not part.startswith("--")]
        if len(arguments) < 2:
            problems.append(f"Dockerfile COPY instruction is invalid at line {line_number}")
            continue
        for source in arguments[:-1]:
            if Path(source).is_absolute() or source.startswith("../"):
                problems.append(f"Dockerfile COPY source must stay inside the build context: {source}")
                continue
            sources.append(source.rstrip("/"))
    return sources


def _service(compose: Any) -> dict[str, Any] | None:
    if not isinstance(compose, dict):
        return None
    services = compose.get("services")
    if not isinstance(services, dict):
        return None
    service = services.get("mo")
    return service if isinstance(service, dict) else None


def main() -> int:
    problems = check_packaging()
    if problems:
        for problem in problems:
            print(f"[packaging] {problem}")
        return 1
    print("[packaging] non-root image, private-state mount, workspace mount, and public/private boundaries valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
