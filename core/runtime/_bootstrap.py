"""Shared MO runtime bootstrap — used by mo.py and mo_service.py.

Factor the identical startup scaffolding out of both entry points so
pycache-redirect, utf8-stdio, cwd/env setup lives in one place.
"""

from __future__ import annotations

import os
import sys


def bootstrap(caller_file: str, invoked_as: str = "mo") -> str:
    """Run shared startup and return AGENT_ROOT.

    * Redirects pycache under ~/.mo for clean-checkout caching (~10x cold-start).
    * Configures UTF-8 stdio.
    * Sets MO_PROJECT_CWD, MO_INVOKED_AS, cwd=AGENT_ROOT, and sys.path[0].

    Returns the resolved AGENT_ROOT path so callers don't recompute it.
    """
    _MO_HOME = os.environ.get("MO_HOME") or os.path.join(os.path.expanduser("~"), ".mo")
    sys.pycache_prefix = os.path.join(_MO_HOME, "pycache")

    from core.utils.text_safety import configure_utf8_stdio

    configure_utf8_stdio()

    CALLER_CWD = os.environ.get("MO_PROJECT_CWD") or os.getcwd()
    AGENT_ROOT = os.path.dirname(os.path.abspath(caller_file))
    os.environ.setdefault("MO_PROJECT_CWD", CALLER_CWD)
    os.environ.setdefault("MO_INVOKED_AS", os.path.splitext(os.path.basename(sys.argv[0] or invoked_as))[0] or invoked_as)
    os.chdir(AGENT_ROOT)
    sys.path.insert(0, AGENT_ROOT)
    return AGENT_ROOT
