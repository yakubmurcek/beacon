# pyright: strict
"""agentrun: the one policy every Beacon agent starts under (task 2043).

Four launchers start Claude Code on his behalf: task-agent (a task handed to it, a move he tapped on a page, a
jailed run in one of his repos, the check after it), answer-dispatcher (his answer on Decisions resumes or starts
a run) and beacon-agent (the live chat in a herdr-core terminal). Until 2026-11-25 each wrote its own `claude`
flags, and they drifted: a brief that pointed at deleted skills, jailed runs that never saw his core rules,
answer runs with no spending cap. Here is the one table of what each trigger gets, and the one builder every
launcher takes its flags from (tests/test_agentrun.py proves each launcher does).

Merging is never an agent's own call: `merge_gate` passes a PR only when its checks are green and a fresh
reviewer, a separate model run that sees only the diff and the task's done-test, says it holds (his studio rule,
2026-11-25: merge only after an independent review, never your own unreviewed PR).

Standard library only. Imported by the launchers next to this file.
"""
import json
import os
import re
import subprocess
from typing import Any, cast

J = dict[str, Any]  # one task or one check as JSON gives it

USD = float(os.environ.get('TASK_AGENT_RUN_USD', '40'))  # one run's ceiling, API prices (his call 2026-11-25: 10 too low)

# What each trigger gets. mode: --permission-mode. settings: whose settings load ('user' = his full setup with the
# studio rules and skills; 'project' = only the repo's own; 'none' = nothing). rules: text appended to the system
# prompt. budget/turns: hard caps; --max-budget-usd only works with --print, so a live chat has none: it is held by
# beacon-agent's cap of live agents and its idle reap instead.
POLICY: dict[str, J] = {
    'task': {'mode': 'auto', 'settings': 'user', 'budget': USD},             # task-agent: a task handed to it
    'move': {'mode': 'auto', 'settings': 'user', 'budget': USD},             # task-agent-moves: a move he tapped
    'repo': {'mode': 'auto', 'settings': 'project', 'rules': 'core', 'budget': USD, 'chrome': False},  # jailed run
    'check': {'mode': 'auto', 'settings': 'project', 'budget': USD, 'chrome': False},  # the check after a jailed run
    # answer runs keep bypass for now: the auto classifier refuses an agent's push to main (2026-11-24), which
    # answer runs in his notes repos need; moving them to auto waits for a trial of one answer flow under it.
    'answer': {'mode': 'bypassPermissions', 'settings': 'user', 'budget': USD, 'turns': 80},
    'live': {'mode': 'bypassPermissions', 'settings': 'user'},
    'review': {'mode': 'default', 'settings': 'none', 'budget': 2.0, 'model': 'sonnet', 'tools': '', 'chrome': False},
}


def core_rules() -> str:
    """His global core rules (~/.claude/CLAUDE.md), HTML comments stripped: a run that skips his user settings
    would never see them otherwise. Empty when the file is missing."""
    try:
        with open(os.path.join(os.path.expanduser('~'), '.claude', 'CLAUDE.md'), encoding='utf-8') as f:
            return re.sub(r'<!--.*?-->', '', f.read(), flags=re.S).strip()
    except OSError:
        return ''


def flags(trigger: str, printed: bool = True) -> list[str]:
    """The `claude` flags the policy gives this trigger (permission mode, settings, rules, caps). `printed`: the
    run is `claude -p` (a live chat is not, and takes no budget)."""
    p = POLICY[trigger]
    out: list[str] = ['--permission-mode', p['mode']]
    if p['settings'] != 'user':
        out += ['--setting-sources', '' if p['settings'] == 'none' else p['settings'], '--strict-mcp-config']
    if p.get('chrome') is False:
        out += ['--no-chrome']
    if 'tools' in p:
        out += ['--tools', p['tools']]
    if p.get('model'):
        out += ['--model', p['model']]
    if p.get('rules') == 'core' and core_rules():
        out += ['--append-system-prompt', core_rules()]
    if printed and p.get('budget'):
        out += ['--max-budget-usd', f"{p['budget']:g}"]
    if printed and p.get('turns'):
        out += ['--max-turns', str(p['turns'])]
    return out


# ------------------------------------------------------------------ the merge gate

REVIEW_SCHEMA = json.dumps({'type': 'object', 'required': ['pass', 'findings'], 'properties': {
    'pass': {'type': 'boolean', 'description': 'true only when the change does what the task asks and nothing in it is a bug'},
    'findings': {'type': 'array', 'items': {'type': 'string'}, 'description': 'each real problem, one line'}}})
DIFF_MAX = 60000


def checks(pr: str, gh: str, env: dict[str, str]) -> tuple[str, str]:
    """(state, why): 'green' when every check passed or was skipped (a repo with no CI has nothing to be red),
    'pending' while one still runs, 'red' otherwise. `gh pr view --json statusCheckRollup`: the box's gh (2.46)
    has no `gh pr checks --json`."""
    r = subprocess.run([gh, 'pr', 'view', pr, '--json', 'statusCheckRollup'], env=env, capture_output=True, text=True,
                       timeout=60, check=False)
    try:
        got: Any = json.loads(r.stdout or 'null')
        rollup: Any = cast(J, got).get('statusCheckRollup') if isinstance(got, dict) else None
    except ValueError:
        rollup = None
    if r.returncode or not isinstance(rollup, list):
        return 'red', f"its checks couldn't be read ({(r.stderr or r.stdout).strip()[:120]})"
    running: list[str] = []
    bad: list[str] = []
    items: list[J] = [c for c in cast(list[Any], rollup) if isinstance(c, dict)]
    for c in items:
        name = str(c.get('name') or c.get('context') or '?')
        if c.get('__typename') == 'StatusContext':
            state = c.get('state')
            if state in ('PENDING', 'EXPECTED'):
                running.append(name)
            elif state != 'SUCCESS':
                bad.append(name)
        elif c.get('status') != 'COMPLETED':
            running.append(name)
        elif c.get('conclusion') not in ('SUCCESS', 'SKIPPED', 'NEUTRAL'):
            bad.append(name)
    if bad:
        return 'red', f"checks not green: {', '.join(bad)[:160]}"
    if running:
        return 'pending', f"checks still running: {', '.join(running)[:160]}"
    return 'green', 'checks green' if items else 'no checks on this repo'


def review(pr: str, task: J, gh: str, claude: str, env: dict[str, str]) -> tuple[bool, list[str]]:
    """(passed, findings): a fresh reviewer that never saw the run, only the diff and the task's goal."""
    d = subprocess.run([gh, 'pr', 'diff', pr], env=env, capture_output=True, text=True, timeout=60, check=False)
    if d.returncode or not d.stdout.strip():
        return False, [f"the diff couldn't be read ({(d.stderr or '').strip()[:120]})"]
    diff = d.stdout if len(d.stdout) <= DIFF_MAX else d.stdout[:DIFF_MAX] + '\n… (diff cut here)'
    goal = (task.get('body') or '').strip()[:4000]
    prompt = (f"You review a pull request before it merges. You did not write it. Task: {task.get('title')}\n\n"
              f"What the task asks (its goal and done-test):\n{goal or '(no body)'}\n\nThe diff:\n{diff}\n\n"
              "Pass it only if the change does what the task asks, breaks nothing you can see, and holds no "
              "secret or personal data that should not be in the repo. List each real problem as one finding.")
    env = {k: v for k, v in env.items() if k != 'CLAUDE_CODE_SESSION_ID'}
    try:
        r = subprocess.run([claude, '-p', *flags('review'), '--output-format', 'json', '--json-schema', REVIEW_SCHEMA,
                            '--name', f"Review: {task.get('title') or pr}"[:60]], input=prompt, env=env,
                           capture_output=True, text=True, timeout=300, check=False)  # the gate fits the dispatcher's 600 s
        out: J = json.loads(r.stdout or '{}')
        verdict: J = out.get('structured_output') or json.loads(out.get('result') or '{}')
    except (ValueError, OSError, subprocess.SubprocessError, TypeError) as e:
        return False, [f'the review did not run ({str(e)[:120]})']
    findings: list[Any] = verdict.get('findings') or []
    return bool(verdict.get('pass')), [str(f) for f in findings]


def merge_gate(pr: str, task: J, gh: str, claude: str, env: dict[str, str]) -> tuple[bool | None, str]:
    """(ok, why) for merging this PR: checks green, then a fresh review passes. The verdict goes on the PR.
    ok is None while checks still run: ask again later, nothing is wrong yet."""
    state, why = checks(pr, gh, env)
    if state != 'green':
        return (None if state == 'pending' else False), why
    passed, findings = review(pr, task, gh, claude, env)
    body = ('Independent review: passed.' if passed else 'Independent review: not passed.') + \
        ''.join(f'\n- {f}' for f in findings)
    subprocess.run([gh, 'pr', 'comment', pr, '--body', body], env=env, capture_output=True, text=True, timeout=60,
                   check=False)
    if not passed:
        return False, 'the independent review found: ' + ('; '.join(findings)[:300] or 'no reason given')
    return True, why + ', independent review passed'
