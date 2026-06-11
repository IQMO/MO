"""Mathematical animations for the Moon Mode MO logo."""
from __future__ import annotations

import math
import time
import threading
from typing import Any

# Gold glow base colors
R_BASE = 255
G_BASE = 180  # 180 to 220
B_BASE = 0

def calculate_moon_glow(timestamp: float) -> str:
    """
    Generate an animated foreground color style for the MO logo.
    Cycles between a deep gold and a bright yellow-gold.
    """
    speed = 2.0
    
    # Pulse the green channel to go between deep gold and bright yellow
    g_val = G_BASE + 40 * math.sin(timestamp * speed)
    g = max(0, min(255, int(g_val)))
    
    hex_color = f"#{R_BASE:02x}{g:02x}{B_BASE:02x}"
    # Return as foreground color and bold
    return f"fg:{hex_color} bold"


def start_moon_animation_tick(agent: Any, app: Any):
    """
    Start a background thread that slowly invalidates the UI to animate the logo
    when moon mode is enabled.
    """
    stop = threading.Event()

    def tick():
        while not stop.is_set():
            time.sleep(0.1)  # ~10 FPS for smooth text glow
            if getattr(agent, "_moon_mode_active", False):
                if app:
                    app.invalidate()

    t = threading.Thread(target=tick, daemon=True, name="mo-moon-tick")
    t.start()
    return stop
