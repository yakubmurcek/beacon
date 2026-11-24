"""Live state of every agent session on this machine: who is working, who waits on you.

collect.py calls snapshot() on every run and sends the result with its events, so the
hub always holds what each machine looked like at its last run. Standard library only,
read-only on every agent's files, same as the rest of the collector.

A session is one of four states:
  working  mid-turn right now
  waiting  finished its turn; the human is the blocker
  done     finished its turn and said nothing is left (its last line is "✅ All done."),
           or opened and never given a prompt: nothing has been asked, so nothing waits
  idle     still open, but mid-turn and quiet past WORKING_STALL (stalled)

State comes from turn markers where an agent writes them (Claude Code, Codex, Hermes),
not from timing: a long tool call leaves a log quiet for minutes while the agent is busy.
Agents with no marker (OpenCode, Grok, Antigravity, Muse) fall back to recency.

Each session also says what it is doing ("Editing store.py", "$ npm test") and the last
thing a human asked it, so a viewer can tell the sessions apart at a glance.
"""
import glob
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime

from collect import HOME, claude_prompt_text, codex_request, project_of, redact

log = logging.getLogger('live')

LIVE_WITHIN = 30 * 60   # a log written this recently is a live session even with no process found
STALE_AFTER = 24 * 3600  # past this, not even a running process keeps a session listed
WORKING_STALL = 10 * 60  # mid-turn and quiet this long: stalled, not working
RECENT_ACTIVE = 45       # no turn markers: written this recently counts as working
HELPER_ACTIVE = 90       # a subagent log written this recently is a helper at work
HELPER_BUSY = 180        # with the main turn over, a helper log written this recently still means work
TAIL_BYTES = 256 * 1024  # only the tail of a log is read; Claude transcripts reach tens of MB
TEXT_MAX = 140


def short(text):
    """One redacted line, at most TEXT_MAX characters."""
    if not text:
        return None
    line = next((x.strip() for x in str(text).splitlines() if x.strip()), '')
    line = redact(line)
    return (line[:TEXT_MAX - 1] + '…') if len(line) > TEXT_MAX else (line or None)


def read_tail(path, size):
    """Parsed JSON lines from the last TAIL_BYTES of a file."""
    start = max(0, size - TAIL_BYTES)
    with open(path, 'rb') as f:
        f.seek(start)
        data = f.read()
    lines = data.split(b'\n')
    if start > 0:
        lines = lines[1:]  # the first line starts mid-record
    out = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue  # a half-written line
    return out


# The global agent rules end every session with "✅ All done." or "⏳ Needs you: …"; an
# older wording was "✅ You can close this session". Only the reply's last line counts, so
# a ✅ bullet above a "⏳ Needs you" list does not.
DONE_LINE = re.compile(r'^\s*✅\s*\**\s*(all done|you can close this session)', re.I)


def says_done(text):
    """Does this final reply close the session (nothing left for the human)?"""
    lines = [x for x in str(text or '').splitlines() if x.strip()]
    return bool(lines) and bool(DONE_LINE.match(lines[-1]))


def derive_state(idle, turn_ended, done=False):
    if turn_ended:
        return 'done' if done else 'waiting'
    return 'working' if idle < WORKING_STALL else 'idle'


# ---------------------------------------------------------------- processes

def list_processes():
    """[{pid, ppid, started, args}] for every process; [] when the OS won't say."""
    if sys.platform == 'win32':
        return list_processes_windows()
    try:
        out = subprocess.run(['ps', 'axo', 'pid=,ppid=,lstart=,args='], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    procs = []
    for line in out.splitlines():
        # lstart is a fixed-width ctime string: "Fri Jul 31 09:26:04 2026"
        m = re.match(r'^\s*(\d+)\s+(\d+)\s+(.{24})\s+(.*)$', line)
        if not m:
            continue
        try:
            started = time.mktime(time.strptime(m.group(3).strip(), '%a %b %d %H:%M:%S %Y'))
        except ValueError:
            started = None
        procs.append({'pid': int(m.group(1)), 'ppid': int(m.group(2)), 'started': started, 'args': m.group(4)})
    return procs


def list_processes_windows():
    # CIM gives full command lines, which the --session-id match needs. CREATE_NO_WINDOW keeps
    # the collector's scheduled run from flashing a console on the desktop.
    cmd = ('Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,CreationDate'
           ' | ConvertTo-Json -Compress -Depth 2')
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', cmd],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        rows = json.loads(out or '[]')
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if isinstance(rows, dict):
        rows = [rows]
    procs = []
    for r in rows:
        if not isinstance(r, dict) or not r.get('CommandLine'):
            continue
        m = re.search(r'\d{10,}', str(r.get('CreationDate')))  # "/Date(1690787164000)/"
        procs.append({'pid': int(r.get('ProcessId') or 0), 'ppid': int(r.get('ParentProcessId') or 0),
                      'started': int(m.group(0)) / 1000 if m else None, 'args': str(r['CommandLine'])})
    return procs


def process_cwd(pid):
    """Working directory of a process, or None (always None on Windows: no cheap way)."""
    try:
        if sys.platform.startswith('linux'):
            return os.readlink(f'/proc/{pid}/cwd')
        if sys.platform == 'darwin':
            out = subprocess.run(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], capture_output=True,
                                 text=True, timeout=4).stdout
            return next((x[1:] for x in out.splitlines() if x.startswith('n')), None)
    except (OSError, subprocess.SubprocessError):
        pass
    return None


# Which processes are interactive Claude Code sessions? The program must be Claude Code
# itself (`claude`, `node …/claude-code/cli.js`) or the program of a command a shell wrapper
# runs (`pwsh -NoExit -Command claude --resume <id>` on Windows). One-shot subcommands,
# servers and headless `-p` runs never wait on a human and do not count. Ported from
# lighthouse's claude-proc.mjs (2026-09-26 fix: a shell running `claude auth login` showed
# as two agents waiting for hours).
HEADLESS_FLAGS = {'-p', '--print', '-v', '--version', '-h', '--help', '--chrome-native-host', '--bg-spare'}
SHELLS = re.compile(r'^(pwsh|powershell|cmd|bash|sh|zsh)(\.exe)?$', re.I)
UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
# `ps` strips quotes, so `claude "install deps"` arrives as `claude install deps`: each
# subcommand only matches its real shape, so a prompt starting with one still counts.
NOT_A_SESSION = {
    'auth': lambda a: bool(a) and a[0] in ('login', 'logout', 'status'),
    'mcp': lambda a: bool(a) and a[0] in ('add', 'add-json', 'add-from-claude-desktop', 'get', 'list', 'remove',
                                          'reset-project-choices', 'serve'),
    'plugin': lambda a: bool(a) and a[0] in ('install', 'i', 'uninstall', 'list', 'enable', 'disable',
                                             'marketplace', 'validate', 'update'),
    'config': lambda a: bool(a) and a[0] in ('get', 'set', 'list', 'ls', 'add', 'remove'),
    'install': lambda a: len(a) <= 1,
    'doctor': lambda a: not a,
    'update': lambda a: not a,
    'upgrade': lambda a: not a,
    'setup-token': lambda a: not a,
    'migrate-installer': lambda a: not a,
    'remote-control': lambda a: True,
    # Background-session plumbing (2.1.x): the daemon, its pty hosts and warm spares, the
    # Chrome bridge and the `claude agents` picker. The sessions they run are found from
    # their transcripts and job state instead.
    'daemon': lambda a: True,
    'bg-pty-host': lambda a: True,
    'bg-spare': lambda a: True,
    'agents': lambda a: True,
}


def argv(cmd):
    """Split a command line into words, honouring double and single quotes."""
    # An empty quoted argument ("") is a word too, not a gap.
    return [next(g for g in m.groups() if g is not None)
            for m in re.finditer(r'"([^"]*)"|\'([^\']*)\'|(\S+)', cmd)]


def _base(p):
    return re.split(r'[\\/]', p)[-1]


def _is_claude_exe(w):
    # Case-sensitive on purpose: the desktop app is `Claude` and its helpers' unquoted
    # paths ("…/MacOS/Claude Helper") would otherwise look like the CLI.
    return re.match(r'^claude(\.exe|\.cmd)?$', _base(w)) is not None


def _shell_commands(words):
    i = 1
    while i < len(words) and words[i].startswith('-'):
        i += 1
    script = re.sub(r'(;|&&|\|\||\|)', ' ; ', ' '.join(words[i:]))
    cmds = [[]]
    for w in argv(script):
        if w == ';':
            cmds.append([])
        else:
            cmds[-1].append(w)
    return cmds


def _claude_args(words):
    if not words:
        return None
    exe = _base(words[0])
    if _is_claude_exe(words[0]):
        return words[1:], False
    if re.match(r'^node(\.exe)?$', exe) and len(words) > 1 and re.search(r'claude-code[\\/]cli\.m?js$', words[1]):
        return words[2:], False
    if SHELLS.match(exe):
        for cmd in _shell_commands(words):
            start = next((i for i, w in enumerate(cmd) if not re.match(r'^[A-Za-z_]\w*=', w)), None)
            if start is not None and _is_claude_exe(cmd[start]):
                return cmd[start + 1:], True
    return None


def claude_session(cmd):
    """{'session_id', 'wrapper'} for an interactive Claude Code command line, else None."""
    found = _claude_args(argv(cmd))
    if not found:
        return None
    rest, wrapper = found
    if any(w in HEADLESS_FLAGS for w in rest):
        return None
    sid = None
    subcommand_checked = False
    i = 0
    while i < len(rest):
        w = rest[i]
        # The desktop app passes `--resume=<id>`; a terminal usually `--resume <id>`.
        flag, eq, value = w.partition('=')
        if eq and flag in ('--session-id', '--resume'):
            if UUID_RE.match(value):
                sid = value
            i += 1
            continue
        if w in ('--session-id', '--resume', '-r'):
            if i + 1 < len(rest) and UUID_RE.match(rest[i + 1]):
                sid = rest[i + 1]
            i += 2
            continue
        # Only the first plain word can be a subcommand. Later ones are flag values or the
        # prompt, and the scan goes on past them: `--output-format stream-json … --resume=<id>`.
        if not w.startswith('-') and not subcommand_checked:
            subcommand_checked = True
            check = NOT_A_SESSION.get(w)
            if check and check([x for x in rest[i + 1:] if not x.startswith('-')]):
                return None
        i += 1
    return {'session_id': sid, 'wrapper': wrapper}


def claude_processes(procs):
    """Interactive sessions among processes. A shell wrapper whose child is also a listed
    session is dropped, so `bash -c claude` and the `claude` it started count once."""
    found = []
    for p in procs:
        s = claude_session(p['args'])
        if s:
            found.append(dict(p, **s))
    parents = {p.get('ppid') for p in found}
    return [p for p in found if not (p['wrapper'] and p['pid'] in parents)]


# ---------------------------------------------------------------- what it is doing

def _path_name(p):
    return _base(str(p or '').rstrip('/\\')) or None


def describe_tool(name, inp):
    """A tool call as one short line: 'Editing store.py', '$ npm test'."""
    return short(_describe_tool(name, inp))


def _describe_shell(inp):
    # Claude Code's shell calls carry the agent's own plain words for what the command does
    if isinstance(inp.get('description'), str) and inp['description'].strip():
        return inp['description'].strip()
    cmd = inp.get('command') or inp.get('cmd') or ''
    if isinstance(cmd, list):
        cmd = ' '.join(str(x) for x in cmd)
        cmd = re.sub(r'^(bash|zsh|sh) -lc ', '', cmd)
    return '$ ' + str(cmd) if cmd else 'Running a command'


def _describe_tool(name, inp):
    inp = inp if isinstance(inp, dict) else {}
    name = str(name or '')
    if name in ('Bash', 'shell', 'exec_command', 'local_shell', 'run_terminal_cmd'):
        return _describe_shell(inp)
    if name in ('Edit', 'MultiEdit', 'NotebookEdit', 'str_replace', 'edit'):
        return f'Editing {_path_name(inp.get("file_path") or inp.get("path") or inp.get("notebook_path")) or "a file"}'
    if name in ('Write', 'write', 'create_file'):
        return f'Writing {_path_name(inp.get("file_path") or inp.get("path")) or "a file"}'
    if name in ('Read', 'read', 'read_file', 'view'):
        return f'Reading {_path_name(inp.get("file_path") or inp.get("path")) or "a file"}'
    if name in ('Grep', 'grep', 'search'):
        return f'Searching for {inp.get("pattern") or inp.get("query") or "text"}'
    if name in ('Glob', 'glob', 'list_dir', 'LS'):
        return f'Looking for {inp.get("pattern") or inp.get("path") or "files"}'
    if name == 'WebFetch':
        return f'Reading {urllib.parse.urlparse(str(inp.get("url") or "")).netloc or "a web page"}'
    if name == 'WebSearch':
        return f'Searching the web: {inp.get("query") or ""}'.rstrip(': ')
    if name in ('Agent', 'Task'):
        return f'Delegating: {inp.get("description") or inp.get("subagent_type") or "a subtask"}'
    if name in ('TodoWrite', 'TaskCreate', 'TaskUpdate'):
        return 'Planning its next steps'
    if name in ('apply_patch',):
        return 'Editing files'
    if name.startswith('mcp__'):
        parts = name.split('__')
        server = re.sub(r'^(claude_ai_|plugin_[^_]+_)', '', parts[1]) if len(parts) > 2 else ''
        return f'Using {parts[-1].replace("_", " ")}' + (f' ({server})' if server else '')
    return f'Using {name}' if name else None


# ---------------------------------------------------------------- Claude Code

TURN_END = {'turn_duration', 'stop_hook_summary', 'away_summary'}
STEPS = 8  # a live session's latest steps kept for its task page


def stamp(o):
    """Epoch seconds of a transcript row's ISO timestamp, or None."""
    t = o.get('timestamp')
    if not isinstance(t, str):
        return None
    try:
        return datetime.fromisoformat(t.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def add_step(r, at, said):
    """Keep this turn's last few steps, so a run nobody watches can be followed on its task page."""
    if not r['doing']:
        return
    step = {'at': at, 'text': r['doing']}
    if said:
        step['said'] = True  # the agent's own words, not a tool call
    r['steps'] = (r['steps'] + [step])[-STEPS:]


def parse_claude(path, size):
    r = {'title': None, 'cwd': None, 'branch': None, 'model': None, 'turn_ended': False,
         'doing': None, 'prompt': None, 'at': None, 'steps': []}
    custom = None
    reply = None  # the last text the agent wrote this turn
    for o in read_tail(path, size):
        kind = o.get('type')
        # Bookkeeping rows (mode, cost-state, last-prompt) carry no timestamp; Claude Code
        # writes them when a session is closed or archived, which must not read as activity.
        r['at'] = stamp(o) or r['at']
        if kind == 'ai-title' and o.get('aiTitle'):
            r['title'] = o['aiTitle']
        if kind == 'custom-title' and o.get('customTitle'):
            custom = o['customTitle']  # a name the user gave it beats the generated one
        if o.get('cwd'):
            r['cwd'] = o['cwd']
        if o.get('gitBranch'):
            r['branch'] = o['gitBranch']
        msg = o.get('message') or {}
        if kind == 'assistant':
            if msg.get('model') and msg['model'] != '<synthetic>':
                r['model'] = msg['model']
            for b in msg.get('content') or []:
                if not isinstance(b, dict):
                    continue
                said = b.get('type') == 'text'
                if b.get('type') == 'tool_use':
                    r['doing'] = describe_tool(b.get('name'), b.get('input'))
                elif said and b.get('text', '').strip():
                    r['doing'] = short(b['text'])
                    reply = b['text']
                else:
                    continue
                add_step(r, stamp(o), said)
        if kind == 'user' and interrupted(o):
            # Esc mid-turn writes no turn-end marker; the session is back with the human.
            r['turn_ended'] = True
            r['doing'] = 'Stopped by you'
            reply = None
        elif kind == 'user' and not o.get('isMeta'):
            text = claude_prompt_text(o)
            if text:
                r['prompt'] = short(text)
                r['turn_ended'] = False
                r['doing'] = 'Reading your message'
                r['steps'] = []
                reply = None
        if kind == 'system' and o.get('subtype') in TURN_END:
            r['turn_ended'] = True
    r['title'] = custom or r['title']
    # Nothing asked and nothing said: a session opened and never used is not waiting on anyone.
    r['done'] = says_done(reply) or (r['prompt'] is None and r['doing'] is None)
    return r


def interrupted(o):
    content = (o.get('message') or {}).get('content')
    texts = [content] if isinstance(content, str) else [
        b.get('text', '') for b in content or [] if isinstance(b, dict) and b.get('type') == 'text']
    return any(str(t).startswith('[Request interrupted') for t in texts)


def claude_slug(cwd):
    """Claude Code names a project directory after its cwd with every other character as '-'."""
    return re.sub(r'[^A-Za-z0-9]', '-', cwd)


JOB_OVER = {'done', 'stopped', 'failed'}


def claude_jobs(jobs_dir):
    """Background sessions (claude --bg, the desktop app) keep a state.json per job: its
    session id, a name, and whether it is still going. A finished job is not waiting on
    anyone even if its transcript was written a minute ago; an open one is live even when
    no process names it (they run inside the daemon)."""
    jobs = {}
    for path in glob.glob(os.path.join(jobs_dir, '*', 'state.json')):
        try:
            with open(path, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        for key in ('sessionId', 'resumeSessionId'):
            if d.get(key):
                jobs[d[key]] = {'over': d.get('state') in JOB_OVER, 'name': d.get('name')}
    return jobs


def claude_registry(sessions_dir, procs):
    """Claude Code (2.1.x) keeps a file per running session in ~/.claude/sessions/<pid>.json:
    its session id, cwd, name (and whether the user gave it), and status, `busy` while a turn
    runs and `idle` once it is back with the human. Every kind is listed: terminal, desktop
    app, background job, remote control. It is the one reliable answer to "which sessions
    are open"; a command line often carries no session id (a desktop-app session started
    fresh), and a quiet transcript says nothing about whether its process still runs.

    A file whose process is gone is left behind by a crash, so the pid must be a live
    process that is Claude Code. With no process list at all (ps failed), files are trusted."""
    alive = {p['pid'] for p in procs if 'claude' in p['args'].lower()} if procs else None
    # A one-shot model call (the hub's own titles and sentences, `claude -p … --no-session-persistence`)
    # registers too, under a made-up name ("tmp-a8"), but it is no agent anyone works with.
    oneshot = {p['pid'] for p in procs or () if '--no-session-persistence' in p['args']}
    out = {}
    for path in glob.glob(os.path.join(sessions_dir, '*.json')):
        try:
            with open(path, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or not d.get('sessionId') or not isinstance(d.get('pid'), int):
            continue
        if alive is not None and d['pid'] not in alive:
            continue
        if d['pid'] in oneshot:
            continue
        stamp = d.get('statusUpdatedAt') or d.get('updatedAt') or d.get('startedAt')
        out[d['sessionId']] = {
            'status': d.get('status'), 'cwd': d.get('cwd'), 'name': d.get('name'), 'pid': d['pid'],
            'named': d.get('nameSource') == 'user',
            'since': stamp / 1000 if isinstance(stamp, (int, float)) else None,
        }
    return out


# A background shell job of a Claude Code session: `zsh -c source …/shell-snapshots/snapshot-… && eval '<cmd>' …`
SNAPSHOT_EVAL = re.compile(r"shell-snapshots[/\\]snapshot-.*?eval '(.*?)'(?: <| &&|$)")
DECISION_WAIT = re.compile(r'(^|[\s/])tasks\s+wait\b')


def background_jobs(pid, procs):
    """Commands a session left running in the background (Bash run_in_background), from its process tree."""
    kids = {}
    for p in procs:
        kids.setdefault(p['ppid'], []).append(p)
    out, todo, seen = [], [pid], set()
    while todo:
        cur = todo.pop()
        for p in kids.get(cur, []):
            if p['pid'] in seen:
                continue
            seen.add(p['pid'])
            m = SNAPSHOT_EVAL.search(p['args'])
            if m:
                out.append(m.group(1))
                continue  # its own children belong to that job
            todo.append(p['pid'])
    return out


def after_turn(state, reg, helpers_busy, procs):
    """A session whose turn is over can still be at work (2026-11-22: the app looked stopped while its
    helper agent or background job ran): background helpers or jobs mean working; a background `tasks wait`
    alone means it waits for his answer on http://beacon/decisions. Returns (state, doing) or None to keep it."""
    if state not in ('waiting', 'done') or not reg.get('pid'):
        return None
    jobs = background_jobs(reg['pid'], procs)
    work = [j for j in jobs if not DECISION_WAIT.search(j)]
    if helpers_busy:
        return 'working', f"{helpers_busy} helper agent{'s' if helpers_busy > 1 else ''} working"
    if work:
        return 'working', f'Background: $ {short(work[0])}'
    if len(jobs) > len(work):
        return 'waiting', 'Waiting for your answer on http://beacon/decisions'
    return None


def claude_live(now, roots, procs, jobs=None, registry=None):
    jobs = jobs or {}
    registry = registry or {}
    claude_procs = claude_processes(procs)
    live_ids = {p['session_id'] for p in claude_procs if p['session_id']}
    live_ids |= {sid for sid, j in jobs.items() if not j['over']}
    live_ids |= set(registry)
    bare = [p for p in claude_procs if not p['session_id']]
    files = {}  # session id -> (path, mtime, size); an open session's log counts at any age
    for root in roots:
        for path in glob.glob(os.path.join(root, '*', '*.jsonl')):
            sid = os.path.splitext(os.path.basename(path))[0]
            try:
                st = os.stat(path)
            except OSError:
                continue
            if now - st.st_mtime > STALE_AFTER and sid not in live_ids:
                continue
            files[sid] = (path, st.st_mtime, st.st_size)

    # A bare `claude` (no --session-id, not in the registry: an older Claude Code) can't be
    # matched by id; its cwd names the project directory, whose newest transcript is almost
    # always the one it is writing.
    unmatched = []
    registered_cwds = {r['cwd'] for r in registry.values()}
    for p in bare:
        cwd = process_cwd(p['pid'])
        if registry and (cwd is None or cwd in registered_cwds):
            continue  # the registry already lists what runs there
        best = None
        if cwd:
            slug = claude_slug(cwd)
            for sid, (path, mtime, _) in files.items():
                if sid in live_ids or os.path.basename(os.path.dirname(path)) != slug:
                    continue
                if best is None or mtime > files[best][1]:
                    best = sid
        if best:
            live_ids.add(best)
        else:
            unmatched.append((p, cwd))

    out = []
    for sid in set(files) | set(registry):
        reg = registry.get(sid, {})
        if jobs.get(sid, {}).get('over'):
            continue  # a finished background job, even if a warm process still holds it
        path, mtime, size = files.get(sid, (None, None, 0))
        last = max(x for x in (mtime, reg.get('since'), 0) if x is not None) or now
        idle = max(0, now - last)
        if sid not in live_ids and idle > LIVE_WITHIN:
            continue
        r = {'title': None, 'cwd': None, 'branch': None, 'model': None, 'turn_ended': True,
             'doing': None, 'prompt': None, 'done': True}  # no log yet: never given a prompt
        if path:
            try:
                r = parse_claude(path, size)
            except OSError:
                pass
        if sid not in live_ids and r.get('at'):
            # No process holds it: only the last real conversation row says how recent it
            # is. Closing or archiving a session rewrites its log with untimed bookkeeping.
            last = r['at']
            idle = max(0, now - last)
            if idle > LIVE_WITHIN:
                continue
        helpers = helpers_busy = 0
        if path:
            for sub in glob.glob(os.path.join(os.path.dirname(path), sid, 'subagents', '*.jsonl')):
                try:
                    age = now - os.path.getmtime(sub)
                except OSError:
                    continue
                helpers += age < HELPER_ACTIVE
                helpers_busy += age < HELPER_BUSY
        # The session's own status beats anything read from its log.
        if reg.get('status') == 'busy':
            state = 'working'  # a long tool call is quiet in the log but still a turn in progress
        elif reg.get('status') == 'idle':
            state = 'done' if r['done'] else 'waiting'
        elif sid in registry and idle >= WORKING_STALL:
            # Registered (so its process runs) but too old a Claude Code to report a status
            # (the hub server's remote-control sessions): long quiet means it waits for a message,
            # not that it stalled.
            state = 'done' if r['done'] else 'waiting'
        else:
            state = derive_state(idle, r['turn_ended'], r['done'])
        doing = r['doing']
        bg = after_turn(state, reg, helpers_busy, procs)
        if bg:
            state, doing = bg
        title = reg.get('name') if reg.get('named') else None
        title = title or r['title'] or reg.get('name') or jobs.get(sid, {}).get('name')
        out.append({'agent': 'Claude Code', 'id': sid, 'title': short(title),
                    'project': project_of(r['cwd'] or reg.get('cwd')), 'branch': r['branch'], 'model': r['model'],
                    'state': state, 'last': round(last), 'doing': doing, 'prompt': r['prompt'],
                    'helpers': helpers, 'steps': r.get('steps') or []})
    for p, cwd in unmatched:
        if not cwd:
            # Windows gives no cwd: nothing would say what this process is.
            continue
        # Nothing says whether it is mid-turn; time since launch is the honest lower bound.
        out.append({'agent': 'Claude Code', 'id': f'pid-{p["pid"]}', 'title': None, 'project': project_of(cwd),
                    'branch': None, 'model': None, 'state': 'waiting', 'last': round(p.get('started') or now),
                    'doing': None, 'prompt': None, 'helpers': 0})
    return out


# ---------------------------------------------------------------- Codex

def codex_live(now, root):
    out = []
    for path in glob.glob(os.path.join(root, '**', 'rollout-*.jsonl'), recursive=True):
        try:
            st = os.stat(path)
        except OSError:
            continue
        idle = max(0, now - st.st_mtime)
        if idle > LIVE_WITHIN:
            continue
        with open(path, 'rb') as f:
            head = f.readline()
        try:
            meta = json.loads(head).get('payload') or {}
        except ValueError:
            meta = {}
        if meta.get('parent_thread_id') or meta.get('thread_source') == 'subagent':
            continue  # a review or helper run; its parent session is the one listed
        cwd, model, ended, doing, prompt, reply = meta.get('cwd'), None, False, None, None, None
        for o in read_tail(path, st.st_size):
            p = o.get('payload') or {}
            typ, ptyp = o.get('type'), p.get('type')
            if typ == 'turn_context':
                cwd, model = p.get('cwd') or cwd, p.get('model') or model
            elif typ == 'event_msg' and ptyp in ('task_started', 'user_message'):
                ended, reply = False, None
            elif typ == 'event_msg' and ptyp in ('task_complete', 'turn_aborted'):
                ended = True
                doing = short(p.get('last_agent_message')) or doing
                reply = p.get('last_agent_message') if ptyp == 'task_complete' else None
            elif typ == 'response_item' and ptyp in ('function_call', 'custom_tool_call', 'local_shell_call'):
                args = p.get('arguments') or p.get('input') or p.get('action') or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        m = re.search(r'"cmd"\s*:\s*"((?:[^"\\]|\\.)*)"', args)  # code-mode exec scripts
                        args = {'cmd': json.loads('"' + m.group(1) + '"')} if m else {}
                name = 'exec_command' if isinstance(args, dict) and args.get('cmd') else p.get('name') or ptyp
                doing = describe_tool(name, args)
            elif typ == 'response_item' and ptyp == 'message' and p.get('role') == 'assistant':
                text = ' '.join(c.get('text', '') for c in p.get('content') or [] if isinstance(c, dict))
                doing = short(text) or doing
            elif typ == 'response_item' and ptyp == 'message' and p.get('role') == 'user':
                text = '\n'.join(c.get('text', '') for c in p.get('content') or [] if isinstance(c, dict)).strip()
                if text and not text.startswith(('<', '# AGENTS.md', 'The following is the Codex agent history')):
                    prompt = short(codex_request(text))
        out.append({'agent': 'Codex', 'id': meta.get('id') or os.path.basename(path), 'title': None,
                    'project': project_of(cwd), 'branch': None, 'model': model,
                    'state': derive_state(idle, ended, says_done(reply)), 'last': round(st.st_mtime), 'doing': doing,
                    'prompt': prompt, 'helpers': 0})
    return out


# ---------------------------------------------------------------- OpenCode

def opencode_live(now, db_path, procs):
    if not os.path.exists(db_path):
        return []
    running = any(re.search(r'(^|[\\/])opencode(\.\w+)?(\s|$)', p['args']) for p in procs)
    uri = 'file:' + urllib.parse.quote(db_path.replace('\\', '/')) + '?mode=ro'
    con = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        # V2 keeps sessions in session_v2; V1's `session` rows are its migration
        # source, so an id present in both must be read from V2 only (V2 may have
        # archived it, and its time_updated keeps moving).
        try:
            have = {r[0] for r in con.execute('SELECT id FROM session_v2')}
        except sqlite3.Error:
            have = set()  # an install that never ran V2
        rows = []
        for table in ('session_v2', 'session'):
            try:
                for r in con.execute(
                        'SELECT id, title, directory, model, time_updated FROM %s '
                        'WHERE parent_id IS NULL AND time_archived IS NULL '
                        'ORDER BY time_updated DESC LIMIT 20' % table):
                    if table == 'session' and r[0] in have:
                        continue
                    rows.append(r)
            except sqlite3.Error:
                continue
        rows.sort(key=lambda r: r[4] or 0, reverse=True)
        rows = rows[:20]
    finally:
        con.close()
    out = []
    for sid, title, directory, model, updated in rows:
        last = (updated or 0) / 1000 if (updated or 0) > 1e12 else (updated or 0)
        idle = max(0, now - last)
        if idle > (STALE_AFTER if running else LIVE_WITHIN):
            continue
        if isinstance(model, str) and model.startswith('{'):
            try:
                model = json.loads(model).get('id')
            except ValueError:
                pass
        out.append({'agent': 'OpenCode', 'id': str(sid), 'title': short(title), 'project': project_of(directory),
                    'branch': None, 'model': model, 'state': derive_state(idle, idle > RECENT_ACTIVE),
                    'last': round(last), 'doing': None, 'prompt': None, 'helpers': 0})
    return out


# ---------------------------------------------------------------- Hermes

def hermes_project(cwd):
    name = project_of(cwd) if cwd else '(home)'
    return 'hermes' if name in ('(home)', '(no project)') else name


def hermes_live(now, db_path):
    """Hermes (the Telegram agent) keeps sessions open for days; only a recent message
    makes one live. A final assistant reply ends the turn, a tool call or a fresh user
    message means it is working."""
    if not os.path.exists(db_path):
        return []
    uri = 'file:' + urllib.parse.quote(db_path.replace('\\', '/')) + '?mode=ro'
    con = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        sessions = con.execute(
            'SELECT s.id, COALESCE(s.title, s.display_name), s.model, s.cwd, MAX(m.timestamp) '
            'FROM sessions s JOIN messages m ON m.session_id = s.id '
            'WHERE s.ended_at IS NULL AND s.archived = 0 AND s.parent_session_id IS NULL AND m.timestamp > ? '
            'GROUP BY s.id', (now - LIVE_WITHIN,)).fetchall()
        out = []
        for sid, title, model, cwd, last in sessions:
            # Only the conversation: Hermes also logs bookkeeping rows (session_meta) after a reply.
            msgs = con.execute("SELECT role, content, tool_calls, tool_name FROM messages WHERE session_id = ? "
                               "AND role IN ('user', 'assistant', 'tool') "
                               "ORDER BY timestamp DESC, id DESC LIMIT 30", (sid,)).fetchall()
            if not msgs:
                continue
            role, content, tool_calls, _ = msgs[0]
            ended = role == 'assistant' and not tool_calls
            done = ended and says_done(content)
            doing = None
            for r, c, tc, tn in msgs:
                if r == 'assistant' and tc:
                    try:
                        call = (json.loads(tc) or [{}])[0]
                        fn = call.get('function') or call
                        args = fn.get('arguments')
                        doing = describe_tool(fn.get('name'), json.loads(args) if isinstance(args, str) else args)
                    except (ValueError, TypeError, AttributeError, IndexError):
                        doing = 'Using a tool'
                    break
                if r == 'assistant' and c:
                    doing = short(c)
                    break
            prompt = next((short(c) for r, c, _, _ in msgs if r == 'user' and c), None)
            out.append({'agent': 'Hermes', 'id': sid, 'title': short(title), 'project': hermes_project(cwd),
                        'branch': None, 'model': model, 'state': derive_state(max(0, now - last), ended, done),
                        'last': round(last), 'doing': doing, 'prompt': prompt, 'helpers': 0})
        return out
    finally:
        con.close()


# ---------------------------------------------------------------- agents with no turn markers

def recency_live(now, agent, paths, project_from, id_from=lambda p: os.path.basename(os.path.dirname(p))):
    """Grok, Antigravity, Muse: a log written within RECENT_ACTIVE is working, otherwise waiting."""
    out = []
    for path in paths:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        idle = max(0, now - mtime)
        if idle > LIVE_WITHIN:
            continue
        out.append({'agent': agent, 'id': id_from(path), 'title': None,
                    'project': project_from(path), 'branch': None, 'model': None,
                    'state': derive_state(idle, idle > RECENT_ACTIVE), 'last': round(mtime),
                    'doing': None, 'prompt': None, 'helpers': 0})
    return out


def grok_project(path):
    # ~/.grok/sessions/<url-quoted cwd>/<session>/updates.jsonl
    return project_of(urllib.parse.unquote(os.path.basename(os.path.dirname(os.path.dirname(path)))))


# ---------------------------------------------------------------- snapshot

RANK = {'waiting': 0, 'working': 1, 'idle': 2, 'done': 3}


def snapshot(now=None):
    """Every live session on this machine, most urgent first."""
    now = now or time.time()
    procs = list_processes()
    claude_roots = [d for d in glob.glob(os.path.join(HOME, '.claude*', 'projects')) if os.path.isdir(d)]
    sources = [
        ('claude', lambda: claude_live(now, claude_roots, procs,
                                       claude_jobs(os.path.join(HOME, '.claude', 'jobs')),
                                       claude_registry(os.path.join(HOME, '.claude', 'sessions'), procs))),
        ('codex', lambda: codex_live(now, os.path.join(HOME, '.codex', 'sessions'))),
        ('opencode', lambda: opencode_live(now, os.path.join(HOME, '.local', 'share', 'opencode', 'opencode.db'), procs)),
        ('hermes', lambda: hermes_live(now, os.path.join(HOME, '.hermes', 'state.db'))),
        ('grok', lambda: recency_live(now, 'Grok', glob.glob(os.path.join(HOME, '.grok', 'sessions', '*', '*', 'updates.jsonl')),
                                      grok_project)),
        ('antigravity', lambda: recency_live(
            now, 'Antigravity',
            glob.glob(os.path.join(HOME, '.gemini', 'antigravity*', 'brain', '*', '.system_generated', 'logs', '*.jsonl')),
            lambda p: '(antigravity)', lambda p: p.split(os.sep + '.system_generated')[0].rsplit(os.sep, 1)[-1])),
        ('muse', lambda: recency_live(now, 'Muse', glob.glob(os.path.join(HOME, '.local', 'share', 'muse', 'sessions', '*', '*', '*', '*', 'session.jsonl')),
                                      lambda p: '(muse)')),
    ]
    sessions = []
    for name, fn in sources:
        try:
            sessions.extend(fn())
        except Exception as e:  # one broken source must not blank the others
            log.warning('%s: %s: %s', name, type(e).__name__, e)
    sessions.sort(key=lambda s: (RANK[s['state']], -s['last']))
    return {'scanned_at': round(now), 'sessions': sessions}
