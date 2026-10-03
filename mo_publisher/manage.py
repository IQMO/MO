"""Local/SSH operator commands; never expose these as anonymous web routes."""

import argparse
import json
import os
from pathlib import Path

from mo_publisher.reports import OUTCOMES, configured_store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("pending", help="List report IDs, times and outcomes without content")
    commands.add_parser("purge", help="Erase reports at the 30-day retention boundary")
    export = commands.add_parser("export", help="Write one report to a new private file for human review")
    export.add_argument("report_id")
    export.add_argument("--output", type=Path, required=True)
    review = commands.add_parser("review")
    review.add_argument("report_id")
    review.add_argument("outcome", choices=sorted(OUTCOMES))
    delete = commands.add_parser("delete", help="Erase one report after verifying its receipt")
    delete.add_argument("receipt")
    args = parser.parse_args()
    if not Path(os.environ.get("MO_STATE_HOME", "")).is_absolute() or os.environ.get("MO_STATE_LOCAL"):
        parser.error("Set a dedicated absolute MO_STATE_HOME")
    store = configured_store()
    if args.command == "pending":
        result = store.pending()
    elif args.command == "purge":
        result = {"deleted": store.purge()}
    elif args.command == "export":
        row = store.read(args.report_id)
        if row is None:
            parser.error("Report not found or expired")
        if not args.output.is_absolute():
            parser.error("Use an absolute private output path")
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(row, handle, indent=2)
        result = {"exported": True, "reminder": "Delete the private export immediately after review"}
    elif args.command == "review":
        result = {"updated": store.review(args.report_id, args.outcome)}
    else:
        result = {"deleted": store.delete(args.receipt)}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
