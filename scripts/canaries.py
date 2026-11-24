#!/usr/bin/env python3
# pyright: strict
"""Canaries: proof that every check still turns red on the thing it exists to catch.

Each canary in canaries.json is one known-bad change (a type error, a dead assertion, a loosened config, a committed
secret...). For each, a throwaway worktree of HEAD gets the change as one commit, the named check runs against the
commit before it, and the canary passes only when the check fails AND prints the expected text, so a check that
breaks for another reason does not count. The tools are borrowed from this checkout (.venv, node_modules, .tools).

    scripts/canaries.py                 # all of them (~1 min each, the coverage one ~4 min)
    scripts/canaries.py --only "type error" --json out.json

Exit 0 when every canary was caught; the JSON lists each with its result, for the quality report.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
BORROWED = (".venv", "node_modules", ".tools")
RUNS = {
    "fast": ["scripts/check", "--fast"],
    "full": ["scripts/check"],
    "supply-chain": ["scripts/supply-chain"],
}
Canary = dict[str, Any]


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def plant(tree: Path, canary: Canary) -> None:
    """Make the canary's one bad change in the worktree."""
    path = tree / canary["file"]
    if "create" in canary:
        path.write_text(canary["create"])
    elif "append" in canary:
        path.write_text(path.read_text() + canary["append"])
    elif "replace" in canary:
        old, new = canary["replace"]
        text = path.read_text()
        if old not in text:
            raise ValueError(f"{canary['file']} no longer holds the line the canary changes: {old!r}")
        path.write_text(text.replace(old, new, 1))
    elif "json_bump" in canary:
        section, key, by = canary["json_bump"]
        data = json.loads(path.read_text())
        data[section][key] += by
        path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    elif canary.get("secret"):  # made up at run time, so no secret-shaped string sits in the repo
        path.write_text(path.read_text() + f"\n\nGITHUB_TOKEN = 'ghp_{secrets.token_hex(18)}'\n")


def command(run: str) -> list[str]:
    if run.startswith("test "):
        return [".venv/bin/python", "-m", "unittest", run.split(" ", 1)[1]]
    return RUNS[run]


def try_one(canary: Canary) -> dict[str, Any]:
    # Next to the checkout, not in /tmp: some tests read meaning into where the repo sits.
    tree = Path(tempfile.mkdtemp(prefix=".canary-", dir=ROOT.parent))
    started = time.time()
    try:
        git("worktree", "add", "-q", "--detach", str(tree), "HEAD")
        plant(tree, canary)
        git("add", "-A", ".", cwd=tree)
        git("-c", "user.name=canary", "-c", "user.email=canary@example.invalid",
            "commit", "-q", "--no-verify", "-m", f"canary: {canary['name']}", cwd=tree)
        for name in BORROWED:  # after the commit: the links are not part of the change
            if (ROOT / name).exists():
                (tree / name).symlink_to(ROOT / name)
        proc = subprocess.run(command(canary["run"]), cwd=tree, capture_output=True, text=True, check=False,
                              env={**os.environ, "CHECK_BASE": "HEAD~1"})
        out = proc.stdout + proc.stderr
        caught = proc.returncode != 0 and canary["expect"] in out
        why = "caught" if caught else ("stayed green" if proc.returncode == 0 else "red, but not for this")
        return {"name": canary["name"], "check": canary["check"], "caught": caught, "result": why,
                "seconds": round(time.time() - started), "tail": "" if caught else out[-1500:]}
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=ROOT, capture_output=True, check=False)
        shutil.rmtree(tree, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--only", action="append", help="run only the canary with this name (repeatable)")
    parser.add_argument("--json", metavar="FILE", help="write the results here")
    args = parser.parse_args()
    canaries = cast("list[Canary]", json.loads((ROOT / "canaries.json").read_text())["canaries"])
    if args.only:
        canaries = [c for c in canaries if c["name"] in args.only]
    results: list[dict[str, Any]] = []
    for canary in canaries:
        result = try_one(canary)
        results.append(result)
        mark = "✓" if result["caught"] else "✗"
        print(f"{mark} {result['name']:<24} {result['check']:<28} {result['result']} ({result['seconds']} s)", flush=True)
        if result["tail"]:
            print("   " + result["tail"].strip().replace("\n", "\n   ")[-800:])
    if args.json:
        Path(args.json).write_text(json.dumps({"at": time.time(), "results": results}, indent=1) + "\n")
    missed = [r["name"] for r in results if not r["caught"]]
    summary = f"{len(results) - len(missed)} of {len(results)} canaries caught"
    print(f"\n{summary}; missed: {', '.join(missed)}" if missed else f"\n{summary}")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
