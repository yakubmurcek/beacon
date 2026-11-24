#!/usr/bin/env python3
"""Collect AI agent usage on this machine and push it to the hub.

Standard library only, so the same file runs on macOS, Linux and Windows.
Every source is parsed into flat events:

  prompt  a message a human typed (text kept, truncated)
  call    one model API call (tokens, tool calls, reported cost if the agent logs one)

Events carry a stable id, so re-sending is harmless: the hub upserts. Byte offsets
per log file live in a state file, so a run only reads what was appended since the
last successful push. Delete the state file to re-send everything.

Config: ~/.config/agent-usage/config.json  {"hub": "...", "token": "...", "machine": "..."}
Flags override it. `--dry-run` prints a summary instead of pushing. `--every 10 --for 55`
runs it every 10 s for a minute, for schedulers that fire at most once a minute (Windows).
"""
import argparse
import datetime
import glob
import http.client
import json
import logging
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger('collect')
VERSION = 6  # 6: host.fresh (outdated copies, hub/freshness.py); 2: pushes carry a `live` snapshot; 3: and `host` (stats, versions, jobs: collector/host.py); 4: and session names; 5: call effort, job AI sniff, llm_runs
HOME = os.path.expanduser('~')
CONFIG_DIR = os.path.join(HOME, '.config', 'agent-usage')
PROMPT_MAX = 4000
BATCH = 2000


# ---------------------------------------------------------------- helpers

def parse_ts(v):
    """ISO string, seconds, ms or µs -> unix seconds (float)."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return datetime.datetime.fromisoformat(v.replace('Z', '+00:00')).timestamp()
        except ValueError:
            return None
    v = float(v)
    if v > 1e14:
        return v / 1e6
    if v > 1e11:
        return v / 1e3
    return v


SECRET_RE = re.compile(
    r'\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}[A-Za-z0-9_/+=-]*'           # OpenAI, Anthropic
    r'|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}'
    r'|xox[abprs]-[A-Za-z0-9-]{10,}'                      # Slack
    r'|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|glpat-[A-Za-z0-9_-]{20,}'
    r'|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})')  # JWT


def redact(text):
    """Mask API keys and tokens pasted into prompts; the dashboard shows prompt text."""
    return SECRET_RE.sub('[redacted key]', text) if text else text


CODEX_REQUEST_RE = re.compile(r'^## My request(?: for Codex)?:\s*\n', re.M)


def codex_request(text):
    """Codex wraps a prompt with attachments or annotations in headings; keep what was typed."""
    if not text or '## My request' not in text:
        return text
    parts = CODEX_REQUEST_RE.split(text)
    if len(parts) < 2:
        return text
    body = re.sub(r'<image\b[^>]*>\s*</image>', '', parts[-1]).strip()
    return body or text


def clip(text):
    text = re.sub(r'\s+\n', '\n', redact((text or '').strip()))
    return text[:PROMPT_MAX]


def worktree_repo(path, name, absolute):
    """A git worktree checked out beside its repo (`usage-dashboard-names`) is that repo:
    its `.git` is a file pointing into `<repo>/.git/worktrees/`."""
    try:
        with open(('/' if absolute else '') + path + '/.git') as f:
            m = re.match(r'gitdir:\s*(.+?)/\.git/worktrees/', f.read())
        return re.split(r'[\\/]', m.group(1).rstrip('/\\'))[-1] if m else name
    except OSError:
        return name


def project_of(cwd):
    """Working directory -> project name. Worktrees and scratch dirs fold into one name."""
    if not cwd:
        return '(no project)'
    p = str(cwd).replace('\\', '/').rstrip('/')
    low = p.lower()
    if re.search(r'(^|/)(private/)?tmp/|/var/folders/|/appdata/local/temp/|/scratch-workspaces/', low):
        return 'scratch'
    p = re.split(r'/\.claude/worktrees/|/\.worktrees/|--claude-worktrees-', p)[0]
    parts = [x for x in p.split('/') if x]
    idx = None
    for i, part in enumerate(parts):
        if part.lower().endswith('projects'):
            idx = i
    if idx is not None:
        if idx + 1 >= len(parts):
            return '(projects root)'
        j = idx + 2 if parts[idx + 1].lower() in ('parked', 'archive') and idx + 2 < len(parts) else idx + 1
        name = worktree_repo('/'.join(parts[:j + 1]), parts[j], p.startswith('/'))
    elif parts and parts[-1] in ('.claude', '.grok', '.codex', '.gemini'):
        return 'agent config'
    elif p.lower() in (HOME.replace('\\', '/').lower(), '/root') or len(parts) <= 2:
        return '(home)'
    else:
        name = parts[-1]
    return re.sub(r'-wt-[\w-]+$', '', name)


class State:
    """Per-file byte offsets plus parser context, saved only after a successful push."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding='utf-8') as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        self.pending = {}
        self.names = {}  # session -> its name (custom-title beats ai-title), from the lines read this run

    def get(self, key):
        return self.data.get(key) or {}

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)


def read_new_lines(path, state):
    """Yield parsed JSON objects appended since the last run. Returns (objects, new_state)."""
    st = state.get(path)
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], None
    offset = st.get('offset', 0)
    ctx = st.get('ctx', {})
    if size < offset:  # truncated or rewritten: start over
        offset, ctx = 0, {}
    if size == offset:
        return [], None
    out = []
    with open(path, 'rb') as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b'\n')
    if end < 0:
        return [], None
    for raw in chunk[:end + 1].splitlines():
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out, {'offset': offset + end + 1, 'ctx': ctx}


# ---------------------------------------------------------------- Claude Code

CLAUDE_NOT_HUMAN = (
    '<local-command-caveat>', '<local-command-stdout>', '<local-command-stderr>',
    '<task-notification>', '<system-reminder>', '<bash-stdout>', '<bash-stderr>',
    '<fork-boilerplate>', '[Request interrupted', 'Caveat: The messages below',
    '<bash-input>', '<permission-response>',  # `!` shell commands (can hold secrets), permission replies
    'Background agent "',  # Claude Code's notice when a background agent is stopped
    'Goal set: ',  # its notice after /goal; the /goal itself is the prompt
)
BACKGROUND_STOPPED_RE = re.compile(r'\d+ background agents were stopped by the user')


LEADING_REMINDERS_RE = re.compile(r'^(?:\s*<system-reminder>.*?</system-reminder>)+\s*', re.S)


def claude_prompt_text(o):
    text = claude_message_text(o)
    if text is None:
        return None
    # The desktop app puts a reminder block before what he typed (a session with no project
    # folder): keep his text, drop the reminder. A record that is only reminders is dropped.
    text = LEADING_REMINDERS_RE.sub('', text.strip())
    if not text:
        return None
    cmd = re.search(r'<command-name>(.*?)</command-name>', text, re.S)
    if cmd:
        args = re.search(r'<command-args>(.*?)</command-args>', text, re.S)
        return (cmd.group(1).strip() + ' ' + (args.group(1).strip() if args else '')).strip()
    if text.startswith(CLAUDE_NOT_HUMAN) or text.startswith('<command-message>') or BACKGROUND_STOPPED_RE.match(text):
        return None
    return re.sub(r'</?(?:pasted_content|USER_REQUEST)[^>]*>', '', text)


def claude_message_text(o):
    """The typed text of a user record, or None for a record no human wrote (meta, sidechain, tool results)."""
    if not claude_from_human(o):
        return None
    content = (o.get('message') or {}).get('content')
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    if any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
        return None
    return '\n'.join(t for t in (b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text') if t)


def claude_automated(o):
    """A turn nobody typed: a /loop or cron re-fire, or a headless `claude -p` run from a
    script (health checks, nightly jobs). It runs the model but isn't a prompt."""
    return bool(o.get('scheduledFireId') or o.get('scheduledTaskId')
                or (o.get('entrypoint') == 'sdk-cli' and o.get('turnOrigin') != 'human'))


MID_RUN = '[mid-run] '  # typed while the agent was still working: steering, not a new request


def claude_from_human(o):
    """False for records Claude Code writes itself: meta, sidechain, compaction summaries and its own notices
    ("Goal set: …", task done)."""
    return not (o.get('isMeta') or o.get('isSidechain') or o.get('isCompactSummary')) and o.get('promptSource') != 'system'


def claude_queued_text(o):
    """A message typed while the agent was mid-turn. Claude Code hands it to the running turn
    as a `queued_command` attachment and never writes it as a user record of its own."""
    a = o.get('attachment') or {}
    if a.get('type') != 'queued_command' or a.get('commandMode', 'prompt') != 'prompt':
        return None
    origin = (a.get('origin') or {}).get('kind') or ('human' if a.get('humanTurn') else None)
    if origin != 'human':
        return None  # a background task's notification, not him
    text = queued_prompt(a.get('prompt'))
    if not text or text.startswith(CLAUDE_NOT_HUMAN) or text.startswith('<'):
        return None
    return MID_RUN + text


def queued_prompt(p):
    """A queued command's prompt (a string, or a list of content blocks) as stripped text."""
    if isinstance(p, list):
        p = '\n'.join(b.get('text', '') for b in p if isinstance(b, dict) and b.get('type') == 'text')
    return (p or '').strip() if isinstance(p, str) else ''


# One-off re-reads: when the parser learns something new, logs read before it are read once more so the hub
# gets it (the upsert replaces what it had). Each flag is saved with the offsets, after a good push.
CLAUDE_REREADS = (
    # Headless `claude -p` runs used to count as typed prompts.
    ('claude-sdk-v1', lambda path, is_sub: file_has(path, b'"sdk-cli"')),
    # Messages typed mid-run, and prompts behind a desktop reminder block, weren't read.
    ('claude-queued-v1', lambda path, is_sub: not is_sub and (
        file_has(path, b'"queued_command"') or file_has(path, b'without choosing a project folder'))),
    # Session names (custom-title from /rename, --name or the tasks hook; Claude Code's own ai-title) weren't sent.
    ('claude-names-v1', lambda path, is_sub: not is_sub and file_has(path, b'-title"')),
    # Each call's effort wasn't sent; only the last 8 days' logs.
    ('claude-effort-v1', lambda path, is_sub: recent(path, 8 * 86400) and file_has(path, b'"effort":"')),
)
CLAUDE_TITLES = {'custom-title': ('customTitle', 'custom'), 'ai-title': ('aiTitle', 'ai')}


def recent(path, age):
    try:
        return time.time() - os.path.getmtime(path) < age
    except OSError:
        return False


def file_has(path, needle, chunk=1 << 20):
    with open(path, 'rb') as f:
        tail = b''
        while True:
            block = f.read(chunk)
            if not block:
                return False
            if needle in tail + block:
                return True
            tail = block[-len(needle):]


def collect_claude(state, machine, roots):
    rereads = [check for flag, check in CLAUDE_REREADS if not state.get(flag)]
    events = []
    for root in roots:
        for path in glob.glob(os.path.join(root, '**', '*.jsonl'), recursive=True):
            session, is_sub = claude_log_session(root, path)
            if state.get(path) and any(check(path, is_sub) for check in rereads):
                state.set(path, {})
            lines, new = read_new_lines(path, state)
            if new is None:
                continue
            events += ClaudeLog(machine, session, is_sub, new['ctx'], state.names).read(lines)
            state.pending[path] = new
    for flag, _ in CLAUDE_REREADS:
        state.pending[flag] = {'done': 1}
    return events


def claude_log_session(root, path):
    """(session id, is a subagent's log) for <project>/<session>.jsonl or <project>/<session>/subagents/agent-x.jsonl."""
    rel = os.path.relpath(path, root).replace('\\', '/').split('/')
    is_sub = 'subagents' in rel
    return (rel[1] if is_sub and len(rel) >= 4 else os.path.splitext(rel[-1])[0]), is_sub


class ClaudeLog:
    """The new lines of one Claude Code transcript as events. A subagent's log adds calls only: what it was
    asked came from the agent, not a human, and its name is the parent session's."""

    def __init__(self, machine, session, is_sub, ctx, names):
        self.machine, self.session, self.is_sub, self.ctx, self.names = machine, session, is_sub, ctx, names
        self.calls = {}  # request id -> its call event, folded together across streamed lines

    def read(self, lines):
        events = []
        for o in lines:
            if o.get('type') in CLAUDE_TITLES and not self.is_sub:  # untimed lines
                self.name(o)
                continue
            t = parse_ts(o.get('timestamp'))
            if t is None:
                continue
            if o.get('cwd'):
                self.ctx['cwd'] = o['cwd']
            if o.get('type') == 'assistant':
                e = self.call(t, o)
            elif not self.is_sub:
                e = self.prompt(t, o)
            else:
                e = None
            if e:
                events.append(e)
        return events

    def event(self, t, kind, **kw):
        return ev(self.machine, 'Claude Code', self.session, self.ctx, t, kind, **kw)

    def name(self, o):
        """A custom name beats Claude Code's generated one, whichever comes later."""
        field, source = CLAUDE_TITLES[o['type']]
        title = o.get(field)
        if not isinstance(title, str) or not title.strip():
            return
        key = f'Claude Code:{self.session}'
        if source == 'custom' or self.names.get(key, {}).get('source') != 'custom':
            self.names[key] = {'session': key, 'title': title.strip()[:120], 'source': source}

    def prompt(self, t, o):
        """A prompt a human typed (a user record, or a message queued mid-turn), else None."""
        kind = o.get('type')
        if kind == 'user':
            text = claude_prompt_text(o)
            if text and claude_automated(o):
                text = '[scheduled] ' + text  # runs the model, but nobody typed it
            prefix, own = 'claude-u:', o.get('uuid')
        elif kind == 'attachment':
            text = claude_queued_text(o)
            prefix, own = 'claude-q:', (o.get('attachment') or {}).get('delivery_id') or o.get('uuid')
        else:
            return None
        if not text:
            return None
        return self.event(t, 'prompt', id=prefix + (own or f'{self.session}:{t}'), text=clip(text))

    def call(self, t, o):
        """Fold one assistant line into its API call. Returns the call's event when this line starts it."""
        msg = o.get('message') or {}
        model = msg.get('model')
        if not claude_billed(o, model):
            return None
        rid = o.get('requestId') or msg.get('id') or o.get('uuid')
        u = msg.get('usage') or {}
        e = self.calls.get(rid)
        started = e is None
        if started:
            e = self.event(t, 'call', id='claude:' + rid, model=model, subagent=int(self.is_sub),
                           speed=u.get('speed') or 'standard')
            self.calls[rid] = e
        if isinstance(o.get('effort'), str) and o['effort']:
            e['effort'] = o['effort'][:12]  # the reasoning effort the call ran at (low … max)
        # Streaming writes one line per content block with the same usage;
        # keep the largest numbers and add up the tool_use blocks.
        for k, v in claude_usage(u).items():
            e[k] = max(e.get(k, 0), v)
        e['tools'] = e.get('tools', 0) + tool_uses(msg.get('content'))
        return e if started else None


def claude_billed(o, model):
    """Whether an assistant line is a real API call: not a synthetic message or an API error."""
    return bool(model) and model != '<synthetic>' and not o.get('isApiErrorMessage')


def tool_uses(content):
    return sum(1 for b in (content or []) if isinstance(b, dict) and b.get('type') == 'tool_use')


def claude_usage(u):
    """One assistant line's token counts. Older logs give cache writes as one total: all of it is 5-minute writes."""
    cc = u.get('cache_creation') or {}
    w1h = cc.get('ephemeral_1h_input_tokens') or 0
    w5m = cc.get('ephemeral_5m_input_tokens')
    if w5m is None:
        w5m = max(0, (u.get('cache_creation_input_tokens') or 0) - w1h)
    return {'input': u.get('input_tokens') or 0, 'output': u.get('output_tokens') or 0,
            'cache_read': u.get('cache_read_input_tokens') or 0, 'cache_write_5m': w5m, 'cache_write_1h': w1h}


def ev(machine, agent, session, ctx, t, kind, **kw):
    e = {'machine': machine, 'agent': agent, 'session': f'{agent}:{session}',
         'project': project_of(ctx.get('cwd')), 'ts': round(t, 3), 'kind': kind}
    e.update(kw)
    return e


# ---------------------------------------------------------------- Codex

CODEX_SUB_RE = re.compile(rb'"(?:parent_thread_id|thread_source"\s*:\s*"subagent)')
CODEX_TOOL_CALLS = ('function_call', 'custom_tool_call', 'local_shell_call')
# Not typed by a human: context blocks, the rules file, and Codex's own action reviews logged as user turns.
CODEX_NOT_TYPED = ('<', '# AGENTS.md', 'The following is the Codex agent history')


def collect_codex(state, machine, root):
    events = []
    # Subagent runs (e.g. the "guardian" that reviews each action) used to be sent as
    # sessions of their own; read those files once more so they move under their parent.
    redo = not state.get('codex-subagents-v1')
    # Archiving a thread moves its log out of sessions/; ids don't depend on the path, so a log
    # read before it moved is not counted twice.
    archived = os.path.join(os.path.dirname(root), 'archived_sessions')
    paths = glob.glob(os.path.join(root, '**', 'rollout-*.jsonl'), recursive=True) + glob.glob(os.path.join(archived, 'rollout-*.jsonl'))
    for path in paths:
        if redo and state.get(path) and codex_is_subagent(path):
            state.set(path, {})
        lines, new = read_new_lines(path, state)
        if new is None:
            continue
        events += CodexLog(machine, path, new['ctx']).read(lines)
        state.pending[path] = new
    state.pending['codex-subagents-v1'] = {'done': 1}  # saved with the offsets, after a good push
    return events


def codex_user_text(p):
    """A user message's text when a human typed it, else ''."""
    text = '\n'.join(c.get('text', '') for c in p.get('content') or [] if isinstance(c, dict)).strip()
    return '' if text.startswith(CODEX_NOT_TYPED) else text


def codex_is_subagent(path):
    with open(path, 'rb') as f:
        return bool(CODEX_SUB_RE.search(f.readline()))


class CodexLog:
    """The new lines of one Codex rollout as events. Its context (session, model, cwd, tool calls since the last
    count) lives in ctx, which is saved with the offset, so the next run picks up mid-session."""

    def __init__(self, machine, path, ctx):
        self.machine, self.path, self.ctx = machine, path, ctx

    def read(self, lines):
        events = []
        for o in lines:
            p = o.get('payload') or {}
            typ = o.get('type')
            if typ == 'session_meta':
                self.session_meta(p)
                continue
            if typ == 'turn_context':
                self.ctx['model'] = p.get('model') or self.ctx.get('model')
                self.ctx['cwd'] = p.get('cwd') or self.ctx.get('cwd')
                continue
            t = parse_ts(o.get('timestamp'))
            if t is None:
                continue
            e = self.line(t, o, typ, p)
            if e:
                events.append(e)
        return events

    def session_meta(self, p):
        ctx = self.ctx
        own = p.get('id') or p.get('session_id')
        parent = p.get('parent_thread_id') or (p.get('session_id') if p.get('thread_source') == 'subagent' else None)
        ctx['own'], ctx['sid'] = own, parent or own
        ctx['sub'] = int(bool(parent) and parent != own)  # its calls count toward the parent session
        ctx['cwd'] = p.get('cwd')

    def line(self, t, o, typ, p):
        ptyp = p.get('type')
        if typ == 'response_item' and ptyp in CODEX_TOOL_CALLS:
            self.ctx['tools'] = self.ctx.get('tools', 0) + 1
        elif typ == 'response_item' and ptyp == 'message' and p.get('role') == 'user':
            return self.prompt(t, o, p)
        elif typ == 'event_msg' and ptyp == 'token_count':
            return self.call(t, o, p)
        return None

    def ids(self):
        """(session the events go under, this log's own id: event ids stay per file, so a parent's and a
        subagent's never collide)."""
        sid = self.ctx.get('sid') or os.path.basename(self.path)
        return sid, self.ctx.get('own') or sid

    def prompt(self, t, o, p):
        text = codex_user_text(p)
        if not text or self.ctx.get('sub'):
            return None
        sid, own = self.ids()
        return ev(self.machine, 'Codex', sid, self.ctx, t, 'prompt',
                  id=f'codex-u:{own}:{o.get("ordinal", t)}', text=clip(codex_request(text)))

    def call(self, t, o, p):
        ctx = self.ctx
        info = p.get('info') or {}
        total = (info.get('total_token_usage') or {}).get('total_tokens')
        last = info.get('last_token_usage') or {}
        if not last or total == ctx.get('last_total'):
            return None  # Codex repeats the same count after some events
        ctx['last_total'] = total
        cached = last.get('cached_input_tokens') or 0
        sid, own = self.ids()
        return ev(self.machine, 'Codex', sid, ctx, t, 'call',
                  id=f'codex:{own}:{o.get("ordinal", t)}', model=ctx.get('model') or 'codex (unknown model)',
                  subagent=ctx.get('sub', 0),
                  input=max(0, (last.get('input_tokens') or 0) - cached), cache_read=cached,
                  cache_write_5m=last.get('cache_write_input_tokens') or 0,
                  output=last.get('output_tokens') or 0,
                  reasoning=last.get('reasoning_output_tokens') or 0,
                  tools=ctx.pop('tools', 0))


# ---------------------------------------------------------------- Grok

def collect_grok(state, machine, root):
    events = []
    for path in glob.glob(os.path.join(root, '*', '*', 'updates.jsonl')):
        sess_dir = os.path.dirname(path)
        sid = os.path.basename(sess_dir)
        lines, new = read_new_lines(path, state)
        if new is None:
            continue
        ctx = new['ctx']
        ctx.setdefault('cwd', urllib.parse.unquote(os.path.basename(os.path.dirname(sess_dir))))
        events += grok_events(machine, sid, ctx, lines)
        state.pending[path] = new
    return events


def grok_events(machine, sid, ctx, lines):
    events = []
    prompts = {}  # a prompt arrives in chunks: one event per prompt index, its text grown in place
    for o in lines:
        upd = (o.get('params') or {}).get('update') or {}
        kind = upd.get('sessionUpdate')
        t = parse_ts((o.get('_meta') or {}).get('agentTimestampMs') or o.get('timestamp'))
        if t is None:
            continue
        if kind == 'user_message_chunk':
            idx = (upd.get('_meta') or {}).get('promptIndex', t)
            pid = f'grok-u:{sid}:{idx}'
            text = ((upd.get('content') or {}).get('text') or '')
            if pid in prompts:
                prompts[pid]['text'] = clip(prompts[pid]['text'] + text)
            else:
                prompts[pid] = ev(machine, 'Grok', sid, ctx, t, 'prompt', id=pid, text=clip(text))
                events.append(prompts[pid])
        elif kind == 'tool_call':
            ctx['tools'] = ctx.get('tools', 0) + 1
        elif kind == 'turn_completed':
            events += grok_calls(machine, sid, ctx, t, upd)
    return events


def grok_calls(machine, sid, ctx, t, upd):
    """A finished turn: one call event per model it used; the turn's tool calls go on the first."""
    usage = upd.get('usage') or {}
    per_model = usage.get('modelUsage') or {'grok (unknown model)': usage}
    tools = ctx.pop('tools', 0)
    out = []
    for model, mu in per_model.items():
        cached = mu.get('cachedReadTokens') or 0
        out.append(ev(machine, 'Grok', sid, ctx, t, 'call',
                      id=f'grok:{sid}:{upd.get("prompt_id")}:{model}', model=model,
                      input=max(0, (mu.get('inputTokens') or 0) - cached), cache_read=cached,
                      cache_write_5m=mu.get('cacheCreationTokens') or 0,
                      output=mu.get('outputTokens') or 0,
                      reasoning=mu.get('reasoningTokens') or 0,
                      calls=mu.get('modelCalls') or 1, tools=tools,
                      cost_reported=(mu['costUsdTicks'] / 1e10) if mu.get('costUsdTicks') is not None else None))
        tools = 0
    return out


# ---------------------------------------------------------------- OpenCode

def opencode_copy(mid, created_s):
    """True for a message a fork copied from another session: OpenCode ids carry the time
    they were minted (ms mod 2^36, times 4096), and a copy is minted long after it was created."""
    try:
        minted = int(mid[4:16], 16) >> 12
    except ValueError:
        return False
    delta = (minted - int(created_s * 1000)) % (1 << 36)
    return 60_000 < delta < (1 << 35)


def collect_opencode(state, machine, db_path):
    if not os.path.exists(db_path):
        return []
    since = state.get(db_path).get('since', 0)
    uri = 'file:' + urllib.parse.quote(db_path.replace('\\', '/')) + '?mode=ro'
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as e:
        log.warning('opencode: %s', e)
        return []
    events = []
    newest = since
    # V1 storage (written until OpenCode 2), then V2; an install has either or both.
    for read in (opencode_v1, opencode_v2):
        try:
            for updated, e in read(con, since, machine):
                newest = max(newest, updated)
                if e:
                    events.append(e)
        except sqlite3.OperationalError as e:
            if 'no such table' not in str(e):
                log.warning('opencode: %s', e)
    con.close()
    state.pending[db_path] = {'since': newest}
    return events


def opencode_v1(con, since, machine):
    """V1: message + part rows, sessions in `session`. Yields (time_updated, event or None) per message."""
    rows = con.execute(
        'SELECT m.id, m.session_id, m.time_updated, m.data, s.directory, s.parent_id '
        'FROM message m JOIN session s ON s.id = m.session_id '
        'WHERE m.time_updated > ? ORDER BY m.time_updated', (since,)).fetchall()
    texts, tools = opencode_v1_parts(con, [r[0] for r in rows])
    for mid, sid, updated, data, directory, parent in rows:
        msg = opencode_message(mid, data)
        if msg is None:
            yield updated, None
            continue
        d, t = msg
        ctx = {'cwd': ((d.get('path') or {}).get('cwd')) or directory}
        e = None
        if d.get('role') == 'user' and not parent:
            e = opencode_prompt(machine, parent or sid, ctx, t, mid, '\n'.join(texts.get(mid, [])))
        elif d.get('role') == 'assistant':
            e = ev(machine, 'OpenCode', parent or sid, ctx, t, 'call', id='opencode:' + mid,
                   model=d.get('modelID') or 'unknown', subagent=int(bool(parent)), **opencode_tokens(d),
                   tools=tools.get(mid, 0), cost_reported=d.get('cost'))
        yield updated, e


def opencode_v1_parts(con, ids):
    """{message id: [typed text, ...]} and {message id: tool calls} from V1's part rows."""
    texts, tools = {}, {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ','.join('?' * len(chunk))
        for mid, data in con.execute(f'SELECT message_id, data FROM part WHERE message_id IN ({q})', chunk):
            try:
                d = json.loads(data)
            except ValueError:
                continue
            if d.get('type') == 'text' and not d.get('synthetic'):
                texts.setdefault(mid, []).append(d.get('text') or '')
            elif d.get('type') == 'tool':
                tools[mid] = tools.get(mid, 0) + 1
    return texts, tools


def opencode_v2(con, since, machine):
    """V2: sessions in session_v2, messages in session_message with their text inline (no part rows); synthetic,
    system and compaction rows have `type`s of their own, so nothing to sift. Yields (time_updated, event or None)."""
    rows = con.execute(
        "SELECT m.id, m.session_id, m.type, m.time_updated, m.data, s.directory, s.parent_id "
        "FROM session_message m JOIN session_v2 s ON s.id = m.session_id "
        "WHERE m.type IN ('user', 'assistant') AND m.time_updated > ? "
        "ORDER BY m.time_updated", (since,)).fetchall()
    for mid, sid, mtype, updated, data, directory, parent in rows:
        msg = opencode_message(mid, data)
        if msg is None:
            yield updated, None
            continue
        d, t = msg
        ctx = {'cwd': directory}
        e = None
        if mtype == 'user' and not parent:
            e = opencode_prompt(machine, parent or sid, ctx, t, mid, d.get('text') or '')
        elif mtype == 'assistant':
            tools = sum(1 for c in (d.get('content') or []) if isinstance(c, dict) and c.get('type') == 'tool')
            e = ev(machine, 'OpenCode', parent or sid, ctx, t, 'call', id='opencode:' + mid,
                   model=(d.get('model') or {}).get('id') or 'unknown', subagent=int(bool(parent)), **opencode_tokens(d),
                   tools=tools, cost_reported=d.get('cost'))
        yield updated, e


def opencode_message(mid, data):
    """(message, created time), or None for a row that can't be read or is a fork's copy (already counted in the
    session it was forked from)."""
    try:
        d = json.loads(data)
    except ValueError:
        return None
    t = parse_ts(((d.get('time') or {}).get('created')))
    if t is None:
        return None
    if mid.startswith('msg_') and opencode_copy(mid, t):
        return None
    return d, t


def opencode_prompt(machine, session, ctx, t, mid, text):
    text = text.strip()
    if not text or text.startswith('<system-reminder>'):
        return None
    return ev(machine, 'OpenCode', session, ctx, t, 'prompt', id='opencode-u:' + mid, text=clip(text))


def opencode_tokens(d):
    tok = d.get('tokens') or {}
    cache = tok.get('cache') or {}
    return {'input': tok.get('input') or 0, 'output': tok.get('output') or 0, 'reasoning': tok.get('reasoning') or 0,
            'cache_read': cache.get('read') or 0, 'cache_write_5m': cache.get('write') or 0}


# ---------------------------------------------------------------- Antigravity

def collect_antigravity(state, machine, brain_roots):
    parent_of = antigravity_parents(brain_roots)
    events = []
    for root in brain_roots:
        for brain in glob.glob(os.path.join(root, '*')):
            path = antigravity_log(brain)
            if not path:
                continue
            lines, new = read_new_lines(path, state)
            if new is None:
                continue
            events += antigravity_events(machine, os.path.basename(brain), parent_of, new['ctx'], lines)
            state.pending[path] = new
    return events


def antigravity_parents(brain_roots):
    """{helper brain id: the brain that started it}, from each brain's subagents folder."""
    parent_of = {}
    for root in brain_roots:
        for f in glob.glob(os.path.join(root, '*', '.system_generated', 'subagents', '*.json')):
            parent_of[os.path.splitext(os.path.basename(f))[0]] = f.split(os.sep + '.system_generated')[0].rsplit(os.sep, 1)[-1]
    return parent_of


def antigravity_log(brain):
    logs = os.path.join(brain, '.system_generated', 'logs')
    for name in ('transcript_full.jsonl', 'transcript.jsonl'):
        path = os.path.join(logs, name)
        if os.path.exists(path):
            return path
    return None


def antigravity_events(machine, bid, parent_of, ctx, lines):
    session = parent_of.get(bid, bid)
    helper = bid in parent_of
    events = []
    for o in lines:
        t = parse_ts(o.get('created_at'))
        if t is None:
            continue
        antigravity_cwd(ctx, o.get('tool_calls'))
        step = o.get('step_index', t)
        if o.get('type') == 'USER_INPUT' and o.get('source') == 'USER_EXPLICIT' and not helper:
            text = antigravity_request(str(o.get('content') or ''))
            if text.strip():
                events.append(ev(machine, 'Antigravity', session, ctx, t, 'prompt',
                                 id=f'antigravity-u:{bid}:{step}', text=clip(text)))
        elif o.get('type') == 'PLANNER_RESPONSE':
            events.append(ev(machine, 'Antigravity', session, ctx, t, 'call',
                             id=f'antigravity:{bid}:{step}', model=o.get('model') or 'gemini (unreported)',
                             subagent=int(helper), tools=len(o.get('tool_calls') or [])))
    return events


def antigravity_cwd(ctx, tool_calls):
    """Antigravity logs no working directory; the first tool call that names one gives the session's."""
    for tc in tool_calls or []:
        cwd = str(((tc or {}).get('args') or {}).get('Cwd') or '').strip('"')
        if cwd and not ctx.get('cwd'):
            ctx['cwd'] = cwd


def antigravity_request(raw):
    """What was typed: the USER_REQUEST block, else the input without its tagged context blocks."""
    m = re.search(r'<USER_REQUEST>\s*(.*?)\s*(?:</USER_REQUEST>|$)', raw, re.S)
    return m.group(1) if m else re.sub(r'<([A-Z_]+)>[\s\S]*?</\1>', '', raw)


# ---------------------------------------------------------------- Muse

def find_key(obj, keys, depth=0):
    if depth > 4:
        return None
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), str):
                return obj[k]
        for v in obj.values():
            r = find_key(v, keys, depth + 1)
            if r:
                return r
    return None


def collect_muse(state, machine, root):
    events = []
    for path in glob.glob(os.path.join(root, '*', '*', '*', '*', 'session.jsonl')):
        sid = os.path.basename(os.path.dirname(path))
        lines, new = read_new_lines(path, state)
        if new is None:
            continue
        events += muse_events(machine, sid, new['ctx'], lines)
        state.pending[path] = new
    return events


def muse_events(machine, sid, ctx, lines):
    events = []
    for o in lines:
        t = parse_ts(o.get('recorded_at'))
        if t is None:
            continue
        pt = o.get('payload_type') or ''
        payload = o.get('payload') or {}
        oid = o.get('id') or f'{sid}:{t}'  # a record without an id must not collapse into one event
        if not ctx.get('cwd') and pt in MUSE_META:
            ctx['cwd'] = find_key(payload, ('cwd', 'workspace_root', 'working_directory'))
        if pt == 'runtime.user_intent.accepted':
            text = muse_text(payload)
            if text.strip():
                events.append(ev(machine, 'Muse', sid, ctx, t, 'prompt', id=f'muse-u:{oid}', text=clip(text)))
        elif pt == 'tool_batch.effect.started':
            ctx['tools'] = ctx.get('tools', 0) + 1
        elif pt == 'runtime.session':
            e = muse_call(machine, sid, ctx, t, oid, payload.get('event') or {})
            if e:
                events.append(e)
    return events


def muse_text(payload):
    return ' '.join(b.get('text', '') for b in payload.get('refill_blocks') or [] if isinstance(b, dict))


def muse_call(machine, sid, ctx, t, oid, e):
    if e.get('kind') != 'model_completed':
        return None
    u = e.get('usage') or {}
    cached = (u.get('cache_read_tokens') or u.get('cached_tokens') or 0)
    return ev(machine, 'Muse', sid, ctx, t, 'call', id=f'muse:{oid}',
              model=e.get('model') or 'muse (unknown model)',
              input=max(0, (u.get('input_tokens') or 0) - cached), cache_read=cached,
              cache_write_5m=u.get('cache_write_tokens') or 0,
              output=u.get('output_tokens') or 0, reasoning=u.get('reasoning_tokens') or 0,
              tools=ctx.pop('tools', 0))


MUSE_META = ('runtime.session.metadata', 'session.opened.observed')


# ---------------------------------------------------------------- main

def sources():
    claude_roots = [d for d in glob.glob(os.path.join(HOME, '.claude*', 'projects')) if os.path.isdir(d)]
    brains = glob.glob(os.path.join(HOME, '.gemini', 'antigravity*', 'brain'))
    return [
        ('claude', lambda s, m: collect_claude(s, m, claude_roots)),
        ('codex', lambda s, m: collect_codex(s, m, os.path.join(HOME, '.codex', 'sessions'))),
        ('grok', lambda s, m: collect_grok(s, m, os.path.join(HOME, '.grok', 'sessions'))),
        ('opencode', lambda s, m: collect_opencode(s, m, os.path.join(HOME, '.local', 'share', 'opencode', 'opencode.db'))),
        ('antigravity', lambda s, m: collect_antigravity(s, m, brains)),
        ('muse', lambda s, m: collect_muse(s, m, os.path.join(HOME, '.local', 'share', 'muse', 'sessions'))),
    ]


def push(hub, token, machine, events, counts, live=None, host=None, names=None):
    url = hub.rstrip('/') + '/api/ingest'
    for i in range(0, max(1, len(events)), BATCH):
        payload = {'machine': machine, 'version': VERSION, 'counts': counts, 'events': events[i:i + BATCH]}
        if i == 0 and live is not None:
            payload['live'] = live
        if i == 0 and host is not None:
            payload['host'] = host
        if i == 0 and names:
            payload['names'] = names
        body = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=body, method='POST', headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()


def main():
    logging.basicConfig(stream=sys.stderr, format='%(name)s: %(message)s')
    try:
        with open(os.path.join(CONFIG_DIR, 'config.json'), encoding='utf-8') as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--hub', default=os.environ.get('AGENT_USAGE_HUB') or cfg.get('hub'))
    ap.add_argument('--token', default=os.environ.get('AGENT_USAGE_TOKEN') or cfg.get('token'))
    ap.add_argument('--machine', default=cfg.get('machine') or socket.gethostname().split('.')[0].lower())
    ap.add_argument('--state', default=os.path.join(CONFIG_DIR, 'state.json'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--every', type=float, default=0, metavar='S',
                    help='run again every S seconds (for schedulers that cannot fire more often than a minute)')
    ap.add_argument('--for', dest='duration', type=float, default=0, metavar='S',
                    help='with --every: stop starting new runs after S seconds')
    args = ap.parse_args()
    if not args.every:
        return run(args)
    start = time.monotonic()
    while True:
        began = time.monotonic()
        try:
            run(args)
        except SystemExit as e:  # one failed push must not end the loop
            if e.code not in (None, 0):
                log.error('this run failed: %s', e.code)
        nxt = began + args.every
        if nxt - start >= args.duration:
            return
        time.sleep(max(0, nxt - time.monotonic()))


def run(args):
    state = State(args.state)
    state.pending = {}
    events, counts = collect_all(state, args.machine)
    snap, me = machine_report(args)
    if args.dry_run:
        print_dry_run(counts, snap, events, state)
        return
    if not args.hub or not args.token:
        sys.exit('no hub/token: set them in ' + os.path.join(CONFIG_DIR, 'config.json'))
    try:
        push(args.hub, args.token, args.machine, events, counts, snap, me, list(state.names.values()))
    except (OSError, http.client.HTTPException) as e:
        # Offline or the hub is down: nothing is marked as sent, so the next run sends it all again.
        reason = e if isinstance(e, urllib.error.HTTPError) else getattr(e, 'reason', None) or e
        sys.exit(f'{args.machine}: could not push ({reason}); {len(events)} events kept for the next run')
    for k, v in state.pending.items():
        state.set(k, v)
    state.save()
    print(f'{args.machine}: pushed {len(events)} events {json.dumps(counts)}'
          + (f", {len(snap['sessions'])} live" if snap else ''))


def collect_all(state, machine):
    """Every source's new events, and how many each gave."""
    events, counts = [], {}
    for name, fn in sources():
        try:
            got = fn(state, machine)
        except Exception as e:  # one broken source must not block the others
            log.warning('%s: %s: %s', name, type(e).__name__, e)
            got = []
        counts[name] = len(got)
        events.extend(got)
    return events, counts


def machine_report(args):
    """(live snapshot, host report): what runs now and the machine itself. Either is None when it breaks:
    the history still goes out."""
    # What every agent is doing right now. Sent on each run (every 10 s), so the hub's
    # /api/live is never older than that for a machine that is up.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import live
        snap = live.snapshot()
    except Exception as e:
        log.warning('live: %s: %s', type(e).__name__, e)
        snap = None
    # The machine itself and its scheduled jobs (collector/host.py), for the hub's machines view
    # and its alarms: a job that fails or stops running is told from the hub, not from this box.
    try:
        import host
        me = host.report(os.path.join(os.path.dirname(os.path.abspath(args.state)), 'host-cache.json'),
                         hub=args.hub, machine=args.machine)
    except Exception as e:
        log.warning('host: %s: %s', type(e).__name__, e)
        me = None
    # Which background job started each running agent process, so the hub can put every run's model,
    # effort and cost on its job (http://beacon/ai).
    if me is not None:
        try:
            import llm
            me['llm_runs'] = llm.runs(live.list_processes())
        except Exception as e:
            log.warning('llm runs: %s: %s', type(e).__name__, e)
    return snap, me


def print_dry_run(counts, snap, events, state):
    print(json.dumps(counts))
    if snap:
        for s in snap['sessions']:
            print(f"live {s['state']:<7} {s['agent']:<11} {s['project']:<24} {s['doing'] or ''}"[:160])
    for e in events[-3:]:
        print(json.dumps(e)[:300])
    for n in list(state.names.values())[-5:]:
        print('name', json.dumps(n, ensure_ascii=False))


if __name__ == '__main__':
    main()
