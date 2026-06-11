"""Compatibility exports for slash-command metadata.

LEGACY GUARD: do not define command lists here. The only source of truth is
`interface/command_registry.py`.
"""
from __future__ import annotations

from .command_registry import (
    SLASH_ALIASES,
    SLASH_COMMAND_HELP,
    SLASH_COMMANDS,
    SLASH_SUBCOMMANDS,
    slash_command_names,
    slash_command_with_desc,
)

__all__ = [
    "SLASH_COMMANDS",
    "SLASH_ALIASES",
    "SLASH_COMMAND_HELP",
    "SLASH_SUBCOMMANDS",
    "slash_command_names",
    "slash_command_with_desc",
]
