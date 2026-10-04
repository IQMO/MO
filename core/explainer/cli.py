"""Command-line boundary used by MO's shipped explainer-video skill."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

from core.provider.provider import load_config

from .media import ingest_project_asset
from .model import ProjectValidationError, load_project
from .quality import quality_report
from .render import render_contact_sheet, render_video
from .storage import create_project, read_explainer_status, read_render_report, write_explainer_status
from .voice import synthesize_narration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create and render evidence-backed MO explainer videos")
    parser.add_argument("--config", default=None, help="MO config path; defaults to the canonical active config")
    subparsers = parser.add_subparsers(dest="command", required=True)
    guide = subparsers.add_parser("guide", help="read the bundled authoring and review guide")
    guide.add_argument("topic", nargs="?", choices=("elements",), help="elements: style, element and motion fields")

    init = subparsers.add_parser("init", help="create a private explainer project")
    init.add_argument("--title", required=True)
    init.add_argument("--slug", default="")
    init.add_argument("--output")
    init.add_argument("--layout", choices=("explanation", "process", "comparison", "product-demo"), default="explanation")
    init.add_argument("--size", type=_frame_size, default=(1280, 720),
                      help="WIDTHxHEIGHT, e.g. 1080x1920 for a vertical short (default 1280x720)")

    for name in ("validate", "check", "narrate", "sheet"):
        command = subparsers.add_parser(name)
        command.add_argument("project")
    sheet = subparsers.choices["sheet"]
    sheet.add_argument("--output")
    sheet.add_argument("--columns", type=int, default=3)

    render = subparsers.add_parser("render")
    render.add_argument("project")
    render.add_argument("--output")
    render.add_argument("--preview-seconds", type=float)
    render.add_argument("--preview-width", type=int)

    add_media = subparsers.add_parser("add-media", help="copy and hash one project-owned image or clip")
    add_media.add_argument("project")
    add_media.add_argument("source")
    add_media.add_argument("--id", required=True, dest="asset_id")
    add_media.add_argument("--origin", choices=("user", "captured", "mo-generated", "licensed-local"), required=True)

    status = subparsers.add_parser("status")
    status.add_argument("project", nargs="?")

    args = parser.parse_args(argv)
    project = None
    try:
        if args.command == "guide":
            print(guide_text(args.topic))
            return 0
        config = load_config(args.config)
        if args.command == "init":
            path = create_project(
                args.title,
                slug=args.slug,
                output=args.output,
                config=config,
                layout=args.layout,
                size=args.size,
            )
            return _print({
                "created": True,
                "project": str(path),
                "manifest": str(path / "project.json"),
                "status": read_explainer_status(path),
            })
        if args.command == "status":
            return _status(args.project, config)
        if args.command == "add-media":
            asset = ingest_project_asset(
                args.project, args.source, asset_id=args.asset_id, origin=args.origin,
            )
            return _print({"added": True, "asset": asset, "project": str(Path(args.project).resolve(strict=False))})
        project = load_project(args.project)
        if args.command == "validate":
            verification = {"status": "passed", "kind": "schema"}
            status_payload = write_explainer_status(
                project.directory, phase="validation", state="completed", completed=1, total=1,
                verification=verification,
            )
            return _print({
                "valid": True,
                "project": str(project.path),
                "scenes": len(project.scenes),
                "status": status_payload,
            })
        if args.command == "check":
            report = quality_report(project)
            report["valid"] = True
            report["project"] = str(project.path)
            report["status"] = write_explainer_status(
                project.directory,
                phase="quality",
                state="completed" if report["ok"] else "failed",
                completed=1,
                total=1,
                verification={
                    "status": "passed" if report["ok"] else "failed",
                    "kind": "quality",
                    "blocking_errors": len(report["errors"]),
                    "advisories": len(report["warnings"]),
                },
            )
            return _print(report, code=0 if report["ok"] else 3)
        if args.command == "narrate":
            _emit_progress(write_explainer_status(
                project.directory, phase="narration", state="running", completed=0, total=len(project.scenes),
                verification={"status": "pending", "kind": "audio"},
            ))
            audio, timings = synthesize_narration(
                project,
                config=config,
                progress=_progress_reporter(project.directory, "narration"),
            )
            status_payload = write_explainer_status(
                project.directory, phase="narration", state="completed",
                completed=len(project.scenes), total=len(project.scenes),
                verification={"status": "passed", "kind": "audio", "source": "piper"},
            )
            return _print({"narrated": True, "audio": str(audio), "timings": str(timings), "status": status_payload})
        if args.command == "sheet":
            _emit_progress(write_explainer_status(
                project.directory, phase="contact_sheet", state="running", completed=0, total=len(project.scenes),
                verification={"status": "pending", "kind": "visual_preview"},
            ))
            output = render_contact_sheet(project, output=args.output, columns=args.columns)
            status_payload = write_explainer_status(
                project.directory, phase="contact_sheet", state="completed",
                completed=len(project.scenes), total=len(project.scenes),
                verification={"status": "generated_unchecked", "kind": "visual_preview"},
            )
            return _print({"rendered": True, "contact_sheet": str(output), "status": status_payload})
        if args.command == "render":
            phase = "preview" if args.preview_seconds else "render"
            _emit_progress(write_explainer_status(
                project.directory, phase=phase, state="running", completed=0, total=0,
                verification={"status": "pending", "kind": "ffprobe"},
            ))
            output = render_video(
                project,
                output=args.output,
                preview_seconds=args.preview_seconds,
                preview_width=args.preview_width,
                progress=_progress_reporter(project.directory, phase),
            )
            inspection = read_render_report(output)
            status_payload = write_explainer_status(
                project.directory,
                phase=phase,
                state="completed",
                completed=int(inspection.get("frame_count") or 0),
                total=int(inspection.get("frame_count") or 0),
                verification={
                    "status": "passed",
                    "kind": "ffprobe",
                    "duration_seconds": inspection.get("duration_seconds"),
                    "frame_count": inspection.get("frame_count"),
                    "narrated": inspection.get("narrated"),
                    "sha256": inspection.get("sha256"),
                    "path": str(output),
                },
            )
            return _print({
                "rendered": True,
                "video": str(output),
                "bytes": output.stat().st_size,
                "preview_seconds": args.preview_seconds,
                "preview_width": args.preview_width,
                "inspection": inspection,
                "status": status_payload,
            })
    except KeyboardInterrupt:
        if project is not None:
            try:
                interrupted_phase = (
                    "preview"
                    if args.command == "render" and getattr(args, "preview_seconds", None)
                    else _phase_name(str(args.command))
                )
                _emit_progress(write_explainer_status(
                    project.directory,
                    phase=interrupted_phase,
                    state="interrupted",
                    verification={"status": "failed", "detail": "cancelled by operator"},
                ))
            except OSError:
                pass
        return _print({"error": "explainer operation cancelled", "type": "KeyboardInterrupt"}, code=130)
    except (OSError, ProjectValidationError, RuntimeError, ValueError) as exc:
        if project is not None:
            try:
                _emit_progress(write_explainer_status(
                    project.directory,
                    phase=_phase_name(str(args.command)),
                    state="interrupted" if "cancel" in str(exc).lower() else "failed",
                    verification={"status": "failed", "detail": str(exc)[:300]},
                ))
            except OSError:
                pass
        return _print({"error": str(exc), "type": type(exc).__name__}, code=2)
    return 1


def _frame_size(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in str(value).lower().split("x", 1))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("size must look like 1080x1920") from exc
    return width, height


def guide_text(topic: str | None = None) -> str:
    """Return one bundled README section; each fits the shell result cap."""
    reference = Path(__file__).with_name("README.md").read_text(encoding="utf-8")
    preamble, _separator, body = reference.partition("\n## ")
    sections = {block.split("\n", 1)[0]: "## " + block.rstrip() for block in body.split("\n## ")}
    if topic == "elements":
        return sections["Elements and style"]
    return f"{preamble.rstrip()}\n\n{sections['Authoring']}"


def _status(project_path: str | None, config: dict) -> int:
    payload = {
        "pillow": _module_available("PIL"),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
    }
    try:
        from mo_desktop.voice.storage import resolve_voice_install_root, voice_runtime_integrity

        payload["voice"] = voice_runtime_integrity(resolve_voice_install_root(config))
    except Exception:
        payload["voice"] = "unavailable"
    if project_path:
        try:
            project = load_project(project_path)
            payload.update({
                "project_valid": True,
                "project": str(project.path),
                "operation": read_explainer_status(project.directory),
            })
        except ProjectValidationError as exc:
            payload.update({"project_valid": False, "project_error": str(exc)})
    return _print(payload)


def _progress_reporter(directory: Path, phase: str):
    def _report(completed: int, total: int) -> None:
        _emit_progress(write_explainer_status(
            directory,
            phase=phase,
            state="running",
            completed=completed,
            total=total,
            verification={"status": "pending"},
        ))
    return _report


def _emit_progress(status: dict) -> None:
    """Project the existing status receipt into one bounded live output line."""
    progress = status.get("progress") or {}
    style = status.get("selected_style") or {}
    verification = status.get("verification") or {}
    event = {
        "event": "explainer_progress",
        "phase": status.get("phase"),
        "state": status.get("state"),
        "elapsed_seconds": status.get("elapsed_seconds", 0),
        "completed": progress.get("completed", 0),
        "total": progress.get("total", 0),
        "style": {key: str(style.get(key) or "")[:64] for key in ("source", "skin", "layout")},
        "artifacts": {
            name: {key: item[key] for key in ("available", "verification", "narrated") if key in item}
            for name, item in (status.get("artifacts") or {}).items()
        },
        "verification": {key: verification[key] for key in ("status", "kind") if key in verification},
    }
    print(json.dumps(event, ensure_ascii=True, separators=(",", ":")), file=sys.stderr, flush=True)


def _phase_name(command: str) -> str:
    return "contact_sheet" if command == "sheet" else command


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _print(payload: dict, *, code: int = 0) -> int:
    status = payload.get("status") or payload.get("operation")
    if isinstance(status, dict):
        _emit_progress(status)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
