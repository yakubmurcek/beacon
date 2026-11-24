"""SQLite storage and the aggregations the dashboard reads.

Costs are computed at read time from pricing.py, so a price fix applies to all history
(after a hub restart, which also empties the cache).

Every view is built from per-day aggregates. A day's aggregate stays cached until an
ingest touches that day, so a year view is one SQL query for the missing days plus a
merge, and the previous-period comparison comes almost free.
"""
import collections
import contextlib
import datetime
import json
import math
import re
import sqlite3
import threading
import time
import unicodedata
import zoneinfo

from settings import TIMEZONE

try:
    from . import pricing
except ImportError:  # run as a script from hub/
    import pricing

GAP_SEC = 15 * 60       # silence longer than this starts a new burst
PAD_SEC = 60            # a burst lasts at least until a minute after its last event
IDLE_MIN = 15           # shorter quiet stretches are not listed as gaps
# Collectors push a live snapshot every 30 s (the Windows PC: every minute). A machine whose last
# one is older than this is asleep or off: its sessions can't be doing anything.
LIVE_STALE_SEC = 150
LIVE_STATES = ('waiting', 'working', 'idle', 'done')
LIVE_TEXT = ('agent', 'id', 'title', 'project', 'branch', 'model', 'doing', 'prompt')
# Texts agents log in the user's slot that no human typed to the agent: Codex's own action
# reviews, `!` shell commands (these can carry secrets), permission replies, injected
# reminders. Dropped on ingest and purged at start, whatever collector version sent them.
NOT_HUMAN = ('The following is the Codex agent history', '<bash-input>', '<bash-stdout>', '<bash-stderr>',
             '<permission-response>', '<system-reminder>', 'Background agent "',
             'Goal set: ')  # Claude Code's notice after /goal (the /goal itself is the prompt)
BACKGROUND_STOPPED_RE = re.compile(r'\d+ background agents were stopped by the user')  # Claude Code's notice
# Built-in commands the agent's UI handles without a model call: activity, not prompts.
LOCAL_COMMANDS = frozenset((
    '/add-dir', '/agents', '/artifacts', '/bashes', '/clear', '/config', '/context', '/copy', '/cost',
    '/doctor', '/effort', '/exit', '/export', '/fast', '/help', '/hooks', '/ide', '/keybindings', '/login',
    '/logout', '/mcp', '/memory', '/model', '/output-style', '/permissions', '/plugin', '/plugins',
    '/privacy-settings', '/quit', '/release-notes', '/reload-plugins', '/reload-skills', '/rename',
    '/resume', '/rewind', '/settings', '/stats', '/status', '/statusline', '/tasks', '/terminal-setup',
    '/theme', '/todos', '/upgrade', '/usage', '/vim', '/workflows',
))


SECRET_RE = re.compile(
    r'\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}[A-Za-z0-9_/+=-]*'           # OpenAI, Anthropic
    r'|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}'
    r'|xox[abprs]-[A-Za-z0-9-]{10,}'                      # Slack
    r'|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|glpat-[A-Za-z0-9_-]{20,}'
    r'|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})')  # JWT

SECRET_HINTS = ('sk-', 'gh', 'github_pat_', 'xox', 'AKIA', 'AIza', 'glpat-', 'eyJ')  # cheap LIKE prefilter


def clean_steps(steps):
    """A live session's latest steps as stored: at most 10, each a redacted line and its time."""
    out = []
    for st in (steps if isinstance(steps, list) else [])[-10:]:
        if isinstance(st, dict) and isinstance(st.get('text'), str) and st['text'].strip():
            at = st.get('at')
            step = {'text': redact(st['text'][:300]),
                    'at': at if isinstance(at, (int, float)) and not isinstance(at, bool) else None}
            if st.get('said') is True:
                step['said'] = True  # the agent's own words, not a tool call
            out.append(step)
    return out


def redact(text):
    """Mask API keys and tokens pasted into prompts; the dashboard shows prompt text."""
    return SECRET_RE.sub('[redacted key]', text) if text else text


def opencode_copy(mid, created_s):
    """Same test as the collector: an OpenCode id minted long after its message was created
    belongs to a copy a fork made (ids carry their mint time, ms mod 2^36 times 4096)."""
    try:
        minted = int(mid[4:16], 16) >> 12
    except (ValueError, TypeError):
        return False
    delta = (minted - int(created_s * 1000)) % (1 << 36)
    return 60_000 < delta < (1 << 35)


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


def fold(text):
    """Lowercase without accents, for search."""
    if text is None:
        return None
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))


def main_project(projects):
    """The project a session belongs to. A session started in ~/Projects that then works
    in a repo belongs to that repo once the repo has a quarter of its events."""
    if not projects:
        return '(no project)'
    real = [(n, p) for p, n in projects.items() if not p.startswith('(')]
    if real:
        n, p = max(real, key=lambda x: (x[0], x[1]))
        if n * 4 >= sum(projects.values()):
            return p
    return max(projects.items(), key=lambda x: (x[1], x[0]))[0]


def snippet(text, needle, before=50, width=180):
    """The part of text around the first match of an already folded needle."""
    folded, starts = '', []
    for i, c in enumerate(text):
        starts.append(len(folded))
        folded += fold(c)
    at = folded.find(needle)
    i = next((j for j, pos in enumerate(starts) if pos >= at), 0) if at > 0 else 0
    lo = max(0, i - before)
    part = ' '.join(text[lo:lo + width].split())
    return ('…' if lo else '') + part + ('…' if lo + width < len(text) else '')


def is_local_command(text):
    t = (text or '').strip()
    return t.startswith('/') and t.split(None, 1)[0] in LOCAL_COMMANDS


SCHEDULED = '[scheduled] '  # the collector's mark on a /loop or cron re-fire


def not_a_prompt(text):
    """Activity a person didn't type as a request: local commands and scheduled re-fires."""
    return is_local_command(text) or (text or '').startswith(SCHEDULED)


# Who drove the tokens. Every call lands in one layer, from most hands-on to most autonomous:
#   driven      main-thread calls within DRIVEN_CALLS of his last message (a prompt, or one
#               typed mid-run): he is in the loop
#   solo        main-thread calls past that: the agent carrying on alone
#   subagent    calls of the subagents his sessions spawned
#   unattended  every call of a turn nobody typed (/loop or cron re-fire, headless `claude -p`,
#               the task agent, a session with no prompt logged)
# Counting from his last message, not by the whole turn's length, keeps a day's numbers right
# for a turn still running or begun the day before.
MID_RUN = '[mid-run] '  # the collector's mark on a message typed while the agent was working
DRIVEN_CALLS = 20
LAYERS = ('driven', 'solo', 'subagent', 'unattended')
LAYER_FIELDS = tuple(f'tokens_{x}' for x in LAYERS)
HANDS_OFF = ('tokens_solo', 'tokens_subagent', 'tokens_unattended')
# Low-content prompts that only keep an agent going. Counted apart from real asks: each one is
# a stop that a hook, /goal or a better first prompt could have avoided. A short decision
# ("merge", "drop it") is not a nudge.
NUDGES = frozenset((
    'continue', 'cont', 'go', 'go on', 'go ahead', 'keep going', 'carry on', 'proceed', 'next', 'resume',
    'yes', 'y', 'yep', 'yeah', 'yes please', 'ok', 'okay', 'k', 'sure', 'do it', 'go for it', 'ok go',
    'ok continue', 'yes continue', 'and now', 'done', 'finish it', 'contnue', 'contiue', 'conitnue',
    'try again', 'try again now', 'try it again', 'try now', 'ok do it', 'ok go ahead', 'yes to all',
    'so', 'so what now', 'what now', 'start it now', 'you take it from here',
    'pokracuj', 'pokracovat', 'dal', 'jed', 'jedem', 'makej', 'ano', 'jo', 'jj', 'ok jo', 'jo pokracuj',
    'ano pokracuj', 'dobre', 'v poradku', 'tak jo', 'hotovo',
))


def is_nudge(text):
    return ' '.join(re.sub(r'[^\w\s]', ' ', fold(text or '')).split()) in NUDGES


def prompt_kind(text):
    """'steer' (typed mid-run), 'nudge' or 'ask' for a prompt that counts."""
    if (text or '').startswith(MID_RUN):
        return 'steer'
    return 'nudge' if is_nudge(text) else 'ask'


PROMPT_FIELD = {'ask': 'asks', 'nudge': 'nudges', 'steer': 'steers'}


def hands_off(b):
    """Share of the tokens he didn't drive, or None with no tokens."""
    total = sum(b.get(k) or 0 for k in LAYER_FIELDS)
    return round(sum(b.get(k) or 0 for k in HANDS_OFF) / total, 4) if total else None


def not_human(text):
    t = (text or '').lstrip()
    return t.startswith(NOT_HUMAN) or bool(BACKGROUND_STOPPED_RE.match(t))


CACHE_MAX = 4000        # day aggregates kept (days x filter combinations)
PALETTE_SIZE = 7        # projects with their own color; the rest are "Other"
TOKEN_FIELDS = ('input', 'output', 'cache_read', 'cache_write_5m', 'cache_write_1h', 'reasoning')
TABLES = ('model', 'project', 'machine', 'agent')
FILTER_KEYS = ('agent', 'machine', 'project')
NO_FILTER = (None, None, None)
# Claude app scratch workspaces were logged as their own project before collectors folded them.
SCRATCH_RE = re.compile(r'^scratch-\d{4}-\d{2}-\d{2}-[0-9a-f]+$')
# A repo renamed on disk: its old folder name still reaches the hub from old sessions and old collectors (2026-11-22: Beacon, no other name).
RENAMED_PROJECTS = {'ai-agents-usage-dashboard': 'beacon'}

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  machine TEXT NOT NULL,
  agent TEXT NOT NULL,
  session TEXT NOT NULL,
  project TEXT,
  model TEXT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  text TEXT,
  subagent INTEGER DEFAULT 0,
  speed TEXT,
  calls INTEGER DEFAULT 1,
  input INTEGER DEFAULT 0,
  output INTEGER DEFAULT 0,
  cache_read INTEGER DEFAULT 0,
  cache_write_5m INTEGER DEFAULT 0,
  cache_write_1h INTEGER DEFAULT 0,
  reasoning INTEGER DEFAULT 0,
  tools INTEGER DEFAULT 0,
  cost_reported REAL,
  effort TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_session ON events(machine, session);
CREATE TABLE IF NOT EXISTS palette (
  project TEXT PRIMARY KEY,
  slot INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS machines (
  name TEXT PRIMARY KEY,
  last_seen REAL,
  version INTEGER,
  counts TEXT
);
CREATE TABLE IF NOT EXISTS live (
  machine TEXT PRIMARY KEY,
  seen_at REAL NOT NULL,
  sessions TEXT NOT NULL
);
"""

COLS = ('id', 'machine', 'agent', 'session', 'project', 'model', 'ts', 'kind', 'text', 'subagent',
        'speed', 'calls', 'input', 'output', 'cache_read', 'cache_write_5m', 'cache_write_1h',
        'reasoning', 'tools', 'cost_reported', 'effort')

NUMERIC = ('subagent', 'calls', 'tools', 'cost_reported', *TOKEN_FIELDS)
DEFAULTS = {'calls': 1, 'subagent': 0, 'tools': 0, **{f: 0 for f in TOKEN_FIELDS}}

UPSERT = f"""
INSERT INTO events ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})
ON CONFLICT(id) DO UPDATE SET
  -- Only a run re-sent as a subagent moves (to its parent). A Claude Code fork copies
  -- earlier calls into the new session's log; those stay where they happened.
  session=CASE WHEN excluded.subagent > subagent THEN excluded.session ELSE session END,
  subagent=max(subagent, excluded.subagent), project=excluded.project, model=excluded.model, text=excluded.text, ts=min(ts, excluded.ts),
  calls=max(calls, excluded.calls), tools=max(tools, excluded.tools),
  cost_reported=coalesce(excluded.cost_reported, cost_reported), effort=coalesce(excluded.effort, effort),
  {', '.join(f'{f}=max({f}, excluded.{f})' for f in TOKEN_FIELDS)}
"""


def parse_filter(q):
    """Query dict -> (agent, machine, project), empty values meaning 'all'."""
    return tuple((q.get(k) or None) for k in FILTER_KEYS)


class Store:
    def __init__(self, path, tz=TIMEZONE):
        self.path = path
        self.tz = zoneinfo.ZoneInfo(tz)
        self._cache = collections.OrderedDict()
        self._gen = collections.Counter()  # bumped per date on ingest; a stale build is not cached
        self._lock = threading.Lock()
        self.on_live = None  # set by tasks.Tasks: settles waiting sessions whose tasks are all closed
        with self.connect() as con:
            if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'events'").fetchone() and 'effort' not in [
                    r[1] for r in con.execute('PRAGMA table_info(events)')]:
                con.execute('ALTER TABLE events ADD COLUMN effort TEXT')  # the reasoning effort a call ran at
            con.executescript(SCHEMA)
            self._canon = self.fold_projects(con)
            con.create_function('not_human', 1, not_human, deterministic=True)
            con.execute("DELETE FROM events WHERE kind = 'prompt' AND (%s OR text LIKE '%% background agents were stopped%%') "
                        "AND not_human(text)" % ' OR '.join(['ltrim(text) LIKE ?'] * len(NOT_HUMAN)),
                        tuple(p + '%' for p in NOT_HUMAN))
            # OpenCode forks copy messages under new ids; older collectors sent the copies too.
            con.create_function('opencode_copy', 2, opencode_copy, deterministic=True)
            con.execute("DELETE FROM events WHERE agent = 'OpenCode' AND id LIKE 'opencode%msg_%' "
                        "AND opencode_copy(substr(id, instr(id, 'msg_')), ts)")
            con.create_function('codex_request', 1, codex_request, deterministic=True)
            con.execute("UPDATE events SET text = codex_request(text) WHERE kind = 'prompt' AND agent = 'Codex' "
                        "AND text LIKE '%## My request%' AND codex_request(text) != text")
            con.create_function('redact', 1, redact, deterministic=True)
            con.execute("UPDATE events SET text = redact(text) WHERE kind = 'prompt' AND text IS NOT NULL "
                        "AND (%s) AND redact(text) != text" % ' OR '.join(f"text LIKE '%{m}%'" for m in SECRET_HINTS))

    # ------------------------------------------------------------ project names

    @staticmethod
    def project_key(name):
        if SCRATCH_RE.match(name):
            return 'scratch'
        low = name.lower()
        for old, new in RENAMED_PROJECTS.items():  # an old folder name (and its side checkouts) is the new one
            if low == old or low.startswith(old + '-'):
                return new + low[len(old):]
        return low

    def fold_projects(self, con):
        """One spelling per project: scratch workspaces become "scratch", and names that differ
        only in case (the same repo on macOS and Windows) take the most used spelling."""
        groups = collections.defaultdict(list)
        for name, n in con.execute('SELECT project, count(*) FROM events WHERE project IS NOT NULL GROUP BY 1'):
            groups[self.project_key(name)].append((n, name))
        canon = {key: 'scratch' if key == 'scratch' else key if key in RENAMED_PROJECTS.values() else max(names)[1]
                 for key, names in groups.items()}
        counts = {key: sum(n for n, _ in names) for key, names in groups.items()}
        for key in sorted(canon, key=len):
            base = self.base_project(key, canon, counts)
            if base:
                canon[key] = canon[base]
        for key, names in groups.items():
            for _, name in names:
                if name != canon[key]:
                    con.execute('UPDATE events SET project = ? WHERE project = ?', (canon[key], name))
        return canon

    def canonical_project(self, name):
        if not name:
            return name
        key = self.project_key(name)
        with self._lock:
            if key not in self._canon:
                base = self.base_project(key, self._canon)
                self._canon[key] = self._canon[base] if base else (
                    'scratch' if key == 'scratch' else key if key in RENAMED_PROJECTS.values() else name)
            return self._canon[key]

    @staticmethod
    def base_project(key, known, counts=None):
        """A side checkout named `<repo>-<anything>` (a worktree or a preview clone, like
        linux-setup-fix) is that repo, when the repo is a project with at least as much history.
        The shortest such repo wins, so linux-setup-rc-gym-site is linux-setup."""
        for i, ch in enumerate(key):
            if ch == '-' and key[:i] in known and key[:i] != 'scratch' and (counts is None or counts[key[:i]] >= counts[key]):
                return key[:i]
        return None

    @contextlib.contextmanager
    def connect(self):
        """A connection that commits on success and is always closed (sqlite3's own
        context manager only commits, leaving every request's connection to the GC)."""
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA journal_mode=WAL')
            with con:
                yield con
        finally:
            con.close()

    # ------------------------------------------------------------ writes

    def ingest(self, machine, events, version=None, counts=None, live=None):
        rows = []
        for e in events:
            if not isinstance(e, dict) or e.get('kind') not in ('prompt', 'call'):
                continue
            if not e.get('id') or not isinstance(e.get('ts'), (int, float)):
                continue
            # A timestamp in milliseconds, NaN or years off can't be placed on a day (and would
            # fail the whole batch, which the collector then resends forever): skip it.
            if not (946684800 <= e['ts'] <= time.time() + 86400):
                continue
            for f in NUMERIC:  # a count that isn't a number would break every view that reads it
                if f in e and e[f] is not None and not isinstance(e[f], (int, float)):  # bools are ints: fine
                    e = dict(e, **{f: None})
            # One odd event must not reject the batch (the collector would resend it forever).
            odd = {k: str(e[k]) for k in ('text', 'agent', 'session', 'project', 'model', 'speed', 'effort')
                   if e.get(k) is not None and not isinstance(e[k], str)}
            if odd:
                e = dict(e, **odd)
            if e['kind'] == 'prompt' and not_human(e.get('text')):
                continue
            if e['kind'] == 'prompt':
                e = dict(e, text=redact(codex_request(e.get('text')) if e.get('agent') == 'Codex' else e.get('text')))
            e = dict(e, machine=machine, project=self.canonical_project(e.get('project')))
            rows.append(tuple(e.get(c) if e.get(c) is not None else DEFAULTS.get(c) for c in COLS))
        with self.connect() as con:
            con.executemany(UPSERT, rows)
            con.execute('INSERT INTO machines(name,last_seen,version,counts) VALUES(?,?,?,?) '
                        'ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen, '
                        'version=excluded.version, counts=excluded.counts',
                        (machine, time.time(), version, json.dumps(counts or {})))
            if isinstance(live, dict) and isinstance(live.get('sessions'), list):
                con.execute('INSERT OR REPLACE INTO live(machine, seen_at, sessions) VALUES (?, ?, ?)',
                            (machine, time.time(), json.dumps(self.clean_live(live['sessions']))))
        self.invalidate({self.local_date(r[COLS.index('ts')]) for r in rows})
        return len(rows)

    def invalidate(self, dates):
        if not dates:
            return
        with self._lock:
            for d in dates:
                self._gen[d] += 1
            for key in [k for k in self._cache if k[0] in dates]:
                del self._cache[key]

    # ------------------------------------------------------------ time

    def day_bounds(self, date):
        d = datetime.date.fromisoformat(date)
        start = datetime.datetime(d.year, d.month, d.day, tzinfo=self.tz)
        end = datetime.datetime.combine(d + datetime.timedelta(days=1), datetime.time(), tzinfo=self.tz)
        return start.timestamp(), end.timestamp()

    def local_date(self, ts):
        return datetime.datetime.fromtimestamp(ts, self.tz).date().isoformat()

    def today(self):
        return datetime.datetime.now(self.tz).date().isoformat()

    # ------------------------------------------------------------ day aggregates

    def rows(self, start, end, where='', params=()):
        with self.connect() as con:
            return [dict(r) for r in con.execute(
                f'SELECT * FROM events WHERE ts >= ? AND ts < ? {where} ORDER BY ts', (start, end, *params))]

    def day_aggs(self, dates, filt=NO_FILTER):
        """Aggregates for these dates, from cache where possible, else one query for the rest."""
        out = {}
        with self._lock:
            for d in dates:
                agg = self._cache.get((d, filt))
                if agg is not None:
                    self._cache.move_to_end((d, filt))
                    out[d] = agg
            missing = sorted(d for d in dates if d not in out)
            gens = {d: self._gen[d] for d in missing}
        if missing:
            start, _ = self.day_bounds(missing[0])
            _, end = self.day_bounds(missing[-1])
            by_day = collections.defaultdict(list)
            for r in self.rows(start, end, *prefilter(filt, start, end)):
                by_day[self.local_date(r['ts'])].append(r)
            for d in missing:
                s, e = self.day_bounds(d)
                day_rows = by_day.get(d, [])
                out[d] = aggregate(day_rows, s, e, filt, self.seeds(day_rows, s))
            with self._lock:
                for d in missing:
                    if self._gen[d] == gens[d]:  # an ingest landed meanwhile: rebuild next time
                        self._cache[(d, filt)] = out[d]
                while len(self._cache) > CACHE_MAX:
                    self._cache.popitem(last=False)
        return out

    def seeds(self, rows, start):
        """For each session whose first event here is a call (a turn carried over from before
        `start`): whether a person opened that turn, and the main-thread calls since his last
        message. See aggregate()."""
        firsts = {}
        for r in rows:
            firsts.setdefault((r['machine'], r['session']), r)
        need = [k for k, r in firsts.items() if r['kind'] == 'call']
        out = {}
        if not need:
            return out
        with self.connect() as con:
            for machine, sess in need:
                opened = None
                for p in con.execute("SELECT ts, text FROM events WHERE machine = ? AND session = ? AND kind = 'prompt' "
                                     'AND ts < ? ORDER BY ts DESC', (machine, sess, start)):
                    if not is_local_command(p['text']):
                        opened = p
                        break
                if opened is None or (opened['text'] or '').startswith(SCHEDULED):
                    out[(machine, sess)] = ('unattended', 0)
                    continue
                n = con.execute("SELECT coalesce(sum(calls), 0) FROM events WHERE machine = ? AND session = ? "
                                "AND kind = 'call' AND subagent = 0 AND ts >= ? AND ts < ?",
                                (machine, sess, opened['ts'], start)).fetchone()[0]
                out[(machine, sess)] = ('human', n)
        return out

    def warm(self, days=800):
        """Fill the cache for recent history so the first year view is fast."""
        today = datetime.date.fromisoformat(self.today())
        self.day_aggs([(today - datetime.timedelta(days=i)).isoformat() for i in range(days)])

    # ------------------------------------------------------------ reads

    def meta(self):
        with self.connect() as con:
            machines = [dict(r, counts=json.loads(r['counts'] or '{}'))
                        for r in con.execute('SELECT * FROM machines ORDER BY name')]
            lo, hi, n = con.execute('SELECT min(ts), max(ts), count(*) FROM events').fetchone()
        today = datetime.date.fromisoformat(self.today())
        last = lambda n: merge(self.day_aggs([(today - datetime.timedelta(days=i)).isoformat() for i in range(n)]).values())
        recent, year = last(30), last(365)
        by_active = lambda agg, k: [name for name, b in sorted(agg['tables'][k].items(),
                                                                key=lambda x: (-x[1]['active_min'], x[0]))]
        return {
            'today': today.isoformat(), 'tz': str(self.tz), 'now': time.time(), 'machines': machines,
            'first_date': self.local_date(lo) if lo else None,
            'last_date': self.local_date(hi) if hi else None, 'events': n,
            # Top projects of the last 30 days keep one color in every view, whatever the filter.
            'palette': self.palette([p for p in by_active(recent, 'project') if not p.startswith('(')][:PALETTE_SIZE]),
            'options': {k: by_active(year, k) for k in FILTER_KEYS},
            'live': self.live(),
        }

    def palette(self, top):
        """Color slot per top project, remembered so a reshuffle of the ranking doesn't recolor them:
        a project keeps its slot while it stays on top, and gets it back later if it is still free."""
        with self._lock, self.connect() as con:
            held = dict(con.execute('SELECT project, slot FROM palette'))
            out = {p: held[p] for p in top if p in held}
            if len(set(out.values())) < len(out):  # two top projects remember one slot: first keeps it
                seen = set()
                out = {p: n for p, n in out.items() if not (n in seen or seen.add(n))}
            taken = set(out.values())
            unheld = [n for n in range(1, PALETTE_SIZE + 1) if n not in taken and n not in held.values()]
            reusable = [n for n in range(1, PALETTE_SIZE + 1) if n not in taken and n in held.values()]
            for p in top:
                if p in out:
                    continue
                n = (unheld or reusable).pop(0)
                out[p] = n
                con.execute('DELETE FROM palette WHERE slot = ?', (n,))
                con.execute('INSERT OR REPLACE INTO palette(project, slot) VALUES (?, ?)', (p, n))
        return out

    def clean_live(self, sessions):
        """A machine's snapshot as stored: known fields only, strings clipped, no odd types,
        so one bad session from a collector can't break every reader."""
        out = []
        for x in sessions[:100]:
            if not isinstance(x, dict) or x.get('state') not in LIVE_STATES or not x.get('id'):
                continue
            s = {k: str(x[k])[:300] if x.get(k) is not None else None for k in LIVE_TEXT}
            s['agent'] = s['agent'] or 'agent'
            # Same session id as its events ("Claude Code:<uuid>"), so a reader can join the two.
            s['id'] = f"{s['agent']}:{s['id']}"
            s['project'] = self.canonical_project(s['project']) or '(no project)'
            s['prompt'] = redact(s['prompt'])
            s['doing'] = redact(s['doing'])
            s['state'] = x['state']
            s['last'] = x['last'] if isinstance(x.get('last'), (int, float)) and not isinstance(x['last'], bool) else None
            s['helpers'] = x['helpers'] if isinstance(x.get('helpers'), int) and not isinstance(x['helpers'], bool) else 0
            s['steps'] = clean_steps(x.get('steps'))
            out.append(s)
        return out

    def live_now(self):
        """Every machine that ever reported, and what each of its agents is doing right now.
        A machine whose snapshot is older than LIVE_STALE_SEC is offline, with no sessions."""
        now = time.time()
        with self.connect() as con:
            snaps = {r['machine']: r for r in con.execute('SELECT * FROM live')}
            names = [r['name'] for r in con.execute('SELECT name FROM machines ORDER BY name')]
        machines = []
        for name in sorted(set(names) | set(snaps)):
            snap = snaps.get(name)
            online = bool(snap) and now - snap['seen_at'] <= LIVE_STALE_SEC
            sessions = []
            if online:
                for x in json.loads(snap['sessions']):
                    x['machine'] = name
                    x['idle_s'] = round(max(0, now - x['last'])) if x.get('last') else None
                    sessions.append(x)
            machines.append({'machine': name, 'online': online,
                             'seen_at': snap['seen_at'] if snap else None, 'sessions': sessions})
        if self.on_live:
            self.on_live(machines)
        return {'now': now, 'stale_after': LIVE_STALE_SEC, 'machines': machines}

    def live(self):
        """Every live session on every online machine, most urgent first (the "Running now" strip).
        A done session is still open but has nothing left, so it is not running."""
        rank = {s: i for i, s in enumerate(LIVE_STATES)}
        out = [x for m in self.live_now()['machines'] for x in m['sessions'] if x['state'] != 'done']
        out.sort(key=lambda x: (rank[x['state']], -(x['last'] or 0)))
        return out

    def day(self, date, filt=NO_FILTER):
        start, end = self.day_bounds(date)
        now = time.time()
        agg = self.day_aggs([date], filt)[date]
        out = finalize(agg)
        horizon = min(end, now) if start <= now < end else end
        out.update(
            date=date, is_today=start <= now < end, start=start, end=end, minutes=agg['minutes'],
            sessions=[finalize_session(s) for s in sorted(agg['sessions'].values(), key=lambda s: s['start'])],
            concurrency=agg['conc'], concurrency_by_agent=agg['conc_agent'],
            gaps=idle_gaps(agg['conc'], start, horizon),
        )
        # A session carried over from an earlier day says when it really began, not 00:00.
        with self.connect() as con:
            for sess in out['sessions']:
                if sess['start'] - start < 3600:
                    began = con.execute('SELECT min(ts) FROM events WHERE machine = ? AND session = ?',
                                        (sess['machine'], sess['id'])).fetchone()[0]
                    if began is not None and began < start:
                        sess['began'] = began
        d = datetime.date.fromisoformat(date)
        week = self.day_aggs([(d - datetime.timedelta(days=i)).isoformat() for i in range(1, 8)], filt).values()
        week = list(week)
        # Spend per hour of this day against the average day, for the cumulative chart.
        out['cost_by_hour'] = [round(c, 4) for c in agg['hourly']['cost']]
        out['baseline_cost_by_hour'] = [round(sum(a['hourly']['cost'][h] for a in week) / max(1, len(week)), 4)
                                        for h in range(24)]
        if out['is_today']:
            out['baseline'] = average_until(week, (now - start) / 3600 * 24 / (agg['minutes'] / 60))
            out['baseline_label'] = '7d avg by now'
        else:
            out['baseline'] = average(week)
            out['baseline_label'] = '7-day avg'
        out['baseline']['hands_off'] = hands_off(merge(week)['total'])  # a share: of the whole week, by now or not
        return out

    def range(self, date_from, date_to, filt=NO_FILTER):
        d0, d1 = datetime.date.fromisoformat(date_from), datetime.date.fromisoformat(date_to)
        span = (d1 - d0).days + 1
        if span < 1 or span > 400:
            raise ValueError('range must be 1-400 days')
        dates = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(span)]
        prev = [(d0 - datetime.timedelta(days=span - i)).isoformat() for i in range(span)]
        aggs = self.day_aggs(dates + prev, filt)
        days, hours = [], [[0] * 24 for _ in range(7)]
        for ds in dates:
            a = aggs[ds]
            t = finalize(a)
            days.append({'date': ds, **t['totals'],
                         'active_by_project': {k: b['active_min'] for k, b in a['tables']['project'].items()},
                         'cost_by_project': {k: round(b['cost'], 4) for k, b in a['tables']['project'].items()},
                         'active_by_agent': {k: b['active_min'] for k, b in a['tables']['agent'].items()},
                         'cost_by_agent': {k: round(b['cost'], 4) for k, b in a['tables']['agent'].items()}})
            wd = datetime.date.fromisoformat(ds).weekday()
            for h, m in enumerate(a['hours']):
                hours[wd][h] += m
        merged = merge(aggs[d] for d in dates)
        out = finalize(merged)
        sessions = sorted(merged['sessions'].values(), key=lambda s: (-s['cost'], -s['active_min']))
        previous = merge(aggs[d] for d in prev)
        out.update({
            'from': date_from, 'to': date_to, 'days': days, 'hours': hours,
            # Every session, most expensive first, trimmed to what the list shows; the UI lists
            # the top 25 and searches them all. The drawer loads the full session.
            'sessions': [finalize_session(s, light=True) for s in sessions],
            'previous': finalize(previous)['totals'] if previous['total']['calls'] or previous['total']['prompts'] else None,
        })
        return out

    def search(self, q, date_from, date_to):
        """[key, snippet] for each session (machine|session) with a prompt containing q in
        these days; the snippet is the first matching prompt around the match."""
        start, _ = self.day_bounds(date_from)
        _, end = self.day_bounds(date_to)
        needle = fold(q)
        out = {}
        with self.connect() as con:
            # Case and accents fold on both sides: "ucet" finds "Účet" (SQLite's LIKE only folds ASCII).
            con.create_function('fold', 1, fold, deterministic=True)
            for key, text in con.execute(
                    "SELECT machine || '|' || session, text FROM events WHERE kind = 'prompt' AND ts >= ? AND ts < ? "
                    "AND instr(fold(text), ?) ORDER BY ts", (start, end, needle)):
                if key not in out:
                    out[key] = snippet(text, needle)
                    if len(out) >= 2000:
                        break
        return [[k, v] for k, v in out.items()]

    def session(self, machine, session):
        """One session in full. With no machine, the one that logged it last (a task links a
        session by id alone)."""
        with self.connect() as con:
            if not machine:
                hit = con.execute('SELECT machine FROM events WHERE session=? ORDER BY ts DESC LIMIT 1',
                                  (session,)).fetchone()
                if not hit:
                    return None
                machine = hit['machine']
            rows = [dict(r) for r in con.execute(
                'SELECT * FROM events WHERE machine=? AND session=? ORDER BY ts', (machine, session))]
        if not rows:
            return None
        agg = aggregate(rows, rows[0]['ts'], rows[-1]['ts'] + PAD_SEC)
        if not agg['sessions']:
            return None  # only local commands: not a session
        raw = next(iter(agg['sessions'].values()))
        sess = finalize_session(raw)
        sess['projects'] = raw['projects']  # events per folder: a session can work in several repos
        sess['turns'] = turns_of(rows)
        return sess


def prefilter(filt, start, end):
    """SQL narrowing to the sessions a filter can match; aggregate() still decides by main project."""
    agent, machine, project = filt
    where, params = '', []
    if agent:
        where += ' AND agent = ?'
        params.append(agent)
    if machine:
        where += ' AND machine = ?'
        params.append(machine)
    if project and project != '(no project)':  # that one also covers sessions with no project logged
        where += (' AND (machine, session) IN (SELECT machine, session FROM events'
                  ' WHERE ts >= ? AND ts < ? AND project = ?)')
        params += [start, end, project]
    return where, tuple(params)


# ---------------------------------------------------------------- aggregation

def call_cost(r):
    """(cost, uncached_cost, priced) for one call row. Reported cost wins over list price."""
    tokens = {k: r.get(k) or 0 for k in ('input', 'cache_write_5m', 'cache_write_1h', 'cache_read', 'output')}
    listed = pricing.cost(r.get('model'), r.get('speed'), **tokens)
    uncached = pricing.uncached_cost(r.get('model'), r.get('speed'), **tokens)
    reported = r.get('cost_reported')
    # OpenCode logs $0 for calls on a subscription or free tier. That is not what the
    # tokens list at, so a $0 on a call that used tokens counts as no reported price.
    if reported == 0 and any(tokens.values()):
        reported = None
    if reported is not None:
        return reported, uncached if uncached is not None else reported, True
    if listed is None:
        return 0.0, 0.0, False
    return listed, uncached, True


def bursts_of(times, breaks=frozenset()):
    """Runs of events no more than GAP_SEC apart. An event in `breaks` (a scheduled re-fire)
    always starts a new run: the wait before a timer isn't someone working."""
    out = []
    for t in times:
        if out and t - out[-1][1] <= GAP_SEC and t not in breaks:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(a, b + PAD_SEC) for a, b in out]


def minutes_covered(intervals, start, n):
    """Set of minute indexes (0..n-1 from start) touched by any interval."""
    mins = set()
    for a, b in intervals:
        lo = max(0, int((a - start) // 60))
        hi = min(n, math.ceil((b - start) / 60))
        mins.update(range(lo, hi))
    return mins


def new_bucket():
    return {'sessions': set(), 'prompts': 0, 'calls': 0, 'tools': 0, 'cost': 0.0, 'uncached': 0.0,
            'unpriced_calls': 0, 'active_min': 0, **{k: 0 for k in TOKEN_FIELDS},
            'asks': 0, 'nudges': 0, 'steers': 0, **{k: 0 for k in LAYER_FIELDS}}


def tokens_of(r):
    return sum(r.get(k) or 0 for k in ('input', 'output', 'cache_read', 'cache_write_5m', 'cache_write_1h'))


def add_call(b, r, c, layer):
    b['calls'] += r.get('calls') or 1
    b['tools'] += r.get('tools') or 0
    for k in TOKEN_FIELDS:
        b[k] += r.get(k) or 0
    b[f'tokens_{layer}'] += tokens_of(r)
    cost, uncached, priced = c
    b['cost'] += cost
    b['uncached'] += uncached
    if not priced:
        b['unpriced_calls'] += r.get('calls') or 1


def add_bucket(dst, src):
    for k, v in src.items():
        if k == 'sessions':
            dst[k] |= v
        else:
            dst[k] += v


def aggregate(rows, start, end, filt=NO_FILTER, seeds=None):
    """Mergeable aggregate of one period (a day, or one whole session).

    Sessions are filtered as a whole by their agent, machine and main project, so a
    filtered view never shows half a session. `seeds` carries a turn begun before `start`
    into the period: (machine, session) -> ('human' or 'unattended', main calls since his
    last message).
    """
    by_session = collections.OrderedDict()
    for r in rows:
        by_session.setdefault((r['machine'], r['session']), []).append(r)
    period = Period(start, end)
    for (machine, sess), evs in by_session.items():
        period.add_session(machine, sess, evs, filt, (seeds or {}).get((machine, sess), ('unattended', 0)))
    return period.result()


class Period:
    """The running sums of aggregate(): totals, the per-project/machine/agent/model tables, hourly counts and
    each session's own bucket."""

    def __init__(self, start, end):
        self.start = start
        self.n = max(1, round((end - start) / 60))
        self.tables = {k: collections.defaultdict(new_bucket) for k in TABLES}
        self.intervals = {k: collections.defaultdict(list) for k in ('project', 'machine', 'agent')}
        # Per-hour counts, so today can be compared with other days up to the same time.
        self.hourly = {k: [0.0] * 24 for k in ('cost', 'prompts', 'calls', 'sessions')}
        self.project_hour = {}
        self.total = new_bucket()
        self.cost_by_family = collections.Counter()
        self.sessions = {}

    def hour_of(self, ts):
        return min(23, max(0, int((ts - self.start) // 60) * 24 // self.n))

    def add_session(self, machine, sess, evs, filt, seed):
        """One session's events, unless it did no work or the filter leaves it out."""
        # Opened, ran /clear or /model, closed: no request, no model call, no work to count.
        if all(e['kind'] == 'prompt' and not_a_prompt(e['text']) for e in evs):
            return
        agent = evs[0]['agent']
        projects = collections.Counter(e['project'] for e in evs if e['project'])
        project = main_project(projects)
        if any(want is not None and want != have for want, have in zip(filt, (agent, machine, project))):
            return
        key = f'{machine}|{sess}'
        times = [e['ts'] for e in evs]
        self.hourly['sessions'][self.hour_of(times[0])] += 1
        self.project_hour[project] = min(self.project_hour.get(project, 23), self.hour_of(times[0]))
        bursts = bursts_of(times, {e['ts'] for e in evs if e['kind'] == 'prompt' and (e['text'] or '').startswith(SCHEDULED)})
        dims = (('project', project), ('machine', machine), ('agent', agent))
        s = new_bucket()
        walk = {'s': s, 'dims': dims, 'key': key, 'models': collections.Counter(),
                'own_models': collections.Counter(),  # the session's own calls name its model, not its subagents'
                'first_prompt': None, 'turn': seed[0], 'since': seed[1]}
        for e in evs:
            if e['kind'] == 'prompt':
                self.add_prompt(walk, e)
            else:
                self.add_call_event(walk, e)
        for k, v in dims:
            self.tables[k][v]['sessions'].add(key)
            self.intervals[k][v].extend(bursts)
        self.total['sessions'].add(key)
        s['sessions'].add(key)
        s['active_min'] = len(minutes_covered(bursts, self.start, self.n))
        models, own_models = walk['models'], walk['own_models']
        s.update({
            'key': key, 'id': sess, 'machine': machine, 'agent': agent, 'project': project,
            'model': (own_models or models).most_common(1)[0][0] if models else None,
            'models': dict(models), 'own_models': dict(own_models), 'projects': dict(projects), 'start': times[0],
            'end': bursts[-1][1], 'bursts': [[a, b] for a, b in bursts], 'first_prompt': walk['first_prompt'],
            'subagent_calls': sum(e.get('calls') or 1 for e in evs if e.get('subagent') and e['kind'] == 'call'),
        })
        self.sessions[key] = s

    def add_prompt(self, walk, e):
        """A prompt: his own message starts a human turn; a /command or scheduled re-fire only labels."""
        s = walk['s']
        if not_a_prompt(e['text']):
            walk['first_prompt'] = walk['first_prompt'] or e['text']  # a label of last resort
            if (e['text'] or '').startswith(SCHEDULED):
                walk['turn'], walk['since'] = 'unattended', 0
            return
        walk['turn'], walk['since'] = 'human', 0
        kind = PROMPT_FIELD[prompt_kind(e['text'])]
        s['prompts'] += 1
        s[kind] += 1
        self.total[kind] += 1
        # A leading /clear, /model or scheduled re-fire says nothing about the session;
        # label it with the first real request, falling back to that.
        first = walk['first_prompt']
        if not first or (weak_label(first) and not weak_label(e['text'])):
            walk['first_prompt'] = e['text'] or first
        for k, v in walk['dims']:
            self.tables[k][v]['prompts'] += 1
            self.tables[k][v][kind] += 1
        self.total['prompts'] += 1
        self.hourly['prompts'][self.hour_of(e['ts'])] += 1

    def add_call_event(self, walk, e):
        """A model call, counted in the layer it belongs to: unattended, subagent, driven by him or solo."""
        c = call_cost(e)
        calls = e.get('calls') or 1
        if walk['turn'] == 'unattended':
            layer = 'unattended'
        elif e.get('subagent'):
            layer = 'subagent'
        else:
            layer = 'driven' if walk['since'] < DRIVEN_CALLS else 'solo'
            walk['since'] += calls
        self.hourly['calls'][self.hour_of(e['ts'])] += calls
        self.hourly['cost'][self.hour_of(e['ts'])] += c[0]
        walk['models'][e['model']] += calls
        if not e.get('subagent'):
            walk['own_models'][e['model']] += calls
        self.cost_by_family[pricing.family(e['model'])] += c[0]
        add_call(walk['s'], e, c, layer)
        add_call(self.total, e, c, layer)
        add_call(self.tables['model'][e['model']], e, c, layer)
        self.tables['model'][e['model']]['sessions'].add(walk['key'])
        for k, v in walk['dims']:
            add_call(self.tables[k][v], e, c, layer)

    def result(self):
        """The aggregate, with minute-by-minute concurrency (split by agent) and active hours."""
        n, start = self.n, self.start
        conc = [0] * n
        conc_agent = collections.defaultdict(lambda: [0] * n)
        # Minutes of sessions whose calls are mostly priced: API value per hour divides by these,
        # not by hours on agents that log no price.
        priced = [0] * n
        for s in self.sessions.values():
            mostly_priced = s['calls'] and s['unpriced_calls'] * 2 < s['calls']
            for m in minutes_covered(s['bursts'], start, n):
                conc[m] += 1
                conc_agent[s['agent']][m] += 1
                if mostly_priced:
                    priced[m] = 1
        for k, groups in self.intervals.items():
            for name, iv in groups.items():
                self.tables[k][name]['active_min'] = len(minutes_covered(iv, start, n))
        hours, priced_hours = [0] * 24, [0] * 24
        for m, c in enumerate(conc):
            if c:
                hours[min(23, m * 24 // n)] += 1  # n is 1380/1500 on DST days; close enough
                priced_hours[min(23, m * 24 // n)] += priced[m]
        self.total['active_min'] = sum(hours)
        peak = max(conc) if conc else 0
        return {
            'minutes': n, 'total': self.total, 'tables': self.tables, 'cost_by_family': self.cost_by_family,
            'sessions': self.sessions, 'conc': conc, 'conc_agent': dict(conc_agent), 'hours': hours,
            'priced_hours': priced_hours,
            'hourly': self.hourly, 'project_hour': self.project_hour,
            'peak': peak, 'peak_ts': start + conc.index(peak) * 60 if peak else None,
        }


def merge(aggs):
    """Sum day aggregates into one (sessions spanning midnight count once)."""
    out = {'total': new_bucket(), 'tables': {k: collections.defaultdict(new_bucket) for k in TABLES},
           'cost_by_family': collections.Counter(), 'sessions': {}, 'peak': 0, 'peak_ts': None,
           'priced_hours': [0] * 24}
    for a in aggs:
        add_bucket(out['total'], a['total'])
        out['priced_hours'] = [x + y for x, y in zip(out['priced_hours'], a['priced_hours'])]
        for k in TABLES:
            for name, b in a['tables'][k].items():
                add_bucket(out['tables'][k][name], b)
        out['cost_by_family'].update(a['cost_by_family'])
        if a['peak'] > out['peak']:
            out['peak'], out['peak_ts'] = a['peak'], a['peak_ts']
        for key, s in a['sessions'].items():
            m = out['sessions'].get(key)
            if m is None:
                out['sessions'][key] = dict(s, sessions=set(s['sessions']), models=dict(s['models']),
                                            own_models=dict(s['own_models']), projects=dict(s['projects']), bursts=list(s['bursts']))
                continue
            for f in ('prompts', 'calls', 'tools', 'cost', 'uncached', 'unpriced_calls', 'active_min',
                      'subagent_calls', *TOKEN_FIELDS, 'asks', 'nudges', 'steers', *LAYER_FIELDS):
                m[f] += s[f]
            for f in ('models', 'own_models', 'projects'):
                for name, n in s[f].items():
                    m[f][name] = m[f].get(name, 0) + n
            m['bursts'] += s['bursts']
            # Same label rule as one day: the earliest real request beats a leading /command.
            if label_rank(s['first_prompt'], s['start']) < label_rank(m['first_prompt'], m['start']):
                m['first_prompt'] = s['first_prompt']
            m['start'] = min(m['start'], s['start'])
            m['end'] = max(m['end'], s['end'])
            m['merged'] = True
    # A session spanning days takes its model and project from all of them, not its first day.
    for m in out['sessions'].values():
        if m.pop('merged', False):
            pick = m['own_models'] or m['models']
            m['model'] = max(pick, key=pick.get) if pick else None
            m['project'] = main_project(m['projects'])
    return out


def weak_label(text):
    """A session label that only stands in until a typed request comes along."""
    return not text or text.startswith('/') or not_a_prompt(text)


def label_rank(prompt, start):
    return (prompt is None, weak_label(prompt), start)


def finalize_bucket(b, name=None):
    out = {k: v for k, v in b.items() if k != 'sessions'}
    out['sessions'] = len(b['sessions'])
    out['cost'] = round(b['cost'], 4)
    out['uncached'] = round(b['uncached'], 4)
    if name is not None:
        out['name'] = name
    return out


LIST_FIELDS = ('key', 'id', 'machine', 'agent', 'project', 'model', 'start', 'end', 'active_min',
               'prompts', 'calls', 'cost', 'unpriced_calls')


def finalize_session(s, light=False):
    out = finalize_bucket(s)
    del out['sessions']
    out.pop('projects', None)
    out.pop('own_models', None)
    if light:
        out = {k: out[k] for k in LIST_FIELDS}
        out['first_prompt'] = (s['first_prompt'] or '')[:300] or None
    return out


def finalize(agg):
    """JSON shape shared by day and range responses."""
    totals = finalize_bucket(agg['total'], 'total')
    totals.update(peak=agg['peak'], peak_ts=agg['peak_ts'], priced_min=sum(agg['priced_hours']),
                  hands_off=hands_off(agg['total']),
                  projects=sum(1 for p in agg['tables']['project'] if not p.startswith('(')))

    def table(k):
        items = [finalize_bucket(b, name) for name, b in agg['tables'][k].items()]
        items.sort(key=lambda x: (-x['cost'], -x['active_min'], -x['calls']))
        if k == 'model':
            for x in items:
                x['family'] = pricing.family(x['name'])
        return items

    return {
        'totals': totals,
        'cost_by_family': {k: round(v, 4) for k, v in agg['cost_by_family'].items() if v},
        'active_by_agent': {k: b['active_min'] for k, b in agg['tables']['agent'].items()},
        **{f'by_{k}': table(k) for k in TABLES},
    }


def average(aggs):
    """Mean of the per-day totals (projects and sessions are counted per day, like the day itself)."""
    days = [finalize(a)['totals'] for a in aggs]
    return {k: sum(t[k] for t in days) / max(1, len(days))
            for k in ('cost', 'active_min', 'priced_min', 'sessions', 'prompts', 'calls', 'projects')}


def average_until(aggs, hour):
    """Per-day average of the first `hour` hours (fractional) of these day aggregates."""
    whole, frac = int(hour), hour - int(hour)
    part = lambda xs: sum(xs[:whole]) + (xs[whole] * frac if whole < 24 else 0)
    out = collections.Counter()
    aggs = list(aggs)
    for a in aggs:
        out['active_min'] += part(a['hours'])
        out['priced_min'] += part(a['priced_hours'])
        for k in ('cost', 'prompts', 'calls', 'sessions'):
            out[k] += part(a['hourly'][k])
        out['projects'] += sum(1 for p, hr in a['project_hour'].items() if hr <= whole and not p.startswith('('))
    return {k: out[k] / max(1, len(aggs)) for k in ('cost', 'active_min', 'priced_min', 'sessions', 'prompts', 'calls', 'projects')}


def idle_gaps(conc, start, horizon):
    limit = min(len(conc), max(0, math.ceil((horizon - start) / 60)))
    gaps, run = [], None
    for m in range(limit + 1):
        idle = m < limit and conc[m] == 0
        if idle and run is None:
            run = m
        elif not idle and run is not None:
            if m - run >= IDLE_MIN:
                gaps.append({'start': start + run * 60, 'end': start + m * 60, 'minutes': m - run})
            run = None
    return gaps


def turns_of(rows):
    """Split a session at each human prompt; each turn owns the calls that followed it."""
    turns, cur = [], None

    def new(ts, text, project):
        return {'ts': ts, 'text': text, 'project': project, 'last': ts, 'calls': 0, 'tools': 0,
                'cost': 0.0, 'unpriced_calls': 0, 'output': 0, 'tokens': 0, 'context': 0, 'subagent_calls': 0}

    for r in rows:
        if r['kind'] == 'prompt' and is_local_command(r['text']):
            continue  # /model, /clear…: no request of its own; later calls stay with the turn before
        if r['kind'] == 'prompt':
            cur = new(r['ts'], r['text'], r['project'])
            turns.append(cur)
            continue
        if cur is None:  # calls before the first prompt: a resumed or headless session
            cur = new(r['ts'], None, r['project'])
            turns.append(cur)
        cost, _, priced = call_cost(r)
        n = r.get('calls') or 1
        cur['calls'] += n
        cur['tools'] += r.get('tools') or 0
        cur['cost'] += cost
        cur['output'] += r.get('output') or 0
        cur['tokens'] += tokens_of(r)
        cur['context'] = max(cur['context'], sum(r.get(k) or 0 for k in
                                                 ('input', 'cache_read', 'cache_write_5m', 'cache_write_1h')))
        cur['subagent_calls'] += n if r.get('subagent') else 0
        cur['unpriced_calls'] += 0 if priced else n
        cur['last'] = max(cur['last'], r['ts'])
    for t in turns:
        t['cost'] = round(t['cost'], 4)
    return turns
