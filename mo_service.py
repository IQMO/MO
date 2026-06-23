#!/usr/bin/env python3
# ruff: noqa: E402
"""MO Agent headless service entrypoint.

Use this for VPS/systemd/background operation. It does not start the TUI.
"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True

from core.text_safety import configure_utf8_stdio

configure_utf8_stdio()

CALLER_CWD = os.environ.get("MO_PROJECT_CWD") or os.getcwd()
AGENT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("MO_PROJECT_CWD", CALLER_CWD)
os.environ.setdefault("MO_INVOKED_AS", os.path.splitext(os.path.basename(sys.argv[0] or "mo_service"))[0] or "mo_service")
os.chdir(AGENT_ROOT)
sys.path.insert(0, AGENT_ROOT)

from core.runtime_lock import acquire_runtime_lock
from core.service import main as service_main


def main() -> int:
    if not acquire_runtime_lock(lock_name="mo-service.lock", label="MO Agent service"):
        return 1
    return service_main()


if __name__ == "__main__":
    raise SystemExit(main())
