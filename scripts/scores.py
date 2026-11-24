#!/usr/bin/env python3
# pyright: strict
"""Quality scores: test coverage and type errors, held by a ratchet like the lint counts.

quality-baseline.json keeps two sections next to the lint counts:

- `at_least`: scores that may only go up (test coverage per area, the mutation score). A drop fails.
- `at_most`: counts that may only go down (pyright errors per area). A rise fails.

    scripts/scores.py coverage   # after `coverage run` (scripts/check does it): coverage per area vs the floor
    scripts/scores.py types      # pyright per area vs the ceiling
    --update                     # writes the better numbers back; never a worse one

New Python modules carry `# pyright: strict` (scripts/check enforces it), so they start at zero type errors.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "quality-baseline.json"
PYRIGHT_VERSION = "1.1.414"
# The areas a score is kept for. client/ holds extensionless Python CLIs, so its files are listed one by one.
AREAS = {
    "hub": ["hub"],
    "collector": ["collector"],
    "client": sorted(str(p.relative_to(ROOT)) for p in (ROOT / "client").iterdir()
                     if p.is_file() and p.read_bytes()[:40].startswith(b"#!/usr/bin/env python3")),
    "scripts": ["scripts"],
}
COVERED = ("hub", "collector", "client")  # scripts/ is tooling, mostly run by hand: no coverage floor


Baseline = dict[str, dict[str, float]]


def load() -> Baseline:
    return cast("Baseline", json.loads(BASELINE.read_text()))


def save(data: Baseline) -> None:
    BASELINE.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def area_of(path: str) -> str | None:
    rel = Path(path).resolve().relative_to(ROOT).as_posix() if Path(path).is_absolute() else path
    return next((area for area, parts in AREAS.items() if any(rel == p or rel.startswith(p + "/") for p in parts)), None)


def coverage_scores() -> dict[str, float]:
    """Branch-and-line coverage per area, from the .coverage file `coverage run` left."""
    out = subprocess.run([sys.executable, "-m", "coverage", "json", "-q", "-o", "-"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    sums: dict[str, list[int]] = {}
    files = cast("dict[str, dict[str, Any]]", json.loads(out)["files"])
    for path, info in files.items():
        area = area_of(path)
        if area in COVERED:
            s = cast("dict[str, int]", info["summary"])
            done = s["covered_lines"] + s.get("covered_branches", 0)
            total = s["num_statements"] + s.get("num_branches", 0)
            acc = sums.setdefault(area, [0, 0])
            acc[0] += done
            acc[1] += total
    return {f"coverage {area} %": round(100 * done / total, 1) for area, (done, total) in sums.items() if total}


def type_errors() -> dict[str, float]:
    """Pyright's error count per area (settings in pyrightconfig.json)."""
    counts: dict[str, float] = {}
    for area, paths in AREAS.items():
        proc = subprocess.run(["npx", "-y", f"pyright@{PYRIGHT_VERSION}", "--outputjson", *paths],
                              cwd=ROOT, capture_output=True, text=True, check=False)
        try:
            counts[f"pyright {area}"] = int(json.loads(proc.stdout)["summary"]["errorCount"])
        except (json.JSONDecodeError, KeyError):
            sys.exit(f"pyright failed to run on {area}:\n{proc.stdout[-1500:]}{proc.stderr[-1500:]}")
    return counts


def judge(now: Mapping[str, float], section: str, update: bool) -> int:
    """Compare today's numbers with the baseline section; 1 when one got worse."""
    data = load()
    held = data.setdefault(section, {})
    higher = section == "at_least"
    worse: list[str] = []
    better: list[str] = []
    for key, value in sorted(now.items()):
        old = held.get(key)
        if old is None:
            held[key] = value
            better.append(f"{key}: {value} (first measure)")
        elif (value < old) if higher else (value > old):
            worse.append(f"{key}: {value}, baseline {old}")
        elif value != old:
            better.append(f"{key}: {old} → {value}")
            held[key] = value
    for line in [f"  {k}: {v}" for k, v in sorted(now.items())]:
        print(line)
    if worse:
        what = "lowers test coverage" if higher else "adds type errors"
        print(f"This change {what}. Add the tests / fix the types, don't change the baseline:")
        print("\n".join(f"  {w}" for w in worse))
        return 1
    if better and update:
        save(data)
        print("baseline moved: " + "; ".join(better))
    elif better:
        print("better than the baseline (" + "; ".join(better) + "): run with --update and commit quality-baseline.json")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("what", choices=["coverage", "types"])
    parser.add_argument("--update", action="store_true", help="write the better numbers back (never a worse one)")
    args = parser.parse_args()
    if args.what == "coverage":
        return judge(coverage_scores(), "at_least", args.update)
    return judge(type_errors(), "at_most", args.update)


if __name__ == "__main__":
    sys.exit(main())
