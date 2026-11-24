"""Short, friendly session names for the usage page (2026-11-22: "these session names are
super unfriendly").

A session's name, first match wins:
  1. the name in its own log (custom-title: /rename, --name, or the tasks Stop hook / `tasks doing`,
     task 1789), which the collectors send; the same name `claude --resume` shows;
  2. the title of the task it worked (task_sessions): his own words for the outcome;
  3. Claude Code's own ai-title in its log, when it wrote one;
  4. a 2-5 word title Haiku wrote once from the session's first prompt, stored here.
     Sessions with the same first prompt in the same project (an eval batch) share one title.
Claude Code titles sessions the same way for its own picker (a Haiku call), but writes that
"ai-title" into only a few logs, so the hub does it itself.

Haiku runs through `claude -p` on his subscription, batched (up to BATCH prompts per call), in a
background thread, capped at DAILY_CALLS calls a day, and without saving its own session log
(it would show up on the page as a session). No model, timeout or bad output: no title, the page
falls back to the prompt.
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import traceback

MODEL = os.environ.get('TITLES_MODEL', 'claude-haiku-4-5')
CLAUDE = os.environ.get('TITLES_CLAUDE') or os.path.expanduser('~/.local/bin/claude')
BATCH = 30
DAILY_CALLS = int(os.environ.get('TITLES_DAILY_CALLS', '80'))

SCHEMA = '''
CREATE TABLE IF NOT EXISTS session_titles (
    key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS session_names (
    machine TEXT NOT NULL,
    session TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (machine, session)
);
'''

PROMPT = '''Name each AI coding-agent session below for a dashboard. Each line is the project and the
first message the agent got. Write a title of 2 to 5 words that says what the work was about, in
plain English, sentence case, like a to-do item: "Fix login redirect", "Review gate PR", "Grade code
reviews", "Daily analytics reading". No task numbers, file paths, quotes, emoji or trailing period.
If the message is in Czech, still answer in English.
Reply with JSON only: {{"1": "title", "2": "title", ...}}

{items}'''


def clean(prompt):
    return re.sub(r'^\[(scheduled|mid-run)\] ', '', prompt or '').strip()


def key_of(project, prompt):
    return hashlib.sha1(f'{project}\0{clean(prompt)[:300]}'.encode()).hexdigest()[:20]


def ask_haiku(prompt, timeout=120):
    try:
        r = subprocess.run([CLAUDE, '-p', prompt, '--model', MODEL, '--setting-sources', '', '--tools', '',
                            '--strict-mcp-config', '--no-chrome', '--no-session-persistence', '--output-format', 'json'],
                           capture_output=True, text=True, timeout=timeout, cwd='/tmp',
                           env={**os.environ, 'HOME': os.path.expanduser('~'), 'TASKS_HOOKS': '0'}, stdin=subprocess.DEVNULL)
        out = json.loads(r.stdout).get('result') or ''
        m = re.search(r'\{.*\}', out, re.S)
        return json.loads(m.group(0)) if m else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def tidy(title):
    t = re.sub(r'\s+', ' ', str(title or '')).strip().strip('"\'.').strip()
    return (t if len(t) <= 80 else t[:79].rsplit(' ', 1)[0] + '…') if 2 <= len(t) else None


class Titles:
    def __init__(self, store, model=ask_haiku, background=True):
        self.store = store
        self.model = model
        self.pending = {}  # key -> (project, prompt)
        self.lock = threading.Lock()
        self.calls = []  # times of model calls in the last day
        with store.connect() as con:
            con.executescript(SCHEMA)
        if background:
            threading.Thread(target=self.loop, daemon=True).start()

    def save(self, machine, names):
        """Names a collector read from session logs: [{session, title, source: custom|ai}]. A
        custom name is never replaced by an ai one."""
        rows = []
        for n in names if isinstance(names, list) else []:
            if not isinstance(n, dict) or n.get('source') not in ('custom', 'ai'):
                continue
            sess, title = n.get('session'), tidy(n.get('title'))
            if isinstance(sess, str) and sess and title:
                rows.append((str(machine)[:64], sess[:200], title, n['source'], time.time()))
        if rows:
            with self.store.connect() as con:
                con.executemany(
                    'INSERT INTO session_names (machine, session, title, source, at) VALUES (?, ?, ?, ?, ?) '
                    'ON CONFLICT(machine, session) DO UPDATE SET title = excluded.title, source = excluded.source, '
                    "at = excluded.at WHERE excluded.source = 'custom' OR session_names.source <> 'custom'", rows)
        return len(rows)

    def annotate(self, sessions):
        """Sets 'title' (and 'title_from': task or model) on each session dict that has one;
        queues the rest for the model."""
        if not sessions:
            return sessions
        ids = [s.get('id') for s in sessions if s.get('id')]
        keys = {id(s): key_of(s.get('project'), s.get('first_prompt')) for s in sessions if s.get('first_prompt')}
        with self.store.connect() as con:
            tasks = {}
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = ('SELECT l.session_id, t.title FROM task_sessions l JOIN tasks t ON t.id = l.task_id '
                     f'WHERE l.session_id IN ({",".join("?" * len(chunk))}) ORDER BY l.linked_at')
                try:
                    for sid, title in con.execute(q, chunk):
                        tasks[sid] = title  # the latest task wins
                except Exception:  # no tasks tables (a fresh hub): no task names
                    break
            names = {}
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                names.update(((m, sid), (title, source)) for m, sid, title, source in con.execute(
                    'SELECT machine, session, title, source FROM session_names '
                    f'WHERE session IN ({",".join("?" * len(chunk))})', chunk))
            ks = list(set(keys.values()))
            known = {}
            for i in range(0, len(ks), 500):
                chunk = ks[i:i + 500]
                known.update(con.execute(f'SELECT key, title FROM session_titles WHERE key IN ({",".join("?" * len(chunk))})', chunk))
        for s in sessions:
            name = names.get((s.get('machine'), s.get('id')))
            if name and name[1] == 'custom':
                # a name cut from its task's title ("… on all 4 machines at a") reads as the whole title
                full = tasks.get(s.get('id'))
                s['title'], s['title_from'] = (full, 'task') if full and full.startswith(name[0]) else (name[0], 'name')
                continue
            if s.get('id') in tasks:
                s['title'], s['title_from'] = tasks[s['id']], 'task'
                continue
            if name:
                s['title'], s['title_from'] = name[0], 'name'
                continue
            k = keys.get(id(s))
            if not k:
                continue
            if k in known:
                s['title'], s['title_from'] = known[k], 'model'
            else:
                with self.lock:
                    self.pending.setdefault(k, (s.get('project'), s.get('first_prompt')))
        return sessions

    def budget_left(self):
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 86400]
        return len(self.calls) < DAILY_CALLS

    def run_once(self):
        with self.lock:
            batch = list(self.pending.items())[:BATCH]
        if not batch or not self.budget_left():
            return 0
        self.calls.append(time.time())
        items = '\n'.join(f'{i + 1}. [{p}] {clean(q)[:400]}'.replace('\n', ' ') for i, (_, (p, q)) in enumerate(batch))
        got = self.model(PROMPT.format(items=items)) or {}
        rows = [(k, tidy(got.get(str(i + 1))), time.time()) for i, (k, _) in enumerate(batch)]
        rows = [r for r in rows if r[1]]
        if rows:
            with self.store.connect() as con:
                con.executemany('INSERT OR REPLACE INTO session_titles (key, title, at) VALUES (?, ?, ?)', rows)
        with self.lock:
            for k, _ in batch:
                self.pending.pop(k, None)  # a failed one comes back the next time a page asks
        return len(rows)

    def loop(self):
        while True:
            try:
                while self.run_once():
                    time.sleep(2)
            except Exception:
                traceback.print_exc()
            time.sleep(20)
