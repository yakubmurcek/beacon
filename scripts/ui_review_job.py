#!/usr/bin/env python3
# pyright: strict
"""The scheduled AI UI review (a systemd timer on the home server, every 30 min): reviews origin/main's pages once a
night, and after a push that changed web/, at most RUNS_A_DAY times a day; new findings go to one task as a checklist.

    scripts/ui_review_job.py                 # what the timer runs
    scripts/ui_review_job.py --force         # review now, whatever changed

A fresh detached worktree of origin/main is reviewed, so a half-edited checkout never is. State (the last reviewed
commit, today's runs, what was already posted) lives in $QUALITY_STATE/ui-review/job.json. The findings task is
$UI_REVIEW_TASK; without it they are only written to findings.json. Exit non-zero when the review itself failed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get('QUALITY_STATE', os.path.expanduser('~/.local/state/beacon-quality'))) / 'ui-review'
TREE = Path(os.environ.get('UI_REVIEW_TREE', str(ROOT.parent / '.beacon-ui-review')))
RUNS_A_DAY = int(os.environ.get('UI_REVIEW_RUNS_A_DAY', '3'))  # ~40 Sonnet calls each
NIGHT = range(2, 6)  # the hours the nightly run may happen in, whatever changed


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def due(job: dict[str, Any], head: str, now: datetime.datetime, force: bool) -> str:
    """Why to run now, or '' to skip."""
    today = now.date().isoformat()
    runs = job.get('runs', {}).get(today, 0)
    if force:
        return 'asked'
    if runs >= RUNS_A_DAY:
        return ''
    if now.hour in NIGHT and job.get('night') != today:
        return 'nightly'
    last = job.get('commit')
    if last and last != head and git('diff', '--name-only', last, head, '--', 'web/', 'scripts/ui_states.mjs'):
        return 'web/ changed'
    return ''


def key(f: dict[str, Any]) -> str:
    return '|'.join(str(f.get(k, '')) for k in ('page', 'state', 'look', 'kind', 'size'))


def post(task: str, head: str, why: str, new: list[dict[str, Any]], report: dict[str, Any]) -> None:
    lines = [f"🔎 UI review of {head} ({why}): {len(new)} new finding(s), {len(report['findings'])} in all, "
             f"{report['calls']} looks, ${report['cost_usd']}. Fix each and turn it into a rule in "
             'scripts/layout_check.mjs + a canary, or tick it as a false alarm.']
    lines += [f"- [ ] {f['page']} ({f['state']}, {f['look']}, {f.get('size')}) {f.get('kind')}: {f.get('what')}"
              for f in new]
    subprocess.run(['tasks', 'note', task, '\n'.join(lines)], check=False, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or '').splitlines()[0])
    parser.add_argument('--force', action='store_true', help='review now, whatever changed')
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    state_file = STATE / 'job.json'
    job: dict[str, Any] = json.loads(state_file.read_text()) if state_file.exists() else {}
    git('fetch', '-q', 'origin', 'main')
    head = git('rev-parse', '--short', 'origin/main')
    now = datetime.datetime.now()
    why = due(job, head, now, args.force)
    if not why:
        print(f'nothing to review at {head}')
        return 0
    if not TREE.exists():
        git('worktree', 'add', '-q', '--detach', str(TREE), 'origin/main')
    git('checkout', '-q', '--detach', '--force', 'origin/main', cwd=TREE)
    for name in ('.venv', 'node_modules'):  # the pinned tools of this checkout
        if (ROOT / name).exists() and not (TREE / name).exists():
            (TREE / name).symlink_to(ROOT / name)
    code = subprocess.run([sys.executable, str(TREE / 'scripts' / 'ui_review.py'), '--out', str(STATE)],
                          cwd=TREE, check=False).returncode
    today = now.date().isoformat()
    job['runs'] = {today: job.get('runs', {}).get(today, 0) + 1}
    job['commit'] = head
    if why == 'nightly':
        job['night'] = today
    report: dict[str, Any] = json.loads((STATE / 'findings.json').read_text())
    seen = set(job.get('posted', []))
    new = [f for f in report['findings'] if f.get('confidence') == 'high' and key(f) not in seen]
    task = os.environ.get('UI_REVIEW_TASK')
    if task and new:
        post(task, head, why, new, report)
    job['posted'] = sorted(seen | {key(f) for f in new})
    job['last'] = {'at': now.isoformat(timespec='seconds'), 'why': why, 'findings': len(report['findings']),
                   'new': len(new), 'cost_usd': report['cost_usd'], 'errors': report['errors']}
    state_file.write_text(json.dumps(job, indent=1) + '\n')
    print(f"{why}: {len(report['findings'])} finding(s), {len(new)} new and posted, ${report['cost_usd']}")
    return code


if __name__ == '__main__':
    sys.exit(main())
