"""Back-compat entrypoint: ``python -m interface.companion`` forwards to the renamed
``interface.ghost_desktop`` package so existing run-at-startup shortcuts created
before the module merge keep working. New shortcuts target ``interface.ghost_desktop``.
"""
from __future__ import annotations

import sys

from interface.ghost_desktop.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
