#!/usr/bin/env python3
"""Quality ratchet: new code meets the strict lint rules, old code may not get worse.

Runs the strict Python rules (ruff.strict.toml) and the JS rules (biome.json) and counts findings per
rule across the repo, so moving code between files is free (size rules count how far over the limit). The counts are compared with
quality-baseline.json:

- a rule above its baseline (or a rule that had none) fails, naming the files that hold it;
- a count below its baseline is progress: `--update` writes the lower number so it can't come back.

`--update` never raises a number, so the baseline can only move down.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "quality-baseline.json"
# Pinned: another version finds other things, and the baseline would drift.
RUFF_VERSION = "0.17.6"
BIOME_VERSION = "2.2.4"
# Biome's GitHub reporter prints one finding per line: ::error title=lint/x/y,file=web/a.js,line=3,...
# Size rules (too complex, too many branches, statements, arguments) say "(34 > 15)": they count the excess,
# so a giant function that grows fails too, not only a new one.
EXCESS = re.compile(r"\((\d+) > (\d+)\)")
BIOME_LINE = re.compile(r"^::(?:error|warning|notice) title=(?P<rule>[^,]+),file=(?P<file>[^,]+),")


Findings = dict[str, Counter[str]]  # rule -> file -> count


def ruff_findings() -> Findings:
    ruff = shutil.which("ruff")
    local = ruff and subprocess.run([ruff, "--version"], capture_output=True, text=True, check=False).stdout.split()[-1:]
    cmd = [ruff] if local == [RUFF_VERSION] else ["uvx", f"ruff@{RUFF_VERSION}"]
    out = subprocess.run(
        [*cmd, "check", "--config", "ruff.strict.toml", "--output-format", "json", "--exit-zero", "--no-cache", "."],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    found: Findings = {}
    for item in json.loads(out):
        path = Path(item["filename"]).resolve().relative_to(ROOT).as_posix()
        excess = EXCESS.search(item["message"]) if item["code"].startswith(("C901", "PLR09")) else None
        weight = int(excess[1]) - int(excess[2]) if excess else 1
        found.setdefault(item["code"], Counter())[path] += weight
    return found


def biome_findings() -> Findings | None:
    """Biome's findings on web/, or None on a machine without Node (CI always has it)."""
    if not shutil.which("npx"):
        print("warning: no Node here, the JS rules are not checked (CI checks them)")
        return None
    proc = subprocess.run(
        ["npx", "-y", f"@biomejs/biome@{BIOME_VERSION}", "lint", "--reporter=github", "--max-diagnostics=none", "web"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    found: Findings = {}
    for line in proc.stdout.splitlines():
        match = BIOME_LINE.match(line)
        if match:
            found.setdefault(match["rule"], Counter())[match["file"]] += 1
    if proc.returncode not in (0, 1) or (proc.returncode == 1 and not found):
        sys.exit(f"biome failed to run:\n{proc.stderr[-2000:]}")
    return found


def changed_files() -> set[str]:
    """Files this change touches (against CHECK_BASE, default origin/main), so a failure points at them first."""
    base = os.environ.get("CHECK_BASE", "origin/main")
    git = ["git", "-C", str(ROOT)]
    diff = subprocess.run([*git, "diff", "--name-only", base], capture_output=True, text=True, check=False).stdout
    new = subprocess.run([*git, "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True, check=False).stdout
    return set((diff + new).split())


def totals(found: Findings) -> dict[str, int]:
    return {rule: sum(files.values()) for rule, files in sorted(found.items())}


def compare(found: Findings, baseline: dict[str, int], skipped: str = "") -> tuple[list[str], dict[str, int]]:
    """Rules that got worse (naming the changed files that hold them), and the baseline lowered to today's counts.
    Rules starting with `skipped` were not measured on this machine: they keep their baseline."""
    current = totals(found)
    touched = changed_files()
    worse = []
    for rule, n in current.items():
        if n > baseline.get(rule, 0):
            files = found[rule].most_common()
            likely = [(path, k) for path, k in files if path in touched] or files
            where = ", ".join(f"{path} ({k})" for path, k in likely[:5])
            worse.append(f"{rule}: {n}, baseline {baseline.get(rule, 0)}. In: {where}")
    lowered = {rule: n if skipped and rule.startswith(skipped) else min(n, current.get(rule, 0)) for rule, n in baseline.items()}
    return worse, {rule: n for rule, n in lowered.items() if n}


def baseline_raised(rev: str) -> bool:
    """Whether quality-baseline.json allows more of any rule than it did at `rev`."""
    old = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:{BASELINE.name}"], capture_output=True, text=True, check=False)
    if old.returncode:
        return False  # no baseline at that commit yet: nothing to compare
    before = json.loads(old.stdout)
    return any(n > before.get(rule, 0) for rule, n in json.loads(BASELINE.read_text()).items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="write lowered counts back (never raises one)")
    parser.add_argument("--init", action="store_true", help="write today's counts as the first baseline")
    parser.add_argument("--raised-since", metavar="REV", help="only report whether the baseline file went up since REV")
    args = parser.parse_args()
    if args.raised_since:
        return 1 if baseline_raised(args.raised_since) else 0

    js = biome_findings()
    found = ruff_findings() | (js or {})
    if js is None and args.init:
        sys.exit("--init needs Node for the JS rules")
    if args.init:
        BASELINE.write_text(json.dumps(totals(found), indent=1) + "\n")
        print(f"baseline written: {sum(totals(found).values())} findings across {len(found)} rules")
        return 0

    baseline = json.loads(BASELINE.read_text())
    worse, lowered = compare(found, baseline, skipped="lint/" if js is None else "")
    if worse:
        print("Quality ratchet: this change adds lint findings. Fix the code, don't raise the baseline:")
        print("\n".join(f"  {line}" for line in worse))
        print("Details: ruff check --config ruff.strict.toml <file>  /  npx @biomejs/biome lint <file>")
        return 1
    gained = sum(baseline.values()) - sum(lowered.values())
    if args.update and gained:
        BASELINE.write_text(json.dumps(dict(sorted(lowered.items())), indent=1) + "\n")
        print(f"baseline lowered by {gained} findings")
    elif gained:
        print(f"ratchet ok; {gained} findings fixed: run scripts/ratchet.py --update and commit the baseline")
    else:
        print(f"ratchet ok ({sum(baseline.values())} known findings, none new)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
