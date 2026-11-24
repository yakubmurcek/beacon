#!/usr/bin/env python3
# pyright: strict
"""The AI UI reviewer: finds wrong-looking things on every page that no rule names yet.

Draws every page of a throwaway hub (scripts/layout_check.py's) in its data states with scripts/ui_states.mjs:
empty, and full (many items, the longest text agents write) in the light English and dark Czech looks, plus the
moments a tap opens. Then one model call per page, state and look reads the phone and desktop pictures with the
page's text against the brief in .claude/agents/ui-reviewer.md, and answers findings as JSON.

    scripts/ui_review.py                       # everything, findings in $QUALITY_STATE/ui-review/findings.json
    scripts/ui_review.py --root ../planted     # review another checkout's pages (a planted bug)
    scripts/ui_review.py --reuse               # read the last run's pictures again (prompt tuning)

The rules in scripts/layout_check.mjs are the gate; this is the sensor. A real finding gets fixed and becomes a rule
(+ a canary), so it never needs a model again. Sonnet on the owner's subscription, at most CAP calls a run.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from layout_check import throwaway_hub  # scripts/ is on the path when this runs as a script

ROOT = Path(__file__).resolve().parent.parent
CLAUDE = os.environ.get('UI_REVIEW_CLAUDE') or os.path.expanduser('~/.local/bin/claude')
MODEL = os.environ.get('UI_REVIEW_MODEL', 'claude-sonnet-5-5')
CAP = int(os.environ.get('UI_REVIEW_CAP', '60'))  # model calls per run, each 2-3 pictures
TIMEOUT = 240
PARALLEL = 5
STATE_DIR = Path(os.environ.get('QUALITY_STATE', os.path.expanduser('~/.local/state/beacon-quality')))
# (state, demo.py arguments or None for an empty hub, ui_states.mjs flags)
STATES: list[tuple[str, list[str] | None, list[str]]] = [
    ('empty', None, ['--looks', 'paper-en']),
    ('full', ['--many', '--long'], ['--looks', 'paper-en,cyber-cs', '--moments', '--probe']),
]
Finding = dict[str, Any]


def brief() -> str:
    """The reviewer's brief: the agent file without its front matter, so the agent and this script share one text."""
    text = (ROOT / '.claude' / 'agents' / 'ui-reviewer.md').read_text()
    return text.split('---', 2)[2].strip()


def shoot(out: Path, root: Path, pages: list[str] | None) -> None:
    only = ['--pages', ','.join(pages)] if pages else []
    for state, seed, flags in STATES:
        with throwaway_hub(str(root), seed) as (hub, env):
            subprocess.run(['node', str(ROOT / 'scripts' / 'ui_states.mjs'), hub, str(out), state, *flags, *only],
                           env=env, cwd=str(ROOT), check=True)


def groups(out: Path) -> list[dict[str, Any]]:
    """One review per page, state and look: its phone and desktop pictures and texts together."""
    found: dict[tuple[str, str, str], dict[str, Any]] = {}
    for state_dir in sorted(p for p in out.iterdir() if p.is_dir()):
        probe = state_dir / 'dead-buttons.json'
        dead: dict[str, list[str]] = json.loads(probe.read_text()) if probe.exists() else {}
        for dump in sorted(state_dir.glob('*.json')):
            m = re.fullmatch(r'(.+)-(phone|desktop)-(\w+-\w+)', dump.stem)
            if not m:
                continue
            page, size, look = m.groups()
            g = found.setdefault((state_dir.name, page, look), {'state': state_dir.name, 'page': '/' + page, 'look': look,
                                                                'sizes': {}, 'dead': dead.get('/' + page.split('~')[0], [])})
            g['sizes'][size] = {'dir': state_dir, **json.loads(dump.read_text())}
    return list(found.values())


def message(g: dict[str, Any]) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    page, _, moment = g['page'].partition('~')
    head = (f"PAGE {page} · data state: {g['state']}" + (f" · moment: {moment} (after a tap)" if moment else '')
            + f" · look: {g['look']} (theme-language)")
    content.append({'type': 'text', 'text': head})
    for size in ('phone', 'desktop'):
        s = g['sizes'].get(size)
        if not s:
            continue
        for i, shot in enumerate(s['shots'], 1):
            content.append({'type': 'text', 'text': f'{size} screenshot {i} of {len(s["shots"])}'})
            data = base64.b64encode((s['dir'] / shot).read_bytes()).decode()
            content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': data}})
        content.append({'type': 'text', 'text': f"{size} page text:\n{s['text']}\n\n{size} controls:\n"
                        + '\n'.join(s['controls'])})
    if g['dead']:
        content.append({'type': 'text', 'text': 'Buttons a robot tapped on desktop that changed nothing: '
                        + '; '.join(x.replace('\n', ' ') for x in g['dead'])})
    content.append({'type': 'text', 'text': brief()})
    return {'type': 'user', 'message': {'role': 'user', 'content': content}}


def ask(g: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    proc = subprocess.run([CLAUDE, '-p', '--model', MODEL, '--setting-sources', '', '--tools', '', '--strict-mcp-config',
                           '--no-chrome', '--no-session-persistence', '--input-format', 'stream-json',
                           '--output-format', 'stream-json', '--verbose'],
                          input=json.dumps(message(g)) + '\n', capture_output=True, text=True, timeout=TIMEOUT,
                          cwd=tempfile.gettempdir(), env={**os.environ, 'TASKS_HOOKS': '0'}, check=False)
    result: dict[str, Any] = {}
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get('type') == 'result':
            result = ev
    text = str(result.get('result') or '')
    found: list[Finding] = []
    error = '' if result else (proc.stderr.strip()[-300:] or 'no answer')
    m = re.search(r'\{.*\}', text, re.S)
    if m:
        try:
            found = list(json.loads(m.group(0)).get('findings') or [])
        except ValueError:
            error = 'the answer was not JSON: ' + text[:200]
    for f in found:
        f.update(page=g['page'], state=g['state'], look=g['look'])
    return {'page': g['page'], 'state': g['state'], 'look': g['look'], 'findings': found, 'error': error,
            'cost_usd': result.get('total_cost_usd') or 0, 'seconds': round(time.time() - started)}


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or '').splitlines()[0])
    parser.add_argument('--root', default=str(ROOT), help='the checkout whose pages are reviewed')
    parser.add_argument('--out', default=str(STATE_DIR / 'ui-review'), help='pictures and findings go here')
    parser.add_argument('--reuse', action='store_true', help="review the last run's pictures again")
    parser.add_argument('--only', action='append', help='review only this page, e.g. /decisions (repeatable)')
    args = parser.parse_args()
    out = Path(args.out)
    shots = out / 'shots'
    if not args.reuse:
        shutil.rmtree(shots, ignore_errors=True)
        shoot(shots, Path(args.root).resolve(), args.only)
    todo = [g for g in groups(shots) if not args.only or g['page'].split('~')[0] in args.only]
    if len(todo) > CAP:
        print(f'{len(todo)} reviews, capped at {CAP}', file=sys.stderr)
        todo = todo[:CAP]
    with ThreadPoolExecutor(PARALLEL) as pool:
        reviews = list(pool.map(ask, todo))
    findings = [f for r in reviews for f in r['findings']]
    errors = [f"{r['page']} {r['state']} {r['look']}: {r['error']}" for r in reviews if r['error']]
    report: dict[str, Any] = {'at': time.time(), 'root': args.root, 'model': MODEL, 'calls': len(reviews),
              'cost_usd': round(sum(r['cost_usd'] for r in reviews), 3),
              'errors': errors,
              'findings': findings, 'reviews': reviews}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'findings.json').write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n')
    for f in findings:
        print(f"{f['page']:<22} {f['state']:<6} {f['look']:<9} {f.get('size', ''):<7} {f.get('kind', ''):<8} "
              f"{f.get('confidence', ''):<4} {f.get('what', '')}")
    print(f"\n{len(findings)} finding(s) from {len(reviews)} reviews, ${report['cost_usd']}; "
          f"{len(errors)} failed call(s); {out / 'findings.json'}")
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
