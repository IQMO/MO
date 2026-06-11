#!/usr/bin/env python3
"""Separate safe backend monitor window for MO Agent."""

from core.text_safety import configure_utf8_stdio
from interface import monitor_terminal as _monitor_terminal

configure_utf8_stdio()


DEFAULT_LOG_PATH = _monitor_terminal.DEFAULT_LOG_PATH
clear_screen = _monitor_terminal.clear_screen
read_events = _monitor_terminal.read_events
render = _monitor_terminal.render
resolve_log_path = _monitor_terminal.resolve_log_path
main = _monitor_terminal.main


if __name__ == "__main__":
    main()
