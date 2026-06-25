"""Runnable isolated UX application."""
from __future__ import annotations

import argparse
import io
import sys

from rich.console import Console

from .controller import PreviewBackend, RuntimeBackend, UxController, read_only_snapshot
from .layout import build_screen
from .models import SessionSnapshot, demo_snapshot


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MO Agent's isolated next-generation terminal UX.")
    parser.add_argument("--width", type=int, default=110, help="render width for the preview")
    parser.add_argument("--once", action="store_true", help="render once and exit")
    parser.add_argument("--read-only", action="store_true", help="load MO runtime state without sending messages")
    parser.add_argument("--live", action="store_true", help="send messages through MO Gateway.run_turn")
    parser.add_argument("--smoke", action="store_true", help="run local UX smoke checks and exit")
    return parser.parse_args(argv)


def _prompt(console: Console) -> str:
    try:
        from prompt_toolkit import PromptSession
    except Exception:
        return console.input("[bold cyan]> [/]")
    session = PromptSession()
    return session.prompt("> ")


def _runtime_handle():
    from .runtime import create_runtime

    return create_runtime()


def _create_runtime_or_exit(console: Console):
    from .runtime import RuntimeUnavailable

    try:
        return _runtime_handle()
    except RuntimeUnavailable as exc:
        console.print(f"UX runtime unavailable: {exc}", style="red")
        raise SystemExit(2) from exc


def run_smoke(width: int = 100) -> str:
    controller = UxController(PreviewBackend())
    before = controller.snapshot()
    controller.handle_input("smoke input")
    after = controller.snapshot()
    if len(after.transcript) <= len(before.transcript):
        raise RuntimeError("preview transcript did not advance")
    console = Console(record=True, width=max(60, width), color_system=None, file=io.StringIO())
    console.print(build_screen(after))
    return console.export_text(clear=False)


class UxPreviewApp:
    """Small interactive shell for preview, read-only, and live runtime modes."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def render(self, snapshot: SessionSnapshot) -> None:
        self.console.print(build_screen(snapshot))

    def run(self, *, once: bool = False, snapshot: SessionSnapshot | None = None, controller: UxController | None = None) -> None:
        if once and snapshot is not None and controller is None:
            self.render(snapshot)
            return
        current_controller = controller or UxController(PreviewBackend(snapshot or demo_snapshot()))
        self.render(current_controller.snapshot())
        if once or not sys.stdin.isatty():
            return

        while True:
            try:
                user_input = _prompt(self.console).strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print("leaving UX preview", style="dim")
                return
            if not user_input:
                continue
            current_controller.handle_input(user_input)
            if current_controller.exit_requested:
                return
            self.console.clear()
            self.render(current_controller.snapshot())


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    console = Console(width=max(60, int(args.width or 110)))
    if args.smoke:
        console.print(run_smoke(width=args.width), markup=False)
        return
    if args.live and args.read_only:
        raise SystemExit("--live and --read-only are mutually exclusive")
    if args.read_only:
        handle = _create_runtime_or_exit(console)
        UxPreviewApp(console).run(once=True, snapshot=read_only_snapshot(handle))
        return
    if args.live:
        handle = _create_runtime_or_exit(console)
        UxPreviewApp(console).run(once=bool(args.once), controller=UxController(RuntimeBackend(handle)))
        return
    UxPreviewApp(console).run(once=bool(args.once))


if __name__ == "__main__":
    main()
