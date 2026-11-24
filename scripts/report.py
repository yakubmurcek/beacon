#!/usr/bin/env python3
# pyright: strict
"""The quality report: one row per check, with its tool, today's number, when it runs and the canary that proves it.

Built only from the tools' own output: quality-baseline.json (the ratchets' numbers), the last nightly run's state
(canaries.json, nightly.json, exceptions.json in $QUALITY_STATE) and package-lock.json / requirements-dev.txt for
the tool versions. Nothing in it is an opinion.

    scripts/report.py [--state DIR] [--json FILE] > report.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("QUALITY_STATE", Path.home() / ".local/state/beacon-quality"))

# check, tool (package), what it holds, when it runs, the canaries that prove it red
CHECKS: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    ("Lint", "ruff", "syntax errors, undefined and unused names: zero", "every push", ("undefined name",)),
    ("Lint ratchet", "ruff", "strict rules (complexity 15, size, bugbear, security): counts only go down",
     "every push", ("too complex function", "dead test assertion")),
    ("JS lint ratchet", "@biomejs/biome", "web/ rules: counts only go down", "every push", ("JS lint finding",)),
    ("Type errors", "pyright", "errors per area only go down; new modules strict", "every push",
     ("type error", "new module not strict")),
    ("Tests + coverage", "coverage", "line+branch coverage per area only goes up", "every push", ("coverage drop",)),
    ("Copy-paste", "jscpd", "under 1% duplicated lines", "every push", ("copied code",)),
    ("Workflows", "zizmor", "pinned actions, no injectable expressions", "every push", ("injectable workflow",)),
    ("Checks not loosened", "git", "no skipped tests, suppressions, config edits or raised baselines without a reason",
     "every push", ("lint suppression", "skipped test", "loosened lint config", "raised baseline")),
    ("Vendored JS", "sha256 manifest", "every vendored file matches its npm release", "every push",
     ("tampered vendored file",)),
    ("Known CVEs", "osv-scanner + OSV API", "no known vulnerability in any lockfile or vendored file",
     "every push + nightly", ("vulnerable dependency",)),
    ("Secrets", "gitleaks + GitHub push protection", "no secret in the history", "every push", ("committed secret",)),
    ("Code scanning", "CodeQL (GitHub default setup)", "Python, JS and Actions queries", "every push (GitHub)", ()),
]


def load(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text())) if path.exists() else {}


def versions() -> dict[str, str]:
    found: dict[str, str] = {}
    lock = load(ROOT / "package-lock.json").get("packages", {})
    for key, info in cast("dict[str, dict[str, str]]", lock).items():
        if key.startswith("node_modules/") and key.count("node_modules/") == 1:
            found[key.removeprefix("node_modules/")] = info.get("version", "")
    for name, version in re.findall(r"^([a-z0-9-]+)==([\w.]+)", (ROOT / "requirements-dev.txt").read_text(), re.M):
        found[name] = version
    return found


def numbers(baseline: dict[str, Any]) -> dict[str, str]:
    lint = cast("dict[str, int]", baseline.get("lint", {}))
    py = sum(n for rule, n in lint.items() if not rule.startswith("lint/"))
    js = sum(n for rule, n in lint.items() if rule.startswith("lint/"))
    cov = cast("dict[str, float]", baseline.get("at_least", {}))
    types = cast("dict[str, int]", baseline.get("at_most", {}))
    return {
        "Lint ratchet": f"{py} old findings held",
        "JS lint ratchet": f"{js} old findings held",
        "Type errors": ", ".join(f"{k.split()[1]} {v}" for k, v in sorted(types.items())),
        "Tests + coverage": ", ".join(f"{k.split()[1]} {v}%" for k, v in sorted(cov.items())),
    }


def build(state: Path) -> dict[str, Any]:
    baseline = load(ROOT / "quality-baseline.json")
    canaries = {r["name"]: r for r in cast("list[dict[str, Any]]", load(state / "canaries.json").get("results", []))}
    nightly = load(state / "nightly.json")
    exceptions = cast("list[dict[str, str]]", load(state / "exceptions.json").get("exceptions", []))
    tools, nums = versions(), numbers(baseline)
    rows: list[dict[str, Any]] = []
    for name, tool, holds, when, proofs in CHECKS:
        version = tools.get(tool, "")
        rows.append({
            "check": name, "tool": f"{tool} {version}".strip(), "holds": holds, "number": nums.get(name, ""),
            "when": when,
            "proven_red_by": [{"canary": p, "caught": bool(canaries.get(p, {}).get("caught"))} for p in proofs],
        })
    return {"at": time.time(), "nightly": nightly, "rows": rows, "exceptions": exceptions,
            "canaries": {"caught": sum(1 for r in canaries.values() if r["caught"]), "of": len(canaries)}}


def markdown(report: dict[str, Any]) -> str:
    night = report["nightly"]
    lines = ["# Quality report", ""]
    if night:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(night["at"]))
        steps = ", ".join(f"{k} {v}" for k, v in night["steps"].items())
        lines += [f"Last nightly run {when} on {night['commit']}: {steps}.", ""]
    lines += [f"Canaries caught: {report['canaries']['caught']} of {report['canaries']['of']}. "
              f"Self-granted exceptions on main: {len(report['exceptions'])}.", "",
              "| Check | Tool | Holds | Today | Runs | Proven red by |", "|---|---|---|---|---|---|"]
    for r in report["rows"]:
        proofs = ", ".join(f"{'✓' if p['caught'] else '✗'} {p['canary']}" for p in r["proven_red_by"]) or "no canary yet"
        lines.append(f"| {r['check']} | {r['tool']} | {r['holds']} | {r['number']} | {r['when']} | {proofs} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--json", metavar="FILE")
    args = parser.parse_args()
    report = build(args.state)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    sys.stdout.write(markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
