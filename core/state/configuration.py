"""Atomic authored-configuration edits shared by native Settings and Desktop.

This is the existing scoped Desktop YAML writer promoted to the state owner.
Readers and defaults remain with their domains; edits preserve unrelated blocks.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

from .paths import resolve_state_path, runtime_config_path
from ..runtime.lock import file_byte_lock
from ..utils.atomic_write import atomic_write_text

_LOCK = threading.RLock()

def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base or {})
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _replace_top_level_block(text: str, key: str, block_text: str) -> str | None:
    """Replace ONLY the ``key:`` top-level block in ``text`` with ``block_text`` (which
    must itself start ``key:``), preserving every other line — comments and sibling
    blocks included. Appends the block if absent. Returns None if it can't be done
    safely."""
    import yaml

    lines = text.splitlines()
    block_lines = block_text.splitlines()
    if not block_lines or not block_lines[0].startswith(key + ":"):
        return None
    document = yaml.compose(text)
    if document is not None and not isinstance(document, yaml.MappingNode):
        return None
    matches = [(name, value) for name, value in (document.value if document else []) if name.value == key]
    if len(matches) > 1:
        return None
    if not matches:
        body = text.rstrip("\n")
        return (body + "\n\n" if body else "") + "\n".join(block_lines) + "\n"
    name, value = matches[0]
    start = name.start_mark.line
    end = value.end_mark.line + bool(value.end_mark.column)
    # YAML permits unindented sequences and quoted keys. Parser marks avoid
    # guessing ownership from indentation; retain trailing sibling comments.
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    new_lines = lines[:start] + block_lines + lines[end:]
    return "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")


def persist_configuration(config: dict[str, Any], changes: dict[str, Any], *,
                          remove: tuple[str, ...] = (), update_runtime: bool = True) -> bool:
    """Merge selected top-level blocks under one cross-process source lock.

    Callers validate their own supported fields. None is an ordinary YAML value;
    removal is explicit. A failed parse, unsafe rewrite or write leaves memory
    unchanged. Restart-only editors pass update_runtime=False.
    """
    import yaml
    source = runtime_config_path(config, fallback_to_default=True)
    if not source or not (changes or remove):
        return False
    path = Path(source)
    identity = hashlib.sha256(str(path.resolve()).casefold().encode()).hexdigest()[:24]
    lock = Path(resolve_state_path(f"run/configuration/{identity}.lock", config))
    try:
        with file_byte_lock(lock, _LOCK):
            text = path.read_text(encoding="utf-8")
            data = yaml.safe_load(text) or {}
            if not isinstance(data, dict):
                return False
            merged = _deep_merge(data, changes)
            touched = set(changes)
            for dotted in remove:
                keys = dotted.split(".")
                touched.add(keys[0])
                node = merged
                for key in keys[:-1]:
                    node = node.get(key) if isinstance(node, dict) else None
                if isinstance(node, dict):
                    node.pop(keys[-1], None)
            updated = text
            for key in sorted(touched):
                if key not in merged:
                    continue  # resetting an absent override leaves its source untouched
                block = yaml.safe_dump({key: merged.get(key, {})}, default_flow_style=False,
                                       sort_keys=False, allow_unicode=True).rstrip("\n")
                updated = _replace_top_level_block(updated, key, block)
                if updated is None:
                    return False
            # Prove the textual replacement retained every semantic sibling.
            if (yaml.safe_load(updated) or {}) != merged:
                return False
            if updated != text:
                atomic_write_text(path, updated)
            if update_runtime:
                import copy
                runtime = _deep_merge(copy.deepcopy(config), changes)
                for dotted in remove:
                    keys = dotted.split(".")
                    node = runtime
                    for part in keys[:-1]:
                        node = node.get(part) if isinstance(node, dict) else None
                    if isinstance(node, dict):
                        node.pop(keys[-1], None)
                for key in touched:
                    config[key] = runtime.get(key, {})
            return True
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return False
