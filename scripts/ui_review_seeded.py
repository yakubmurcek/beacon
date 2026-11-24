#!/usr/bin/env python3
# pyright: strict
"""Does the AI UI reviewer catch bugs no rule names yet? Plant each known bug, review, score.

The bugs are patches to web/ written by someone who never saw the reviewer's brief, with a key saying what each
breaks and where (key.json: [{"patch", "kind", "page", "what"}]). Each patch goes onto a throwaway worktree of HEAD,
scripts/ui_review.py reviews that page there, and a bug counts as caught when a finding on its page has one of its
kinds ("kinds": the reviewer's words for it).
Every finding is printed beside the key, so a person can check the matching once.

    scripts/ui_review_seeded.py /tmp/planted/key.json [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def review(bug: dict[str, Any], out: Path) -> list[dict[str, Any]]:
    tree = Path(tempfile.mkdtemp(prefix='.planted-', dir=ROOT.parent))
    try:
        subprocess.run(['git', 'worktree', 'add', '-q', '--detach', str(tree), 'HEAD'], cwd=ROOT, check=True)
        subprocess.run(['git', 'apply', str(Path(bug['patch']).resolve())], cwd=tree, check=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts' / 'ui_review.py'), '--root', str(tree), '--out', str(out),
                        '--only', bug['page']], cwd=ROOT, check=False)
        report = json.loads((out / 'findings.json').read_text())
        return list(report['findings'])
    finally:
        subprocess.run(['git', 'worktree', 'remove', '--force', str(tree)], cwd=ROOT, capture_output=True, check=False)
        shutil.rmtree(tree, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or '').splitlines()[0])
    parser.add_argument('key', help='the planted bugs: key.json')
    parser.add_argument('--json', help='write the scores here')
    args = parser.parse_args()
    bugs: list[dict[str, Any]] = json.loads(Path(args.key).read_text())
    work = Path(tempfile.mkdtemp(prefix='ui-seeded-'))
    results: list[dict[str, Any]] = []
    for n, bug in enumerate(bugs, 1):
        findings = review(bug, work / str(n))
        kinds = bug.get('kinds', [bug['kind']])
        hits = [f for f in findings if f.get('page', '').split('~')[0] == bug['page'] and f.get('kind') in kinds]
        results.append({**bug, 'caught': bool(hits), 'hits': hits, 'findings': findings})
        print(f"\n{'✓' if hits else '✗'} {n}. {bug['kind']} on {bug['page']}: {bug['what']}")
        for f in findings:
            mark = '→' if f in hits else ' '
            print(f"   {mark} [{f.get('kind')}/{f.get('confidence')}] {f.get('state')} {f.get('look')} "
                  f"{f.get('size')}: {f.get('what')}")
    caught = sum(r['caught'] for r in results)
    print(f'\n{caught} of {len(results)} planted bugs caught')
    if args.json:
        Path(args.json).write_text(json.dumps({'caught': caught, 'of': len(results), 'results': results},
                                              indent=1, ensure_ascii=False) + '\n')
    shutil.rmtree(work, ignore_errors=True)
    return 0 if caught >= len(results) - 1 else 1


if __name__ == '__main__':
    sys.exit(main())
