"""Native MO Phone companion window.

A self-contained MO Desktop app in the same shape as ``mo_desktop.files``: one
package owning its window, its view model and its capture sink, exported through
a single window class so the tray and companion stay loosely coupled.
"""

from .window import MoPhoneWindow

__all__ = ["MoPhoneWindow"]
