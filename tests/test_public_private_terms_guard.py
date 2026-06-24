from __future__ import annotations

from pathlib import Path
import subprocess


PRIVATE_OWNER_TERMS = tuple(
    "".join(parts).lower()
    for parts in (
        ("dev", "mode", "05"),
        ("vs", "05"),
        ("if", "dev", "05"),
        ("iam", "05"),
    )
)


def test_private_owner_codenames_do_not_ship_in_tracked_files():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    offenders: list[str] = []
    for rel in proc.stdout.splitlines():
        lowered_path = rel.lower()
        if any(term in lowered_path for term in PRIVATE_OWNER_TERMS):
            offenders.append(rel)
            continue
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(term in text for term in PRIVATE_OWNER_TERMS):
            offenders.append(rel)
    assert offenders == []
