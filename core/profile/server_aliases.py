"""Read configured OpenSSH navigation aliases, never connection or key values."""
from __future__ import annotations

import glob
import ipaddress
from pathlib import Path
import re
import shlex


def configured_server_aliases(config_path: Path | None = None) -> list[str]:
    """Project explicit Host names only; configuration is not a health observation.

    Includes are read as configuration, not executed. Match/exec, HostName,
    User, IdentityFile and proxy directives never become output or actions.
    Unreadable/oversized configuration raises rather than claiming no hosts.
    """
    root = (config_path or Path.home() / ".ssh" / "config").expanduser()
    aliases: dict[str, str] = {}
    visited: set[Path] = set()

    def read(path: Path, *, optional: bool = False) -> None:
        path = path.resolve()
        if path in visited:
            return
        if len(visited) >= 32:
            raise ValueError("SSH configuration include limit")
        visited.add(path)
        try:
            with path.open("r", encoding="utf-8-sig") as stream:
                text = stream.read(262145)
        except FileNotFoundError:
            if optional:
                return
            raise
        if len(text) > 262144:
            raise ValueError("SSH configuration size limit")
        for line in text.splitlines():
            # Parse only directives needed for navigation; never parse values of
            # credential, endpoint or execution directives.
            match = re.match(r"^\s*(Host|Include)(?:\s*=\s*|\s+)(.*)$", line, re.I)
            if not match:
                continue
            directive, value = match.groups()
            tokens = shlex.split(value, comments=True, posix=True)
            if directive.casefold() == "include":
                for token in tokens:
                    include = Path(token).expanduser()
                    if not include.is_absolute():
                        include = root.parent / include
                    matches = glob.glob(str(include))
                    if len(matches) > 32:
                        raise ValueError("SSH configuration include limit")
                    for item in sorted(matches):
                        read(Path(item))
                continue
            for alias in tokens:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", alias):
                    continue
                try:
                    ipaddress.ip_address(alias)
                except ValueError:
                    aliases.setdefault(alias.casefold(), alias)
                if len(aliases) > 256:
                    raise ValueError("SSH alias limit")

    read(root, optional=True)
    return list(aliases.values())
