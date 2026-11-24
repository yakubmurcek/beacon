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
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

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
    if o.get('isMeta') or o.get('isSidechain') or o.get('isCompactSummary'):
        return None
    if o.get('promptSource') == 'system':  # Claude Code's own notices ("Goal set: …", task done)
        return None
    content = (o.get('message') or {}).get('content')
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
            return None
        texts = [b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text']
    else:
        return None
    text = '\n'.join(t for t in texts if t).strip()
    # The desktop app puts a reminder block before what he typed (a session with no project
    # folder): keep his text, drop the reminder. A record that is only reminders is dropped.
    text = LEADING_REMINDERS_RE.sub('', text)
    if not text:
        return None
    cmd = re.search(r'<command-name>(.*?)</command-name>', text, re.S)
    if cmd:
        args = re.search(r'<command-args>(.*?)</command-args>', text, re.S)
        return (cmd.group(1).strip() + ' ' + (args.group(1).strip() if args else '')).strip()
    if text.startswith(CLAUDE_NOT_HUMAN) or text.startswith('<command-message>') or BACKGROUND_STOPPED_RE.match(text):
        return None
    return re.sub(r'</?(?:pasted_content|USER_REQUEST)[^>]*>', '', text)


def claude_automated(o):
    """A turn nobody typed: a /loop or cron re-fire, or a headless `claude -p` run from a
    script (health checks, nightly jobs). It runs the model but isn't a prompt."""
    return bool(o.get('scheduledFireId') or o.get('scheduledTaskId')
                or (o.get('entrypoint') == 'sdk-cli' and o.get('turnOrigin') != 'human'))


MID_RUN = '[mid-run] '  # typed while the agent was still working: steering, not a new request


def claude_queued_text(o):
    """A message typed while the agent was mid-turn. Claude Code hands it to the running turn
    as a `queued_command` attachment and never writes it as a user record of its own."""
    a = o.get('attachment') or {}
    if a.get('type') != 'queued_command' or a.get('commandMode', 'prompt') != 'prompt':
        return None
    origin = (a.get('origin') or {}).get('kind') or ('human' if a.get('humanTurn') else None)
    if origin != 'human':
        return None  # a background task's notification, not him
    p = a.get('prompt')
    if isinstance(p, list):
        p = '\n'.join(b.get('text', '') for b in p if isinstance(b, dict) and b.get('type') == 'text')
    text = (p or '').strip() if isinstance(p, str) else ''
    if not text or text.startswith(CLAUDE_NOT_HUMAN) or text.startswith('<'):
        return None
    return MID_RUN + text


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
    events = []
    # Headless `claude -p` runs used to count as typed prompts; read those logs once more so
    # the hub gets them marked (the upsert replaces the text).
    redo = not state.get('claude-sdk-v1')
    # Messages typed mid-run, and prompts behind a desktop reminder block, weren't read before
    # claude-queued-v1; read those logs once more.
    redo_queued = not state.get('claude-queued-v1')
    # Session names (custom-title from /rename, --name or the tasks hook; Claude Code's own ai-title)
    # weren't sent before claude-names-v1; read the logs that hold one once more.
    redo_names = not state.get('claude-names-v1')
    # Each call's effort wasn't sent before claude-effort-v1; read the last 8 days' logs once more for it.
    redo_effort = not state.get('claude-effort-v1')
    for root in roots:
        for path in glob.glob(os.path.join(root, '**', '*.jsonl'), recursive=True):
            rel = os.path.relpath(path, root).replace('\\', '/').split('/')
            # <project>/<session>.jsonl  or  <project>/<session>/subagents/agent-x.jsonl
            is_sub = 'subagents' in rel
            session = rel[1] if is_sub and len(rel) >= 4 else os.path.splitext(rel[-1])[0]
            if redo and state.get(path) and file_has(path, b'"sdk-cli"'):
                state.set(path, {})
            if redo_queued and not is_sub and state.get(path) and (
                    file_has(path, b'"queued_command"') or file_has(path, b'without choosing a project folder')):
                state.set(path, {})
            if redo_names and not is_sub and state.get(path) and file_has(path, b'-title"'):
                state.set(path, {})
            if redo_effort and state.get(path) and recent(path, 8 * 86400) and file_has(path, b'"effort":"'):
                state.set(path, {})
            lines, new = read_new_lines(path, state)
            if new is None:
                continue
            ctx = new['ctx']
            calls = {}
            for o in lines:
                if o.get('type') in ('custom-title', 'ai-title') and not is_sub:  # untimed lines
                    title = o.get('customTitle') if o['type'] == 'custom-title' else o.get('aiTitle')
                    if isinstance(title, str) and title.strip():
                        source = 'custom' if o['type'] == 'custom-title' else 'ai'
                        key = f'Claude Code:{session}'
                        if source == 'custom' or state.names.get(key, {}).get('source') != 'custom':
                            state.names[key] = {'session': key, 'title': title.strip()[:120], 'source': source}
                    continue
                t = parse_ts(o.get('timestamp'))
                if t is None:
                    continue
                if o.get('cwd'):
                    ctx['cwd'] = o['cwd']
                kind = o.get('type')
                msg = o.get('message') or {}
                if kind == 'user' and not is_sub:
                    text = claude_prompt_text(o)
                    if text and claude_automated(o):
                        text = '[scheduled] ' + text  # runs the model, but nobody typed it
                    if text:
                        events.append(ev(machine, 'Claude Code', session, ctx, t, 'prompt',
                                         id='claude-u:' + (o.get('uuid') or f'{session}:{t}'), text=clip(text)))
                elif kind == 'attachment' and not is_sub:
                    text = claude_queued_text(o)
                    if text:
                        a = o['attachment']
                        events.append(ev(machine, 'Claude Code', session, ctx, t, 'prompt',
                                         id='claude-q:' + (a.get('delivery_id') or o.get('uuid') or f'{session}:{t}'),
                                         text=clip(text)))
                elif kind == 'assistant':
                    model = msg.get('model')
                    if not model or model == '<synthetic>' or o.get('isApiErrorMessage'):
                        continue
                    rid = o.get('requestId') or msg.get('id') or o.get('uuid')
                    u = msg.get('usage') or {}
                    cc = u.get('cache_creation') or {}
                    w1h = cc.get('ephemeral_1h_input_tokens') or 0
                    w5m = cc.get('ephemeral_5m_input_tokens')
                    if w5m is None:
                        w5m = max(0, (u.get('cache_creation_input_tokens') or 0) - w1h)
                    tools = sum(1 for b in (msg.get('content') or [])
                                if isinstance(b, dict) and b.get('type') == 'tool_use')
                    e = calls.get(rid)
                    if e is None:
                        e = ev(machine, 'Claude Code', session, ctx, t, 'call', id='claude:' + rid,
                               model=model, subagent=int(is_sub), speed=u.get('speed') or 'standard')
                        calls[rid] = e
                        events.append(e)
                    if isinstance(o.get('effort'), str) and o['effort']:
                        e['effort'] = o['effort'][:12]  # the reasoning effort the call ran at (low … max)
                    # Streaming writes one line per content block with the same usage;
                    # keep the largest numbers and add up the tool_use blocks.
                    e['input'] = max(e.get('input', 0), u.get('input_tokens') or 0)
                    e['output'] = max(e.get('output', 0), u.get('output_tokens') or 0)
                    e['cache_read'] = max(e.get('cache_read', 0), u.get('cache_read_input_tokens') or 0)
                    e['cache_write_5m'] = max(e.get('cache_write_5m', 0), w5m)
                    e['cache_write_1h'] = max(e.get('cache_write_1h', 0), w1h)
                    e['tools'] = e.get('tools', 0) + tools
            state.pending[path] = new
    state.pending['claude-sdk-v1'] = {'done': 1}  # saved with the offsets, after a good push
    state.pending['claude-queued-v1'] = {'done': 1}
    state.pending['claude-names-v1'] = {'done': 1}
    state.pending['claude-effort-v1'] = {'done': 1}
    return events


def ev(machine, agent, session, ctx, t, kind, **kw):
    e = {'machine': machine, 'agent': agent, 'session': f'{agent}:{session}',
         'project': project_of(ctx.get('cwd')), 'ts': round(t, 3), 'kind': kind}
    e.update(kw)
    return e


# ---------------------------------------------------------------- Codex

CODEX_SUB_RE = re.compile(rb'"(?:parent_thread_id|thread_source"\s*:\s*"subagent)')


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
        if redo and state.get(path):
            with open(path, 'rb') as f:
                if CODEX_SUB_RE.search(f.readline()):
                    state.set(path, {})
        lines, new = read_new_lines(path, state)
        if new is None:
            continue
        ctx = new['ctx']
        for o in lines:
            t = parse_ts(o.get('timestamp'))
            p = o.get('payload') or {}
            typ, ptyp = o.get('type'), p.get('type')
            if typ == 'session_meta':
                own = p.get('id') or p.get('session_id')
                parent = p.get('parent_thread_id') or (p.get('session_id') if p.get('thread_source') == 'subagent' else None)
                ctx['own'], ctx['sid'] = own, parent or own
                ctx['sub'] = int(bool(parent) and parent != own)  # its calls count toward the parent session
                ctx['cwd'] = p.get('cwd')
                continue
            if typ == 'turn_context':
                ctx['model'] = p.get('model') or ctx.get('model')
                ctx['cwd'] = p.get('cwd') or ctx.get('cwd')
                continue
            sid = ctx.get('sid') or os.path.basename(path)
            own = ctx.get('own') or sid  # event ids stay per file, so a parent's and a subagent's never collide
            if t is None:
                continue
            if typ == 'response_item' and ptyp in ('function_call', 'custom_tool_call', 'local_shell_call'):
                ctx['tools'] = ctx.get('tools', 0) + 1
            elif typ == 'response_item' and ptyp == 'message' and p.get('role') == 'user':
                text = '\n'.join(c.get('text', '') for c in p.get('content') or [] if isinstance(c, dict)).strip()
                # Codex logs its own action reviews ("The following is the Codex agent history…") as user turns.
                if text and not ctx.get('sub') and not text.startswith(('<', '# AGENTS.md', 'The following is the Codex agent history')):
                    events.append(ev(machine, 'Codex', sid, ctx, t, 'prompt',
                                     id=f'codex-u:{own}:{o.get("ordinal", t)}', text=clip(codex_request(text))))
            elif typ == 'event_msg' and ptyp == 'token_count':
                info = p.get('info') or {}
                total = (info.get('total_token_usage') or {}).get('total_tokens')
                last = info.get('last_token_usage') or {}
                if not last or total == ctx.get('last_total'):
                    continue  # Codex repeats the same count after some events
                ctx['last_total'] = total
                cached = last.get('cached_input_tokens') or 0
                events.append(ev(machine, 'Codex', sid, ctx, t, 'call',
                                 id=f'codex:{own}:{o.get("ordinal", t)}', model=ctx.get('model') or 'codex (unknown model)',
                                 subagent=ctx.get('sub', 0),
                                 input=max(0, (last.get('input_tokens') or 0) - cached), cache_read=cached,
                                 cache_write_5m=last.get('cache_write_input_tokens') or 0,
                                 output=last.get('output_tokens') or 0,
                                 reasoning=last.get('reasoning_output_tokens') or 0,
                                 tools=ctx.pop('tools', 0)))
        state.pending[path] = new
    state.pending['codex-subagents-v1'] = {'done': 1}  # saved with the offsets, after a good push
    return events


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
        prompts = {}
        for o in lines:
            upd = (o.get('params') or {}).get('update') or {}
            meta = o.get('_meta') or {}
            kind = upd.get('sessionUpdate')
            t = parse_ts(meta.get('agentTimestampMs') or o.get('timestamp'))
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
                usage = upd.get('usage') or {}
                per_model = usage.get('modelUsage') or {'grok (unknown model)': usage}
                tools = ctx.pop('tools', 0)
                for model, mu in per_model.items():
                    cached = mu.get('cachedReadTokens') or 0
                    events.append(ev(machine, 'Grok', sid, ctx, t, 'call',
                                     id=f'grok:{sid}:{upd.get("prompt_id")}:{model}', model=model,
                                     input=max(0, (mu.get('inputTokens') or 0) - cached), cache_read=cached,
                                     cache_write_5m=mu.get('cacheCreationTokens') or 0,
                                     output=mu.get('outputTokens') or 0,
                                     reasoning=mu.get('reasoningTokens') or 0,
                                     calls=mu.get('modelCalls') or 1, tools=tools,
                                     cost_reported=(mu['costUsdTicks'] / 1e10) if mu.get('costUsdTicks') is not None else None))
                    tools = 0
        state.pending[path] = new
    return events


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
    key = db_path
    st = state.get(key)
    since = st.get('since', 0)
    uri = 'file:' + urllib.parse.quote(db_path.replace('\\', '/')) + '?mode=ro'
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as e:
        print(f'opencode: {e}', file=sys.stderr)
        return []
    events = []
    newest = since
    try:  # V1 storage (written until OpenCode 2): message + part, sessions in session
        rows = con.execute(
            'SELECT m.id, m.session_id, m.time_updated, m.data, s.directory, s.parent_id '
            'FROM message m JOIN session s ON s.id = m.session_id '
            'WHERE m.time_updated > ? ORDER BY m.time_updated', (since,)).fetchall()
        parts = {}
        tools = {}
        ids = [r[0] for r in rows]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ','.join('?' * len(chunk))
            for mid, data in con.execute(f'SELECT message_id, data FROM part WHERE message_id IN ({q})', chunk):
                try:
                    d = json.loads(data)
                except ValueError:
                    continue
                if d.get('type') == 'text' and not d.get('synthetic'):
                    parts.setdefault(mid, []).append(d.get('text') or '')
                elif d.get('type') == 'tool':
                    tools[mid] = tools.get(mid, 0) + 1
        for mid, sid, updated, data, directory, parent in rows:
            newest = max(newest, updated)
            try:
                d = json.loads(data)
            except ValueError:
                continue
            t = parse_ts(((d.get('time') or {}).get('created')))
            if t is None:
                continue
            if mid.startswith('msg_') and opencode_copy(mid, t):
                continue  # already counted in the session it was forked from
            ctx = {'cwd': ((d.get('path') or {}).get('cwd')) or directory}
            session = parent or sid
            if d.get('role') == 'user' and not parent:
                text = '\n'.join(parts.get(mid, [])).strip()
                if text and not text.startswith('<system-reminder>'):
                    events.append(ev(machine, 'OpenCode', session, ctx, t, 'prompt', id='opencode-u:' + mid, text=clip(text)))
            elif d.get('role') == 'assistant':
                tok = d.get('tokens') or {}
                cache = tok.get('cache') or {}
                events.append(ev(machine, 'OpenCode', session, ctx, t, 'call', id='opencode:' + mid,
                                 model=d.get('modelID') or 'unknown', subagent=int(bool(parent)),
                                 input=tok.get('input') or 0, output=tok.get('output') or 0,
                                 reasoning=tok.get('reasoning') or 0, cache_read=cache.get('read') or 0,
                                 cache_write_5m=cache.get('write') or 0, tools=tools.get(mid, 0),
                                 cost_reported=d.get('cost')))
    except sqlite3.OperationalError as e:
        if 'no such table' not in str(e):
            print(f'opencode: {e}', file=sys.stderr)
    try:  # V2 storage: sessions in session_v2, messages in session_message (text inline; no part rows)
        rows = con.execute(
            "SELECT m.id, m.session_id, m.type, m.time_updated, m.data, s.directory, s.parent_id "
            "FROM session_message m JOIN session_v2 s ON s.id = m.session_id "
            "WHERE m.type IN ('user', 'assistant') AND m.time_updated > ? "
            "ORDER BY m.time_updated", (since,)).fetchall()
        for mid, sid, mtype, updated, data, directory, parent in rows:
            newest = max(newest, updated)
            try:
                d = json.loads(data)
            except ValueError:
                continue
            t = parse_ts(((d.get('time') or {}).get('created')))
            if t is None:
                continue
            if mid.startswith('msg_') and opencode_copy(mid, t):
                continue  # already counted in the session it was forked from
            ctx = {'cwd': directory}
            session = parent or sid
            if mtype == 'user' and not parent:
                # V1 kept the typed text in `part` rows; V2 puts it inline and gives
                # synthetic/system/compaction rows their own `type`s, so nothing to sift.
                text = (d.get('text') or '').strip()
                if text and not text.startswith('<system-reminder>'):
                    events.append(ev(machine, 'OpenCode', session, ctx, t, 'prompt', id='opencode-u:' + mid, text=clip(text)))
            elif mtype == 'assistant':
                tok = d.get('tokens') or {}
                cache = tok.get('cache') or {}
                tools = sum(1 for c in (d.get('content') or []) if isinstance(c, dict) and c.get('type') == 'tool')
                events.append(ev(machine, 'OpenCode', session, ctx, t, 'call', id='opencode:' + mid,
                                 model=(d.get('model') or {}).get('id') or 'unknown', subagent=int(bool(parent)),
                                 input=tok.get('input') or 0, output=tok.get('output') or 0,
                                 reasoning=tok.get('reasoning') or 0, cache_read=cache.get('read') or 0,
                                 cache_write_5m=cache.get('write') or 0, tools=tools,
                                 cost_reported=d.get('cost')))
    except sqlite3.OperationalError as e:
        if 'no such table' not in str(e):
            print(f'opencode: {e}', file=sys.stderr)
    con.close()
    state.pending[key] = {'since': newest}
    return events


# ---------------------------------------------------------------- Antigravity

def collect_antigravity(state, machine, brain_roots):
    parent_of = {}
    for root in brain_roots:
        for f in glob.glob(os.path.join(root, '*', '.system_generated', 'subagents', '*.json')):
            parent_of[os.path.splitext(os.path.basename(f))[0]] = f.split(os.sep + '.system_generated')[0].rsplit(os.sep, 1)[-1]
    events = []
    for root in brain_roots:
        for brain in glob.glob(os.path.join(root, '*')):
            logs = os.path.join(brain, '.system_generated', 'logs')
            path = os.path.join(logs, 'transcript_full.jsonl')
            if not os.path.exists(path):
                path = os.path.join(logs, 'transcript.jsonl')
            if not os.path.exists(path):
                continue
            bid = os.path.basename(brain)
            session = parent_of.get(bid, bid)
            lines, new = read_new_lines(path, state)
            if new is None:
                continue
            ctx = new['ctx']
            for o in lines:
                t = parse_ts(o.get('created_at'))
                if t is None:
                    continue
                for tc in o.get('tool_calls') or []:
                    cwd = str(((tc or {}).get('args') or {}).get('Cwd') or '').strip('"')
                    if cwd and not ctx.get('cwd'):
                        ctx['cwd'] = cwd
                step = o.get('step_index', t)
                if o.get('type') == 'USER_INPUT' and o.get('source') == 'USER_EXPLICIT' and bid not in parent_of:
                    raw = str(o.get('content') or '')
                    m = re.search(r'<USER_REQUEST>\s*(.*?)\s*(?:</USER_REQUEST>|$)', raw, re.S)
                    text = m.group(1) if m else re.sub(r'<([A-Z_]+)>[\s\S]*?</\1>', '', raw)
                    if text.strip():
                        events.append(ev(machine, 'Antigravity', session, ctx, t, 'prompt',
                                         id=f'antigravity-u:{bid}:{step}', text=clip(text)))
                elif o.get('type') == 'PLANNER_RESPONSE':
                    events.append(ev(machine, 'Antigravity', session, ctx, t, 'call',
                                     id=f'antigravity:{bid}:{step}', model=o.get('model') or 'gemini (unreported)',
                                     subagent=int(bid in parent_of), tools=len(o.get('tool_calls') or [])))
            state.pending[path] = new
    return events


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
        ctx = new['ctx']
        for o in lines:
            t = parse_ts(o.get('recorded_at'))
            if t is None:
                continue
            pt = o.get('payload_type') or ''
            payload = o.get('payload') or {}
            oid = o.get('id') or f'{sid}:{t}'  # a record without an id must not collapse into one event
            if not ctx.get('cwd') and pt in ('runtime.session.metadata', 'session.opened.observed'):
                ctx['cwd'] = find_key(payload, ('cwd', 'workspace_root', 'working_directory'))
            if pt == 'runtime.user_intent.accepted':
                text = ' '.join(b.get('text', '') for b in payload.get('refill_blocks') or [] if isinstance(b, dict))
                if text.strip():
                    events.append(ev(machine, 'Muse', sid, ctx, t, 'prompt', id=f'muse-u:{oid}', text=clip(text)))
            elif pt == 'tool_batch.effect.started':
                ctx['tools'] = ctx.get('tools', 0) + 1
            elif pt == 'runtime.session':
                e = payload.get('event') or {}
                if e.get('kind') == 'model_completed':
                    u = e.get('usage') or {}
                    cached = (u.get('cache_read_tokens') or u.get('cached_tokens') or 0)
                    events.append(ev(machine, 'Muse', sid, ctx, t, 'call', id=f'muse:{oid}',
                                     model=e.get('model') or 'muse (unknown model)',
                                     input=max(0, (u.get('input_tokens') or 0) - cached), cache_read=cached,
                                     cache_write_5m=u.get('cache_write_tokens') or 0,
                                     output=u.get('output_tokens') or 0, reasoning=u.get('reasoning_tokens') or 0,
                                     tools=ctx.pop('tools', 0)))
        state.pending[path] = new
    return events


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
                print(e.code, file=sys.stderr)
        nxt = began + args.every
        if nxt - start >= args.duration:
            return
        time.sleep(max(0, nxt - time.monotonic()))


def run(args):
    state = State(args.state)
    state.pending = {}
    events, counts = [], {}
    for name, fn in sources():
        try:
            got = fn(state, args.machine)
        except Exception as e:  # one broken source must not block the others
            print(f'{name}: {type(e).__name__}: {e}', file=sys.stderr)
            got = []
        counts[name] = len(got)
        events.extend(got)

    # What every agent is doing right now. Sent on each run (every 10 s), so the hub's
    # /api/live is never older than that for a machine that is up.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import live
        snap = live.snapshot()
    except Exception as e:  # history still goes out when the live scan breaks
        print(f'live: {type(e).__name__}: {e}', file=sys.stderr)
        snap = None
    # The machine itself and its scheduled jobs (collector/host.py), for the hub's machines view
    # and its alarms: a job that fails or stops running is told from the hub, not from this box.
    try:
        import host
        me = host.report(os.path.join(os.path.dirname(os.path.abspath(args.state)), 'host-cache.json'),
                         hub=args.hub, machine=args.machine)
    except Exception as e:
        print(f'host: {type(e).__name__}: {e}', file=sys.stderr)
        me = None
    # Which background job started each running agent process, so the hub can put every run's model,
    # effort and cost on its job (http://beacon/ai).
    if me is not None:
        try:
            import llm
            me['llm_runs'] = llm.runs(live.list_processes())
        except Exception as e:
            print(f'llm runs: {type(e).__name__}: {e}', file=sys.stderr)

    if args.dry_run:
        print(json.dumps(counts))
        if snap:
            for s in snap['sessions']:
                print(f"live {s['state']:<7} {s['agent']:<11} {s['project']:<24} {s['doing'] or ''}"[:160])
        for e in events[-3:]:
            print(json.dumps(e)[:300])
        for n in list(state.names.values())[-5:]:
            print('name', json.dumps(n, ensure_ascii=False))
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


if __name__ == '__main__':
    main()
