"""Tasks: what needs doing, by him or by an agent, in the same database as the sessions.

A task lives in the hub so it can be joined to the agent sessions that worked on it, with the
same project names and session ids the rest of the hub uses (docs/specs/2026-10-06-tasks-design.md).
Only what the ClickUp data showed in use: title, markdown body, status, priority, due date,
tags, comments, plus provenance and session links.
"""
import datetime
import json
import re
import sqlite3
import time

from settings import OWNER_ID, OWNER_NAME

try:
    from .progress import steps_of
    from .store import fold
except ImportError:  # run as a script from hub/
    from progress import steps_of
    from store import fold

STATUSES = ('todo', 'doing', 'done', 'dropped')
OPEN = ('todo', 'doing')
CLOSED = ('done', 'dropped')
STALE_AFTER = 14 * 86400   # open and untouched this long: ask keep or drop
SETTLE_SLACK = 30 * 60     # a task linked this long before a session's last reply belongs to that reply
TITLE_MAX = 120
DUE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$')
SENTENCE_END = re.compile(r'[.!?](?=\s)')
DEFAULT_PROJECT = 'personal'

# Agents write titles he reads at a glance on his phone and other agents pick up to work:
# the outcome with a checkable result, on any day (2026-11-22). Plans, timing and
# references belong in the body and due; his decisions belong in questions, not tasks.
AGENT_TITLE_MAX = 70
TITLE_JUNK = re.compile(r'[()\[\]]|https?://|\bPR\s*#?\d|#\d|\b(tonight|tomorrow|today|by morning'
                        r'|needs your ok|for ' + re.escape(OWNER_NAME.lower()) + r'|dnes|zítra|večer|do rána)\b', re.I)
DECISION_TITLE = re.compile(r'^\s*(decide|rozhodnout|rozhodni)\b', re.I)
BODY_SECTIONS = (('Goal', re.compile(r'^#{1,6}\s*goal\b', re.I | re.M)),
                 ('Done-test', re.compile(r'^#{1,6}\s*done[- ]?(test|when)\b', re.I | re.M)),
                 ('Steps', re.compile(r'^#{1,6}\s*steps\b', re.I | re.M)))


def agent_problem(fields, new):
    """Why an agent's task write is refused, or None. The owner's own writes are never checked."""
    title = fields.get('title')
    if isinstance(title, str):
        t = title.strip()
        bad = TITLE_JUNK.search(t)
        if DECISION_TITLE.search(t):
            return ('title: a decision of his is a question, not a task: '
                    '`tasks ask "…" -o "pick :: why" --task ID` on the task it unblocks')
        if len(t) > AGENT_TITLE_MAX or bad:
            why = f'"{bad.group(0)}"' if bad else f'{len(t)} chars (max {AGENT_TITLE_MAX})'
            return (f'title: {why}. Title = the outcome with a result anyone can check, readable on any day: '
                    '"Sleep Like CEO: 5 downloads from 3 TikTok slideshows". No (…), PR/# numbers, URLs, '
                    '"tonight/tomorrow" or "needs your OK": steps go in the body, timing in due')
    if new and not fields.get('source'):
        body = fields.get('body') or ''
        missing = [h for h, rx in BODY_SECTIONS if not rx.search(body)]
        if missing:
            return ('body: missing ' + ', '.join(f'"## {h}"' for h in missing) + '. Body = ## Goal (outcome + why), '
                    f'## Done-test (checkable results + how to check), ## Steps (numbered, 🤖 agent / 👤 {OWNER_NAME}), '
                    'then ## Decided, ## Not doing, ## Context as needed; see the tasks skill')
    return None


SCHEMA = '''
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    project TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    priority INTEGER,
    due TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT UNIQUE,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    closed_at REAL
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, updated_at);
CREATE TABLE IF NOT EXISTS task_sessions (
    task_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    linked_at REAL NOT NULL,
    PRIMARY KEY (task_id, session_id)
);
CREATE TABLE IF NOT EXISTS task_comments (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    at REAL NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS task_comments_task ON task_comments(task_id, at);
'''


def split_paste(text):
    """A pasted or dictated text -> (title, body). The first line is the title; a first line
    too long for one (a dictation with no line breaks) gives its first sentence, or a cut, and
    then the body keeps the whole text so nothing said is lost."""
    text = (text or '').strip()
    first, _, rest = text.partition('\n')
    first = first.strip()
    if len(first) <= TITLE_MAX:
        return first, rest.strip()
    m = SENTENCE_END.search(first, 0, TITLE_MAX)
    if m:
        return first[:m.end()], text
    cut = first.rfind(' ', 0, TITLE_MAX - 1)
    return first[:cut if cut > 40 else TITLE_MAX - 1].rstrip() + '…', text


def check_due(v):
    if v in (None, ''):
        return None
    if not isinstance(v, str) or not DUE_RE.match(v):
        raise ValueError('due: expected YYYY-MM-DD or YYYY-MM-DDTHH:MM')
    try:
        datetime.datetime.fromisoformat(v)
    except ValueError:
        raise ValueError('due: not a real date') from None
    return v


def check_priority(v):
    if v in (None, ''):
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 4:
        raise ValueError('priority: expected 1 (urgent) to 4 (low)')
    return v


def check_tags(v):
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(t, str) and t.strip() for t in v):
        raise ValueError('tags: expected a list of names')
    out = []
    for t in v:
        t = t.strip().lower()
        if t not in out:
            out.append(t)
    return out


class Tasks:
    def __init__(self, store):
        self.store = store
        with store.connect() as con:
            con.executescript(SCHEMA)
            if 'step' not in {r[1] for r in con.execute('PRAGMA table_info(tasks)')}:
                con.execute('ALTER TABLE tasks ADD COLUMN step INTEGER')  # the step of ## Steps its agent is on
        store.on_live = self.settle
        self.on_note = None  # set by questions.Questions: a ⏳ note asks, his note answers
        self.on_close = None  # set by questions.Questions: a closed task's open questions are withdrawn

    def settle(self, machines):
        """A waiting session whose last reply filed tasks for him, all of them now closed, is
        not waiting any more: he did what it asked, wherever he did it. Mark it done."""
        waiting = [x for m in machines for x in m['sessions'] if x.get('state') == 'waiting']
        if not waiting:
            return
        ids = [x['id'] for x in waiting]
        with self.store.connect() as con:
            rows = con.execute(
                f'SELECT l.session_id, l.linked_at, t.status FROM task_sessions l JOIN tasks t ON t.id = l.task_id '
                f'WHERE l.session_id IN ({", ".join("?" * len(ids))})', ids).fetchall()
        links = {}
        for r in rows:
            links.setdefault(r['session_id'], []).append(r)
        for x in waiting:
            got = links.get(x['id'])
            if not got or any(r['status'] in OPEN for r in got):
                continue
            if max(r['linked_at'] for r in got) >= (x.get('last') or 0) - SETTLE_SLACK:
                x['state'] = 'done'
                x['settled_by'] = 'tasks'

    # ------------------------------------------------------------ helpers

    def project(self, name):
        name = (name or '').strip() if isinstance(name, str) else ''
        if not name:
            return DEFAULT_PROJECT
        return self.store.canonical_project(name)

    def today(self):
        return datetime.datetime.now(self.store.tz).date().isoformat()

    @staticmethod
    def row(r):
        t = dict(r)
        t['tags'] = json.loads(t['tags'] or '[]')
        return t

    def clean(self, fields, new):
        """Validated column values for the given fields; ValueError('<field>: <why>')."""
        out = {}
        if new or 'title' in fields or 'body' in fields:
            title, body = fields.get('title'), fields.get('body')
            if new and not (isinstance(title, str) and title.strip()) and isinstance(body, str):
                title, body = split_paste(body)
            if title is not None or new:
                if not isinstance(title, str) or not title.strip():
                    raise ValueError('title: required')
                if '\n' in title.strip() or len(title.strip()) > TITLE_MAX:
                    head, rest = split_paste(title)
                    title = head
                    body = '\n\n'.join(x for x in (rest, body if isinstance(body, str) else '') if x)
                out['title'] = title.strip()
            if body is not None or new:
                if body is not None and not isinstance(body, str):
                    raise ValueError('body: expected text')
                out['body'] = (body or '').strip()
        if 'project' in fields or new:
            out['project'] = self.project(fields.get('project'))
        if 'status' in fields or new:
            s = fields.get('status') or 'todo'
            if s not in STATUSES:
                raise ValueError('status: expected one of ' + ', '.join(STATUSES))
            out['status'] = s
        if 'priority' in fields:
            out['priority'] = check_priority(fields['priority'])
        if 'due' in fields:
            out['due'] = check_due(fields['due'])
        if 'tags' in fields or new:
            out['tags'] = json.dumps(check_tags(fields.get('tags')))
        if 'step' in fields:
            v = fields['step']
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
                raise ValueError('step: expected the number of a step in ## Steps (1, 2, …)')
            out['step'] = v
        if 'source' in fields and new:
            src = fields['source']
            out['source'] = str(src)[:200] if src else None
        return out

    # ------------------------------------------------------------ writes

    def create(self, fields, author=OWNER_ID):
        if not isinstance(fields, dict):
            raise ValueError('body: expected a JSON object')
        cols = self.clean(fields, new=True)
        now = time.time()
        cols['created_at'] = float(fields.get('created_at') or now)
        cols['updated_at'] = float(fields.get('updated_at') or cols['created_at'])
        cols['closed_at'] = now if cols['status'] in CLOSED else None
        with self.store.connect() as con:
            if cols.get('source'):
                hit = con.execute('SELECT id FROM tasks WHERE source = ?', (cols['source'],)).fetchone()
                if hit:
                    return self.get(hit['id'])
            names = list(cols)
            cur = con.execute(f'INSERT INTO tasks ({", ".join(names)}) VALUES ({", ".join("?" * len(names))})',
                              [cols[n] for n in names])
            tid = cur.lastrowid
        return self.get(tid)

    def update(self, tid, fields):
        if not isinstance(fields, dict):
            raise ValueError('body: expected a JSON object')
        with self.store.connect() as con:
            cur = con.execute('SELECT status, closed_at, body FROM tasks WHERE id = ?', (tid,)).fetchone()
        if not cur:
            return None
        if 'title' in fields and 'body' not in fields:
            # A title too long or multi-line spills into the body: keep what the body held.
            fields = dict(fields, body=cur['body'])
            if not isinstance(fields.get('title'), str) or not split_paste(fields['title'])[1]:
                del fields['body']
        cols = self.clean(fields, new=False)
        now = time.time()
        with self.store.connect() as con:
            if 'status' in cols:
                if cols['status'] in CLOSED:
                    cols['closed_at'] = cur['closed_at'] if cur['status'] in CLOSED and cur['closed_at'] else now
                else:
                    cols['closed_at'] = None
            cols['updated_at'] = now  # an empty update still counts: "keep" on a stale task
            con.execute(f'UPDATE tasks SET {", ".join(n + " = ?" for n in cols)} WHERE id = ?',
                        [*cols.values(), tid])
        if self.on_close and cols.get('status') in CLOSED:  # after the commit, like on_note
            self.on_close(tid)
        return self.get(tid)

    def comment(self, tid, text, author=OWNER_ID, at=None):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('text: required')
        with self.store.connect() as con:
            if not con.execute('SELECT 1 FROM tasks WHERE id = ?', (tid,)).fetchone():
                return None
            at = float(at or time.time())
            cur = con.execute('INSERT INTO task_comments (task_id, at, author, text) VALUES (?, ?, ?, ?)',
                              (tid, at, str(author or OWNER_ID)[:80], text.strip()))
            con.execute('UPDATE tasks SET updated_at = max(updated_at, ?) WHERE id = ?', (at, tid))
            note = {'id': cur.lastrowid, 'task_id': tid, 'at': at, 'author': author, 'text': text.strip()}
        if self.on_note:  # after the commit: it writes through a connection of its own
            self.on_note(tid, note)
        return note

    def link(self, tid, session_id):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError('session_id: required')
        with self.store.connect() as con:
            if not con.execute('SELECT 1 FROM tasks WHERE id = ?', (tid,)).fetchone():
                return None
            con.execute('INSERT OR IGNORE INTO task_sessions (task_id, session_id, linked_at) VALUES (?, ?, ?)',
                        (tid, session_id.strip()[:200], time.time()))
        return self.get(tid)

    # ------------------------------------------------------------ reads

    def get(self, tid):
        with self.store.connect() as con:
            r = con.execute('SELECT * FROM tasks WHERE id = ?', (tid,)).fetchone()
            if not r:
                return None
            t = self.row(r)
            t['comments'] = [dict(c) for c in con.execute(
                'SELECT id, at, author, text FROM task_comments WHERE task_id = ? ORDER BY at, id', (tid,))]
            t['sessions'] = [dict(s) for s in con.execute(
                'SELECT session_id, linked_at FROM task_sessions WHERE task_id = ? ORDER BY linked_at', (tid,))]
            t['waiting_on_him'] = tid in self.waiting_on_him(con)
        t['plan'] = [{'label': s['label'], 'owner': s['owner'], 'done': s['done']} for s in steps_of(t['body'])]
        return t

    @staticmethod
    def waiting_on_him(con):
        """Tasks with a blocking question open: the work stopped until he decides (task 1776). Derived, not a
        status, so the queue, the sweep and the pages keep their four states."""
        try:
            return {r[0] for r in con.execute(
                "SELECT DISTINCT task_id FROM questions WHERE status = 'open' AND blocking = 1 AND task_id IS NOT NULL")}
        except sqlite3.OperationalError:  # no questions table (a store without hub/questions.py)
            return set()

    def list(self, status='open', project=None, q=None, due=None, stale=False, limit=500, session=None):
        where, params = [], []
        if status in (None, '', 'open'):
            statuses = OPEN
        elif status == 'all':
            statuses = STATUSES
        else:
            statuses = tuple(s for s in str(status).split(',') if s in STATUSES)
            if not statuses:
                raise ValueError('status: expected open, all or ' + ', '.join(STATUSES))
        where.append(f'status IN ({", ".join("?" * len(statuses))})')
        params += statuses
        if project:
            where.append('project = ?')
            params.append(self.project(project))
        if due == 'today':
            where.append('due IS NOT NULL AND substr(due, 1, 10) <= ?')
            params.append(self.today())
        elif due:
            raise ValueError('due: only "today" is a filter')
        if session:  # the tasks one session filed or took (the Stop hook's question)
            where.append('id IN (SELECT task_id FROM task_sessions WHERE session_id = ?)')
            params.append(session)
        if stale:
            where.append('updated_at < ?')
            params.append(time.time() - STALE_AFTER)
        sql = f'SELECT * FROM tasks WHERE {" AND ".join(where)} ORDER BY updated_at DESC, id DESC'
        with self.store.connect() as con:
            rows = [self.row(r) for r in con.execute(sql, params)]
        if q:
            needle = fold(q)
            rows = [t for t in rows if needle in fold(t['title'] + '\n' + t['body'])]
        rows = rows[:max(1, min(int(limit), 2000))]
        self.annotate(rows)
        return rows

    def annotate(self, rows):
        """Each listed task's linked session ids and its latest note: enough for a list to show
        which agent is on it (joined with /api/live) and what it last said (a PR, a question)."""
        if not rows:
            return
        by_id = {t['id']: t for t in rows}
        for t in rows:
            t['sessions'], t['last_note'] = [], None
        with self.store.connect() as con:
            waiting = self.waiting_on_him(con)
            for t in rows:
                t['waiting_on_him'] = t['id'] in waiting
            for r in con.execute('SELECT task_id, session_id FROM task_sessions ORDER BY linked_at'):
                if r['task_id'] in by_id:
                    by_id[r['task_id']]['sessions'].append(r['session_id'])
            for r in con.execute('SELECT task_id, author, at, text FROM task_comments c WHERE id = '
                                 '(SELECT id FROM task_comments WHERE task_id = c.task_id ORDER BY at DESC, id DESC LIMIT 1)'):
                if r['task_id'] in by_id:
                    by_id[r['task_id']]['last_note'] = {'author': r['author'], 'at': r['at'], 'text': r['text'][:400]}

    def projects(self):
        with self.store.connect() as con:
            rows = con.execute("SELECT project, sum(status IN ('todo', 'doing')) AS open FROM tasks "
                               "GROUP BY project").fetchall()
        return sorted(({'project': r['project'], 'open': r['open'] or 0} for r in rows),
                      key=lambda p: (-p['open'], p['project']))

