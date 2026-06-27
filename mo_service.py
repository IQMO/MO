#!/usr/bin/env python3
# ruff: noqa: E402
"""MO Agent headless service entrypoint.

Use this for VPS/systemd/background operation. It does not start the TUI.
"""
from __future__ import annotations

import os
import sys

# Redirect Python's bytecode cache OUT of the checkout instead of disabling it.
# Disabling bytecode writes recompiled all ~370 modules in memory on every
# service (re)start (~7s, never cached). On a VPS/systemd loop that tax is paid on
# each restart. pycache_prefix under ~/.mo keeps the checkout clean while caching,
# so restarts drop ~10x. A read-only home degrades to no-cache, never an error.
_MO_HOME = os.environ.get("MO_HOME") or os.path.join(os.path.expanduser("~"), ".mo")
sys.pycache_prefix = os.path.join(_MO_HOME, "pycache")

from core.utils.text_safety import configure_utf8_stdio

configure_utf8_stdio()

CALLER_CWD = os.environ.get("MO_PROJECT_CWD") or os.getcwd()
AGENT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("MO_PROJECT_CWD", CALLER_CWD)
os.environ.setdefault("MO_INVOKED_AS", os.path.splitext(os.path.basename(sys.argv[0] or "mo_service"))[0] or "mo_service")
os.chdir(AGENT_ROOT)
sys.path.insert(0, AGENT_ROOT)

from core.runtime.lock import acquire_runtime_lock
from core.runtime.service import main as service_main


def main() -> int:
    if not acquire_runtime_lock(lock_name="mo-service.lock", label="MO Agent service"):
        return 1
    return service_main()


if __name__ == "__main__":
    raise SystemExit(main())
