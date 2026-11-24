"""Where a running task stands: its own written steps, the one its agent marked as current, and a learned
time-left guess (task 1901).

The plan is the task's `## Steps` (numbered, 🤖 agent / 👤 owner); nothing here makes steps up. The agent
working the task marks the step it is on (`tasks step ID N`, the task's `step` column). Time counts only helper
work: the event bursts of the task's linked sessions (store.bursts_of), never nights or waits for the owner.

Time left = minutes per agent step (median over recently closed tasks with steps: the project's own once it has
MIN_SAMPLES, else everyone's) x the agent steps still ahead, times a correction learned from earlier guesses
(median of real/guessed over the last CORRECT_OVER graded ones, in the spirit of evidence-based scheduling).
Each task's first whole-task guess is kept; when the task closes it is graded against its real minutes, so the
next guess uses it and the Team page can say how far off the guesses run.
"""
import re
import statistics
import threading
import time
import traceback

try:
    from .store import bursts_of
except ImportError:  # run as a script from hub/
    from store import bursts_of

REFRESH_SEC = 600
HISTORY_SEC = 60 * 86400
MIN_SAMPLES = 5
CORRECT_OVER = 20
FILL_PER_ROUND = 60     # closed tasks measured per refresh, so a fresh box catches up without a long stall

SCHEMA = '''
CREATE TABLE IF NOT EXISTS task_effort (
    task_id INTEGER PRIMARY KEY,
    project TEXT,
    agent_steps INTEGER NOT NULL,
    active_min REAL NOT NULL,
    closed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS task_guesses (
    task_id INTEGER PRIMARY KEY,
    guess_min REAL NOT NULL,
    at REAL NOT NULL,
    actual_min REAL
);
'''

STEPS_HEAD = re.compile(r'^#{1,6}\s*steps\b.*$', re.I | re.M)
NEXT_HEAD = re.compile(r'^#{1,6}\s', re.M)
STEP_LINE = re.compile(r'^\s*(\d+)[.)]\s+(.+)$')
DONE_MARK = re.compile(r'^(✅|☑️|\[x\]|~~)', re.I)
OWNER_MARK = '👤'


def steps_of(body):
    """The numbered lines under ## Steps: [{'text', 'label', 'owner', 'done'}]; [] when the task has none."""
    m = STEPS_HEAD.search(body or '')
    if not m:
        return []
    rest = body[m.end():]
    nxt = NEXT_HEAD.search(rest)
    out = []
    for raw in (rest[:nxt.start()] if nxt else rest).splitlines():
        s = STEP_LINE.match(raw)
        if not s:
            continue
        text = s.group(2).strip()
        out.append({'text': text, 'label': label_of(text), 'owner': OWNER_MARK in text[:12],
                    'done': bool(DONE_MARK.match(text))})
    return out


def label_of(text):
    """A step's few plain words for the card: no markers, no markdown, the first clause."""
    t = re.sub(r'[🤖👤✅☑️]|\[x\]|~~|\*\*|`', '', text, flags=re.I).strip()
    t = re.sub(r'^[^:]{1,24}:\s+', '', t) if re.match(r'^\S+( \S+)?:\s', t) else t  # "Owner: look at …" -> "look at …"
    t = re.split(r'\s*[(;]|\s+[–—-]\s+|(?<=\w)[.:]\s|,\s(?=(?:then|and)\b)', t)[0].strip().rstrip('.:,')
    if len(t) > 48:
        t = t[:47].rsplit(' ', 1)[0].rstrip(',;:') + '…'
    return t[:1].upper() + t[1:] if t else 'Next step'


def current_index(steps, marked):
    """0-based index of the step running now: the one the agent marked, else the first not ticked done."""
    if not steps:
        return None
    if isinstance(marked, int) and 1 <= marked <= len(steps):
        return marked - 1
    return next((i for i, s in enumerate(steps) if not s['done']), len(steps) - 1)


def coarse(minutes):
    """A guess said the way a person would: never false precision."""
    if minutes < 20:
        return '~15 min'
    if minutes < 45:
        return '~30 min'
    if minutes < 80:
        return '~1 h'
    if minutes < 150:
        return '~2 h'
    if minutes < 300:
        return 'a few hours'
    return 'most of a day'


class Progress:
    def __init__(self, store, background=True):
        self.store = store
        with store.connect() as con:
            con.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.rates = {'all': None, 'projects': {}, 'samples': {}, 'correction': 1.0, 'graded': []}
        if background:
            threading.Thread(target=self.loop, daemon=True).start()

    # ------------------------------------------------------------ measuring

    def active_minutes(self, con, session_ids):
        """Helper work minutes over the given sessions: their event bursts, overlaps counted once."""
        if not session_ids:
            return 0.0
        spans = []
        for sid in session_ids:  # (machine, session) is indexed; session alone would scan every event
            times = [r[0] for r in con.execute(
                'SELECT ts FROM events WHERE machine IN (SELECT name FROM machines UNION SELECT machine FROM live) '
                'AND session = ? ORDER BY ts', (sid,))]
            spans += bursts_of(times)
        spans.sort()
        total, end = 0.0, None
        for a, b in spans:
            if end is None or a > end:
                total += b - a
                end = b
            elif b > end:
                total += b - end
                end = b
        return total / 60

    def fill(self):
        """Measure recently closed tasks with steps once each, and grade the guesses made for them."""
        since = time.time() - HISTORY_SEC
        with self.store.connect() as con:
            todo = con.execute(
                "SELECT t.id, t.project, t.body, t.closed_at FROM tasks t LEFT JOIN task_effort e ON e.task_id = t.id "
                "WHERE t.status = 'done' AND t.closed_at > ? AND e.task_id IS NULL ORDER BY t.closed_at DESC LIMIT ?",
                (since, FILL_PER_ROUND)).fetchall()
            for t in todo:
                sids = [r[0] for r in con.execute('SELECT session_id FROM task_sessions WHERE task_id = ?', (t['id'],))]
                steps = steps_of(t['body'])
                con.execute('INSERT OR REPLACE INTO task_effort VALUES (?, ?, ?, ?, ?)',
                            (t['id'], t['project'], sum(not s['owner'] for s in steps), self.active_minutes(con, sids),
                             t['closed_at']))
            con.execute('UPDATE task_guesses SET actual_min = (SELECT active_min FROM task_effort e '
                        'WHERE e.task_id = task_guesses.task_id) WHERE actual_min IS NULL')

    def learn(self):
        since = time.time() - HISTORY_SEC
        with self.store.connect() as con:
            rows = con.execute('SELECT project, agent_steps, active_min FROM task_effort WHERE closed_at > ? '
                               'AND agent_steps > 0 AND active_min > 0', (since,)).fetchall()
            graded = con.execute('SELECT guess_min, actual_min FROM task_guesses WHERE actual_min > 0 '
                                 'ORDER BY at DESC LIMIT ?', (CORRECT_OVER,)).fetchall()
        per = {}
        for p, n, m in rows:
            per.setdefault(p, []).append(m / n)
        every = [x for xs in per.values() for x in xs]
        ratios = [a / g for g, a in graded if g > 0]
        with self.lock:
            self.rates = {
                'all': statistics.median(every) if every else None,
                'projects': {p: statistics.median(xs) for p, xs in per.items() if len(xs) >= MIN_SAMPLES},
                'samples': {p: len(xs) for p, xs in per.items()},
                'correction': min(4.0, max(0.25, statistics.median(ratios))) if len(ratios) >= MIN_SAMPLES else 1.0,
                'graded': [(g, a) for g, a in graded],
            }

    def refresh(self):
        self.fill()
        self.learn()

    def loop(self):
        while True:
            try:
                self.refresh()
            except Exception:
                traceback.print_exc()
            time.sleep(REFRESH_SEC)

    # ------------------------------------------------------------ guessing

    def per_step(self, project):
        with self.lock:
            r = self.rates
            own = r['projects'].get(project)
            base = own if own is not None else r['all']
            return (base * r['correction'] if base else None), own is not None

    def remember(self, task_id, guess_min):
        """Keep the first whole-task guess, the one graded when the task closes."""
        with self.store.connect() as con:
            con.execute('INSERT OR IGNORE INTO task_guesses (task_id, guess_min, at) VALUES (?, ?, ?)',
                        (task_id, guess_min, time.time()))

    def of(self, task):
        """A running task's progress for its card, or None when it has no written steps."""
        steps = steps_of(task.get('body'))
        if not steps:
            return None
        at = current_index(steps, task.get('step'))
        now = steps[at]
        then = next((s['label'] for s in steps[at + 1:]), None)
        out = {'task': task['id'], 'title': task.get('title') or '', 'steps': [s['label'] for s in steps],
               'owner': [s['owner'] for s in steps], 'at': at, 'then': then, 'left': None, 'first_guess': False}
        if now['owner']:
            out['left'] = 'waiting for you'
            return out
        rate, own = self.per_step(task.get('project'))
        if rate:
            agent_all = sum(not s['owner'] for s in steps)
            ahead = sum(not s['owner'] for s in steps[at:])
            self.remember(task['id'], rate * agent_all)
            out['left'] = coarse(rate * ahead)
            out['first_guess'] = not own
        return out

    def accuracy(self):
        """How far off the graded guesses ran: median |real - guess| / real, over the last CORRECT_OVER."""
        with self.lock:
            graded = self.rates['graded']
        errs = [abs(a - g) / a for g, a in graded if a > 0]
        return {'jobs': len(errs), 'off_pct': round(100 * statistics.median(errs)) if errs else None}
