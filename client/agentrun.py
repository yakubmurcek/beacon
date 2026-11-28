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

Every headless run also shows in his Herdr app (Mac and phone), in the space "Beacon agents" next to the live
chats: `show` opens a herdr-core terminal that follows the run's transcript (`agentrun.py watch`). The run itself
stays in its own systemd unit (jail, timeout, /machines and /ai keep seeing it); the tab only watches.

Standard library only. Imported by the launchers next to this file.
"""
import contextlib
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, cast

J = dict[str, Any]  # one task or one check as JSON gives it

USD = float(os.environ.get('TASK_AGENT_RUN_USD', '40'))  # one run's ceiling, API prices (his call 2026-11-25: 10 too low)
# Every agent Beacon starts runs at this effort unless a repo's conf pins its own (his call 2026-11-28: was the
# CLI's default, high).
EFFORT = os.environ.get('TASK_AGENT_EFFORT', 'medium')

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


def flags(trigger: str, printed: bool = True, effort: str | None = None) -> list[str]:
    """The `claude` flags the policy gives this trigger (permission mode, settings, effort, rules, caps).
    `printed`: the run is `claude -p` (a live chat is not, and takes no budget). `effort`: a repo conf's own pin,
    else EFFORT."""
    p = POLICY[trigger]
    out: list[str] = ['--permission-mode', p['mode'], '--effort', effort or EFFORT]
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


# ------------------------------------------------------------------ the Herdr tab of a headless run

HC = os.environ.get('BEACON_AGENTS_HC') or shutil.which('hc') or '/usr/local/bin/hc'
SPACE = 'beacon-agents'  # the Herdr space of every Beacon agent (client/beacon-agent's live chats too)
WATCH_MAX = 6 * 3600  # a tab never outlives this
LINGER = 10 * 60  # an ended run's tab stays this long, so its last lines can be read
# A new Herdr space takes its name from its first terminal's folder: a viewer that opens it names it right.
VIEW_DIR = os.path.join(os.environ.get('BEACON_AGENTS_DIR', '/var/lib/beacon-agents'), 'Beacon agents')


def view_name(sid: str) -> str:
    """The viewer's terminal, one per run: never `beacon-<sid8>`, the live chat's own name that answers are typed
    into, and never the name of an earlier run's tab that still lingers (two answers resume one session)."""
    return f'view-{sid[:8]}-{int(time.time() * 1000) % 10**8:08d}'


def marker(name: str) -> str:
    return os.path.join(os.path.dirname(VIEW_DIR), '.views', name)


def show(sid: str, title: str, trigger: str, task: int | str | None, home: str) -> str:
    """A Herdr tab following this run from now on (a resumed session's history is not replayed); returns its name
    for `ended`. Never in the way: hc missing, down or slow only means no tab ('')."""
    if os.environ.get('BEACON_AGENT_VIEW', '1') != '1' or not os.path.exists(HC):
        return ''
    name = view_name(sid)
    try:
        os.makedirs(VIEW_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(marker(name)), exist_ok=True)
    except OSError:
        return ''
    cmd = [HC, 'run', '--bg', '--json', '--rm', '--name', name, '--cwd', VIEW_DIR, '--no-parent', '--outlive-parent',
           '--label', f'herdr.space={SPACE}', '--label', f'herdr.tab={SPACE}-{name}',
           '--label', f'beacon.trigger={trigger}', '--label', f'beacon.task={task or ""}',
           '--', sys.executable, os.path.realpath(__file__), 'watch', sid, home, str(os.getpid()),
           f"{title} · {trigger}" + (f' · task {task}' if task else ''), f'{time.time() - 5:.0f}', marker(name)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    return name if r.returncode == 0 else ''


def ended(name: str) -> None:
    """The run behind this tab is over: the tab says so (and goes after LINGER), even if its launcher goes on."""
    if name:
        with contextlib.suppress(OSError), open(marker(name), 'w'):
            pass  # an empty mark


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lines_of(entry: J) -> list[str]:
    """What a person reads of one transcript line: the agent's words and the tools it reaches for."""
    msg: J = entry.get('message') or {}
    content: Any = msg.get('content')
    if entry.get('type') != 'assistant' or not isinstance(content, list):
        return []
    out: list[str] = []
    for c in cast(list[J], content):
        if c.get('type') == 'text' and str(c.get('text') or '').strip():
            out.append('💬 ' + str(c['text']).strip()[:600])
        elif c.get('type') == 'tool_use':
            inp: J = c.get('input') or {}
            what = inp.get('command') or inp.get('file_path') or inp.get('pattern') or inp.get('description') or ''
            out.append(f"🔧 {c.get('name')}: {' '.join(str(what).split())[:160]}")
    return out


def at(entry: J) -> float:
    """A transcript line's time (epoch s); 0 when it has none."""
    try:
        return datetime.datetime.fromisoformat(str(entry.get('timestamp')).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def watch(sid: str, home: str, pid: int, title: str, since: float = 0.0, done: str = '') -> int:
    """Inside the Herdr tab: the run's transcript as it grows (lines from `since` on: a resumed or forked session's
    history stays out), until the run is marked ended or the launcher waiting on it is gone."""
    print(f'\033]0;{title[:60]}\007🤖 {title}', flush=True)  # the terminal title
    began, path, pos = time.time(), None, 0
    while time.time() - began < WATCH_MAX:
        if path is None:
            hits = glob.glob(os.path.join(home, 'projects', '*', sid + '.jsonl'))
            path = hits[0] if hits else None
        if path:
            try:
                with open(path, 'rb') as f:
                    f.seek(pos)
                    for raw in f:
                        if not raw.endswith(b'\n'):
                            break  # a line still being written: the next look takes it whole
                        pos += len(raw)
                        try:
                            got: Any = json.loads(raw)
                        except ValueError:
                            continue
                        entry = cast(J, got) if isinstance(got, dict) else {}
                        if not entry or (since and at(entry) and at(entry) < since):
                            continue
                        for line in lines_of(entry):
                            print(line, flush=True)
            except OSError:
                path = None
        if (done and os.path.exists(done)) or not alive(pid):
            print('■ The run ended. Its result is on the task in Beacon.', flush=True)
            with contextlib.suppress(OSError):
                if done:
                    os.remove(done)
            time.sleep(LINGER)
            return 0
        time.sleep(2)
    return 0


if __name__ == '__main__' and len(sys.argv) == 8 and sys.argv[1] == 'watch':
    sys.exit(watch(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5], float(sys.argv[6]), sys.argv[7]))
