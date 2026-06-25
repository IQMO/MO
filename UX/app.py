"""Runnable isolated UX preview."""
from __future__ import annotations

import argparse
import sys

from rich.console import Console

from .layout import build_screen
from .models import SessionSnapshot, TranscriptItem, demo_snapshot


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview MO Agent's isolated next-generation terminal UX.")
    parser.add_argument("--width", type=int, default=110, help="render width for the preview")
    parser.add_argument("--once", action="store_true", help="render once and exit")
    return parser.parse_args(argv)


class UxPreviewApp:
    """Small interactive preview that never calls the MO runtime."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def render(self, snapshot: SessionSnapshot) -> None:
        self.console.print(build_screen(snapshot))

    def run(self, *, once: bool = False, snapshot: SessionSnapshot | None = None) -> None:
        current = snapshot or demo_snapshot()
        self.render(current)
        if once or not sys.stdin.isatty():
            return

        transcript = list(current.transcript)
        while True:
            try:
                user_input = self.console.input("[bold cyan]> [/]").strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print("leaving UX preview", style="dim")
                return
            if user_input.lower() in {"/exit", "/quit", "exit", "quit"}:
                return
            if not user_input:
                continue
            transcript.append(TranscriptItem("user", user_input))
            transcript.append(TranscriptItem("mo", "UX preview captured input locally. Runtime wiring is intentionally off."))
            current = SessionSnapshot(
                product=current.product,
                project=current.project,
                runtime=current.runtime,
                provider=current.provider,
                model=current.model,
                busy=False,
                notice="Preview only - input was not sent to MO runtime",
                lanes=current.lanes,
                board=current.board,
                transcript=tuple(transcript[-8:]),
                composer_placeholder=current.composer_placeholder,
                composer_hint=current.composer_hint,
            )
            self.console.clear()
            self.render(current)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    console = Console(width=max(60, int(args.width or 110)))
    UxPreviewApp(console).run(once=bool(args.once))


if __name__ == "__main__":
    main()
