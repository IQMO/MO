"""Generate the renderer-neutral MO cube emote contract.

``mo_desktop.emotes`` remains the only authored motion library. Renderers that
cannot execute Python consume this deterministic sampled contract instead.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import emotes

SCHEMA = "mo.emotes.renderer"
VERSION = 1
CUBE_COUNT = 4
SAMPLE_COUNT = 21
DEFAULT_PATH = (
    Path(__file__).resolve().parents[1]
    / "mo_everywhere"
    / "contracts"
    / "emotes_v1"
    / "mo-emotes-v1.json"
)


def _number(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if rounded == -0.0 else rounded


def build_contract() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name, (motion, duration) in emotes.EMOTES.items():
        checkpoints = []
        for step in range(SAMPLE_COUNT):
            progress = step / (SAMPLE_COUNT - 1)
            elapsed = float(duration) * progress
            cubes = []
            for index in range(CUBE_COUNT):
                dx, dy, brightness, alpha = emotes.sample(
                    motion,
                    index,
                    elapsed,
                    float(duration),
                    cube_count=CUBE_COUNT,
                )
                cubes.append(
                    {
                        "dx": _number(dx),
                        "dy": _number(dy),
                        "brightness": _number(brightness),
                        "alpha": _number(alpha),
                        "accent": _number(emotes.accent(motion, index, elapsed, float(duration))),
                    }
                )
            checkpoints.append({"progress": _number(progress), "cubes": cubes})
        rows.append(
            {
                "name": name,
                "duration_ms": int(round(float(duration) * 1000.0)),
                "still": emotes.holds_still(motion),
                "loop": emotes.loops(motion),
                "samples": checkpoints,
            }
        )
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "cube_count": CUBE_COUNT,
        "corner_order": ["top_left", "top_right", "bottom_left", "bottom_right"],
        "events": {key: list(value) for key, value in emotes.EMOTE_EVENTS.items()},
        "emotes": rows,
    }


def render_contract() -> str:
    return json.dumps(build_contract(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_contract(path: Path = DEFAULT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_contract(), encoding="utf-8")
    return path


def contract_matches(path: Path = DEFAULT_PATH) -> bool:
    try:
        return path.read_text(encoding="utf-8") == render_contract()
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the checked-in contract is stale")
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    if args.check:
        return 0 if contract_matches(args.output) else 1
    write_contract(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
