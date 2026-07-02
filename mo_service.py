#!/usr/bin/env python3
# ruff: noqa: E402
"""MO Agent headless service entrypoint.

Use this for VPS/systemd/background operation. It does not start the TUI.
"""
from __future__ import annotations

from core.runtime._bootstrap import bootstrap

AGENT_ROOT = bootstrap(__file__, invoked_as="mo_service")

from core.runtime.lock import acquire_runtime_lock
from core.runtime.service import main as service_main


def main() -> int:
    if not acquire_runtime_lock(lock_name="mo-service.lock", label="MO Agent service"):
        return 1
    return service_main()


if __name__ == "__main__":
    raise SystemExit(main())
