#!/usr/bin/env python3
# pyright: strict
"""Every time a change passed the not-loosened guard on a `Quality-exception: <why>` line, on record.

The guard lets a commit loosen a check if it says why; this lists each such commit on a branch, so the reasons
are counted and read in the quality report instead of disappearing into history.

    scripts/exceptions.py [--rev origin/main] [--json FILE]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = "Quality-exception:"


def exceptions(rev: str) -> list[dict[str, str]]:
    out = subprocess.run(["git", "log", "--format=%h%x1f%as%x1f%s%x1f%B%x1e", f"--grep=^{MARK}", rev],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    found: list[dict[str, str]] = []
    for record in filter(str.strip, out.split("\x1e")):
        sha, day, subject, body = record.strip().split("\x1f", 3)
        reasons = [line[len(MARK):].strip() for line in body.splitlines() if line.startswith(MARK)]
        found.append({"commit": sha, "date": day, "subject": subject, "why": "; ".join(reasons)})
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--rev", default="origin/main")
    parser.add_argument("--json", metavar="FILE", help="write the list here")
    args = parser.parse_args()
    found = exceptions(args.rev)
    for item in found:
        print(f"{item['date']} {item['commit']} {item['subject'][:70]}\n    why: {item['why']}")
    print(f"{len(found)} exceptions on {args.rev}")
    if args.json:
        Path(args.json).write_text(json.dumps({"rev": args.rev, "exceptions": found}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
