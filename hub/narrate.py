"""What each working agent is doing, in one sentence for its boss (2026-11-22: "I need to see real
work", not "$ git pull" or "Editing store.py").

Every SAMPLE_SEC the narrator reads the live snapshot and keeps each working session's recent tool lines.
Every NARRATE_SEC, the sessions whose lines moved since their last sentence go to Haiku in one batch
("Comparing prices of Czech Excel courses"). /api/live then carries that sentence as `now`.

Haiku runs through `claude -p` on his subscription, like titles.py: batched (up to BATCH sessions per
call), capped at DAILY_CALLS calls a day, no session log of its own. Kept in memory only: a restart
starts the sentences over within a minute. No model or bad output: no `now`, the page shows the
activity it reads from the tool line instead.
"""
import collections
import logging
import os
import re
import sqlite3
import threading
import time

from titles import ask_haiku
from settings import OWNER_NAME

log = logging.getLogger('beacon.narrate')

SAMPLE_SEC = 10
NARRATE_SEC = 60
QUIET_SEC = 600      # mid-turn but silent this long: stalled, nothing new to say
BATCH = 12
LINES = 12
ROLES = ('research', 'build', 'test', 'deploy', 'plan', 'look')
DAILY_CALLS = int(os.environ.get('NARRATE_DAILY_CALLS', '400'))

PROMPT = '''Below are AI agents working for {owner}, a product owner who is not a programmer. For each one you
get its job, the last thing he asked it, and its most recent actions (oldest first, the last one is
happening now). Write ONE short sentence of 4 to 10 words saying what it is doing right now, the way a
team lead would tell the boss: present tense, start with a verb, name the real thing being worked on
(the product, page, topic, client, machine), never tools, commands, files or code words.
Good: "Comparing prices of Czech Excel courses", "Putting the new Team page live", "Reading what
marketers say about cold email", "Checking the iPhone app still builds", "Freeing disk space on the
Windows PC". If the actions are in Czech, still answer in English.
Also pick its role right now, the one word that fits that sentence: research (reading, comparing, searching),
build (making or changing the thing), test (checking it works), deploy (putting it live, publishing, installing),
plan (deciding, writing up, filing next steps) or look (investigating a problem).
Reply with JSON only: {{"1": {{"now": "sentence", "role": "research"}}, "2": ...}}

{items}'''


def tidy(text):
    t = re.sub(r'\s+', ' ', str(text or '')).strip().strip('"\'').rstrip('.').strip()
    return t[:90] if len(t) >= 4 else None


class Narrator:
    def __init__(self, store, model=ask_haiku, background=True):
        self.store = store
        self.model = model
        self.lock = threading.Lock()
        self.seen = {}      # session id -> {'lines': deque, 'prompt', 'title', 'project', 'at'}
        self.said = {}      # session id -> (sentence, at, signature of the lines it was written from, role)
        self.calls = []     # times of model calls in the last day
        self.last_run = 0
        if background:
            threading.Thread(target=self.loop, daemon=True).start()

    def observe(self, machines):
        """Keeps the recent tool lines of every working session in a live snapshot's machines."""
        now = time.time()
        with self.lock:
            for m in machines:
                for x in m.get('sessions') or ():
                    if x.get('state') != 'working' or (x.get('idle_s') or 0) >= QUIET_SEC:
                        continue
                    s = self.seen.setdefault(x['id'], {'lines': collections.deque(maxlen=LINES)})
                    line = (x.get('doing') or '').strip()
                    if line and (not s['lines'] or s['lines'][-1] != line):
                        s['lines'].append(line[:200])
                    s.update(prompt=x.get('prompt') or s.get('prompt'), title=x.get('title') or s.get('title'),
                             project=x.get('project'), at=now)
            for sid in [k for k, s in self.seen.items() if now - s['at'] > 3600]:
                self.seen.pop(sid, None)
                self.said.pop(sid, None)

    def job_titles(self, machines):
        """The job is the task a session works (his own words), not its auto name ("projects-e4")."""
        ids = [x['id'] for m in machines for x in m.get('sessions') or () if x.get('state') == 'working']
        if not ids:
            return
        try:
            with self.store.connect() as con:
                rows = con.execute('SELECT l.session_id, t.title FROM task_sessions l JOIN tasks t ON t.id = l.task_id '
                                   f'WHERE l.session_id IN ({",".join("?" * len(ids))}) ORDER BY l.linked_at', ids).fetchall()
        except sqlite3.OperationalError:  # a hub without tasks tables
            return
        titles = dict(rows)
        for m in machines:
            for x in m.get('sessions') or ():
                if x['id'] in titles:
                    x['title'] = titles[x['id']]

    def annotate(self, live):
        """Sets `now` on each working session of an /api/live payload that has a sentence."""
        with self.lock:
            for m in live.get('machines') or ():
                for x in m.get('sessions') or ():
                    got = self.said.get(x.get('id'))
                    if got and x.get('state') == 'working':
                        x['now'] = got[0]
                        if got[3]:
                            x['role'] = got[3]
        return live

    def budget_left(self):
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 86400]
        return len(self.calls) < DAILY_CALLS

    def due(self):
        """Working sessions whose lines moved since their last sentence, most recent first."""
        now = time.time()
        with self.lock:
            out = []
            for sid, s in self.seen.items():
                if now - s['at'] > SAMPLE_SEC * 3 or not s['lines']:
                    continue
                sig = '\n'.join(list(s['lines'])[-4:])
                got = self.said.get(sid)
                if got and got[2] == sig:
                    continue
                out.append((sid, sig, dict(s, lines=list(s['lines']))))
            out.sort(key=lambda r: -r[2]['at'])
            return out[:BATCH]

    def run_once(self):
        batch = self.due()
        if not batch or not self.budget_left():
            return 0
        self.calls.append(time.time())
        items = []
        for i, (_, _, s) in enumerate(batch):
            acts = ' | '.join(s['lines'][-8:]).replace('\n', ' ')
            items.append(f'{i + 1}. job: {(s.get("title") or s.get("project") or "")[:100]}\n'
                         f'   asked: {(s.get("prompt") or "")[:200]}\n   actions: {acts[:900]}'.replace('\r', ' '))
        got = self.model(PROMPT.format(owner=OWNER_NAME, items='\n'.join(items))) or {}
        n = 0
        with self.lock:
            for i, (sid, sig, _) in enumerate(batch):
                one = got.get(str(i + 1))
                one = one if isinstance(one, dict) else {'now': one}
                text = tidy(one.get('now'))
                role = one.get('role') if one.get('role') in ROLES else None
                if text:
                    self.said[sid] = (text, time.time(), sig, role)
                    n += 1
        return n

    def loop(self):
        while True:
            try:
                machines = self.store.live_now()['machines']
                self.job_titles(machines)
                self.observe(machines)
                if time.time() - self.last_run >= NARRATE_SEC:
                    self.last_run = time.time()
                    self.run_once()
            except Exception:
                log.exception('loop failed')
            time.sleep(SAMPLE_SEC)
