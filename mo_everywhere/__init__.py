"""Optional mobile overview and remote-control policy surface for MO Agent.

This package deliberately stays import-light. FastAPI and uvicorn are loaded
only when the separately configured headless service starts the web surface.
"""

from .server import start_everywhere_api_if_enabled

__all__ = ["start_everywhere_api_if_enabled"]
