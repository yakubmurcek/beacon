"""Notices: the one way anything reaches the owner's phone, and the rules that keep it to a handful a day.

Every sender (the `notify` / `telegram-alarm` command on each box, the hub's own machine and talk alarms, the
Outreach move that finished) posts a notice here. The notice always lands in the inbox on http://beacon/inbox;
whether it also buzzes the phone is decided here, never by the sender:

  urgent  his hands are needed now (a box down, a login dead). Pushed at once with sound, like Apple's
          time-sensitive level, day or night, but at most URGENT_CAP a day, never twice in URGENT_GAP, and the
          same trouble (key or topic) buzzes as urgent once a day only.
  normal  something finished or worth knowing. Never pushed alone: held, then sent as one quiet digest at most
          every DIGEST_GAP, only in waking hours, after a burst has had BATCH seconds to gather.
  inbox   kept for the record only (a self-fixed failure, a 🟢 "fixed" line). Never pushed.

On top of that: one push budget a day (DAY_CAP) for both levels; a repeat of the same key within its `every`, or of
the same words within REPEAT, only counts on the first notice; a 🟢 resolved notice withdraws the unpushed ones
with its key. Whatever a cap holds back waits in the inbox and rides the next digest.

The channel is pluggable: Web Push to Beacon's home-screen app (hub/webpush.py), else the legacy Telegram sender.

GET  /api/notify            the inbox: notices, unread count, pushes, a 7-day send log, the rules
POST /api/notify            {level, title, body, link, source, key, every}  -> the notice and what happened to it
POST /api/notify/read       {ids: [...]} or {all: true}
"""
import json
import os
import shutil
import subprocess
import threading
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from settings import TIMEZONE

LEVELS = ('urgent', 'normal', 'inbox')
DAY_CAP = 8            # pushes a day, both levels together: "not a notification every 5 minutes"
URGENT_CAP = 4         # of those, urgent ones
URGENT_GAP = 15 * 60   # two urgent pushes at least this far apart (a burst waits and goes as one)
DIGEST_GAP = 3 * 3600  # normal notices reach the phone at most this often
BATCH = 10 * 60        # a digest waits this long after the first held notice, so a burst lands as one push
WAKE = (8, 22)         # local hours a digest may go out; urgent ignores them
REPEAT = 6 * 3600      # the same topic again within this is a repeat, not a new notice
TOPIC_WORDS = 5        # a title's first words are its topic ("🔴 Claude login needs a" refresh on <any box list>)
KEEP_DAYS = 60
DIGEST_LINES = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  level TEXT NOT NULL,              -- urgent | normal | inbox
  title TEXT NOT NULL,              -- line 1: emoji + plain headline
  body TEXT NOT NULL DEFAULT '',
  link TEXT NOT NULL DEFAULT '',    -- where a tap goes (a Beacon path or a full URL)
  source TEXT NOT NULL DEFAULT '',  -- who sent it ("🖥️ devbox", "Outreach")
  key TEXT NOT NULL DEFAULT '',
  topic TEXT NOT NULL DEFAULT '',
  repeats INTEGER NOT NULL DEFAULT 0,
  alone INTEGER NOT NULL DEFAULT 0, -- his own tap's result: goes out by itself, not in a digest
  state TEXT NOT NULL,              -- held | pushed | kept (inbox only) | resolved | dropped
  push_id INTEGER,
  read_at REAL
);
CREATE INDEX IF NOT EXISTS notices_state ON notices(state, created_at);
CREATE INDEX IF NOT EXISTS notices_key ON notices(key, created_at);
CREATE INDEX IF NOT EXISTS notices_topic ON notices(topic, created_at);
CREATE TABLE IF NOT EXISTS pushes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  level TEXT NOT NULL,              -- urgent | digest
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  link TEXT NOT NULL DEFAULT '',
  count INTEGER NOT NULL,
  channel TEXT NOT NULL,
  ok INTEGER NOT NULL,
  error TEXT
);
CREATE INDEX IF NOT EXISTS pushes_at ON pushes(at);
"""

RESOLVED_MARKS = ('🟢', '✅')


def clean(text, limit, lines=False):
    """Markdown bold off (a phone shows it raw); one line unless `lines`."""
    text = str(text or '').replace('**', '').replace('__', '')
    return (text.strip() if lines else ' '.join(text.split()))[:limit]


class Notify:
    def __init__(self, store, send=None, tz=TIMEZONE, base='', channel_name=None):
        self.store = store
        self.channel_name = channel_name or (lambda: 'none')  # what the inbox says pushes go through
        self.send = send  # f(kind, title, body, link) -> (ok, channel, error); None: nothing leaves the hub
        self.tz = ZoneInfo(tz)
        self.base = base.rstrip('/')  # http://<hub>: a push's link must work from the phone
        self.lock = threading.Lock()  # a notice's dedupe and insert; never held while a push is sent
        self.sending = threading.Lock()  # one flush at a time (the minute loop and an urgent post's thread)
        self.inline = False  # tests: an urgent post flushes before it returns
        with store.connect() as con:
            con.executescript(SCHEMA)
            if 'alone' not in {r[1] for r in con.execute('PRAGMA table_info(notices)')}:
                con.execute('ALTER TABLE notices ADD COLUMN alone INTEGER NOT NULL DEFAULT 0')

    # ------------------------------------------------------------ in

    def post(self, n, now=None):
        """File one notice; urgent ones are pushed before this returns (when the caps allow)."""
        if not isinstance(n, dict):
            raise ValueError('body: expected a JSON object')
        now = now or time.time()
        text = str(n.get('text') or '')
        title = n.get('title') or text.split('\n', 1)[0]
        body = n.get('body') if n.get('title') else (text.split('\n', 1)[1] if '\n' in text else '')
        title, body = clean(title, 160), clean(body, 2000, lines=True)
        if not title:
            raise ValueError('title: required')
        level = n.get('level') or 'normal'
        if level not in LEVELS:
            raise ValueError(f'level: one of {", ".join(LEVELS)}')
        key = clean(n.get('key'), 120)
        every = n.get('every')
        every = int(every) if str(every or '').isdigit() else 0
        resolved = bool(n.get('resolved')) or title.startswith(RESOLVED_MARKS[0])
        if resolved:
            level = 'inbox'  # a self-fixed failure never pings
        row = {'created_at': now, 'level': level, 'title': title, 'body': body, 'link': clean(n.get('link'), 500),
               'source': clean(n.get('source'), 80), 'key': key, 'topic': topic(title), 'alone': int(bool(n.get('alone'))),
               'state': 'kept' if level == 'inbox' else 'held'}
        with self.lock, self.store.connect() as con:
            if resolved and key:  # its alarm, still waiting for a digest, is old news now
                base = key[:-3] if key.endswith(':ok') else key
                con.execute("UPDATE notices SET state = 'resolved' WHERE state = 'held' AND key IN (?, ?)", (base, key))
            same = None
            if key and every:
                same = con.execute('SELECT id FROM notices WHERE key = ? AND created_at > ? ORDER BY id DESC LIMIT 1',
                                   (key, now - every)).fetchone()
            if not same and not resolved:
                same = con.execute('SELECT id FROM notices WHERE topic = ? AND created_at > ? ORDER BY id DESC LIMIT 1',
                                   (row['topic'], now - REPEAT)).fetchone()
            if same:
                con.execute('UPDATE notices SET repeats = repeats + 1 WHERE id = ?', (same['id'],))
                return dict(self.get(same['id'], con), repeat=True)
            if level == 'urgent' and con.execute(  # the same trouble already buzzed today: its slot stays for new ones
                    "SELECT 1 FROM notices WHERE level = 'urgent' AND state = 'pushed' AND created_at > ? "
                    "AND (topic = ? OR (key != '' AND key = ?)) LIMIT 1", (now - 86400, row['topic'], key)).fetchone():
                row['level'] = level = 'normal'
            cur = con.execute('INSERT INTO notices (created_at, level, title, body, link, source, key, topic, alone, state) '
                              'VALUES (:created_at, :level, :title, :body, :link, :source, :key, :topic, :alone, :state)',
                              row)
            nid = cur.lastrowid
        if level == 'urgent' or row['alone']:  # sent behind the request: a slow push never holds up the sender
            if self.inline:
                self.flush(now)
            else:
                threading.Thread(target=self.flush, daemon=True, name='notify-now').start()
        with self.store.connect() as con:
            return self.get(nid, con)

    def get(self, nid, con):
        r = con.execute('SELECT * FROM notices WHERE id = ?', (nid,)).fetchone()
        return dict(r) if r else None

    def read(self, ids=None, everything=False, now=None):
        now = now or time.time()
        with self.store.connect() as con:
            if everything:
                cur = con.execute('UPDATE notices SET read_at = ? WHERE read_at IS NULL', (now,))
            else:
                ids = [int(i) for i in (ids or []) if str(i).isdigit()][:500]
                cur = con.execute('UPDATE notices SET read_at = ? WHERE read_at IS NULL AND id IN '
                                  '(SELECT value FROM json_each(?))', (now, json.dumps(ids)))
            return {'read': cur.rowcount}

    # ------------------------------------------------------------ out

    def day_start(self, now):
        d = datetime.fromtimestamp(now, self.tz)
        return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def awake(self, now):
        return WAKE[0] <= datetime.fromtimestamp(now, self.tz).hour < WAKE[1]

    def flush(self, now=None):
        """One pass of the rules: push what may go now. The live hub runs it every minute; an urgent post runs it at once."""
        with self.sending:
            now = now or time.time()
            kind, items = self.due(now)
            return self.push(kind, items, now) if items else None

    def due(self, now):
        """(kind, notices) the rules let out now, or (None, [])."""
        with self.store.connect() as con:
            held = [dict(r) for r in con.execute("SELECT * FROM notices WHERE state = 'held' ORDER BY id").fetchall()]
            pushes = [dict(r) for r in con.execute(
                'SELECT at, level FROM pushes WHERE ok = 1 AND at >= ? ORDER BY at', (self.day_start(now),))]
        if not held or len(pushes) >= DAY_CAP:
            return None, []
        sent = [p['at'] for p in pushes if p['level'] == 'urgent']
        urgent = [x for x in held if x['level'] == 'urgent']
        if urgent and len(sent) < URGENT_CAP and now - max(sent, default=0) >= URGENT_GAP:
            return 'urgent', urgent  # day or night: urgent breaks through (his ask)
        if not self.awake(now):
            return None, []
        alone = [x for x in held if x['alone']]
        if alone:  # what his own tap started: by itself and quiet, no digest wait
            return 'alone', alone[:1]
        last = max((p['at'] for p in pushes), default=0)
        if now - last < DIGEST_GAP or now - held[0]['created_at'] < BATCH:
            return None, []
        return 'digest', held

    def push(self, kind, items, now):
        if len(items) == 1:
            x = items[0]
            title, body, link = x['title'], x['body'], x['link'] or '/inbox'
        else:
            title = ('🔴 ' if kind == 'urgent' else '📬 ') + f'{len(items)} updates'
            lines = [x['title'] for x in items[-DIGEST_LINES:]]
            more = len(items) - len(lines)
            body = '\n'.join(lines) + (f'\n… and {more} more in the inbox' if more else '')
            link = '/inbox'
        if link.startswith('/'):
            link = self.base + link
        ok, channel, error = (False, 'none', 'no channel') if not self.send else self.call(kind, title, body, link)
        with self.store.connect() as con:
            cur = con.execute('INSERT INTO pushes (at, level, title, body, link, count, channel, ok, error) '
                              'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                              (now, kind, title, body, link, len(items), channel, int(ok), error))
            if ok:
                ids = [x['id'] for x in items]
                con.execute("UPDATE notices SET state = 'pushed', push_id = ? WHERE id IN (SELECT value FROM json_each(?))",
                            (cur.lastrowid, json.dumps(ids)))
            con.execute('DELETE FROM notices WHERE created_at < ?', (now - KEEP_DAYS * 86400,))
            con.execute('DELETE FROM pushes WHERE at < ?', (now - KEEP_DAYS * 86400,))
        return {'kind': kind, 'count': len(items), 'ok': ok, 'channel': channel, 'error': error}

    def call(self, kind, title, body, link):
        try:
            return self.send(kind, title, body, link)
        except Exception as e:  # a channel down must never take the hub with it; tried again next pass
            traceback.print_exc()
            return False, 'error', str(e)[:200]

    def watch(self, every=60):
        def loop():
            while True:
                time.sleep(every)
                try:
                    self.flush()
                except Exception:
                    traceback.print_exc()
        threading.Thread(target=loop, daemon=True, name='notify').start()

    # ------------------------------------------------------------ the inbox page

    def view(self, now=None, limit=300):
        now = now or time.time()
        with self.store.connect() as con:
            notices = [dict(r) for r in con.execute('SELECT * FROM notices ORDER BY id DESC LIMIT ?', (limit,))]
            unread = con.execute('SELECT COUNT(*) FROM notices WHERE read_at IS NULL').fetchone()[0]
            since = self.day_start(now) - 6 * 86400
            pushes = [dict(r) for r in con.execute('SELECT * FROM pushes WHERE at >= ? ORDER BY id DESC', (since,))]
            made = con.execute('SELECT created_at, level FROM notices WHERE created_at >= ?', (since,)).fetchall()
        days = []
        for i in range(6, -1, -1):
            start = self.day_start(now) - i * 86400
            start = self.day_start(start + 3 * 3600)  # a DST day is 23 or 25 hours long
            end = self.day_start(start + 27 * 3600)
            days.append({
                'day': datetime.fromtimestamp(start, self.tz).date().isoformat(),
                'notices': sum(start <= r[0] < end for r in made),
                'pushes': sum(start <= p['at'] < end and p['ok'] for p in pushes),
                'urgent': sum(start <= p['at'] < end and p['ok'] and p['level'] == 'urgent' for p in pushes),
            })
        return {'now': now, 'unread': unread, 'notices': notices, 'pushes': pushes[:100], 'days': days,
                'channel': self.channel_name() if self.send else 'none',
                'rules': {'day_cap': DAY_CAP, 'urgent_cap': URGENT_CAP, 'digest_gap_h': DIGEST_GAP // 3600,
                          'wake': list(WAKE), 'batch_min': BATCH // 60}}


# ------------------------------------------------------------ channels

def legacy_bin():
    exe = shutil.which('telegram-alarm') or '/usr/local/bin/telegram-alarm'
    return exe if os.access(exe, os.X_OK) else None


def telegram(kind, title, body, link):
    """The old Telegram sender straight (its --direct skips the hub, so this never loops back here): the channel
    until the phone has subscribed to Beacon's own pushes, and the fallback when those fail."""
    exe = legacy_bin()
    if not exe:
        return False, 'none', 'no channel'
    text = title + (f'\n{body}' if body else '') + (f'\n{link}' if link else '')
    args = [exe, '--direct', '--from', '🔔 Beacon'] + (['--quiet'] if kind != 'urgent' else []) + [text]
    r = subprocess.run(args, capture_output=True, text=True, timeout=90, check=False)
    return r.returncode == 0, 'telegram', None if r.returncode == 0 else (r.stderr or r.stdout).strip()[:200]


def first_of(*senders):
    """A sender that tries each in turn until one takes the push."""
    def send(kind, title, body, link):
        got = (False, 'none', 'no channel')
        for f in senders:
            got = f(kind, title, body, link)
            if got[0]:
                return got
        return got
    return send


def topic(title):
    return ' '.join(title.lower().split()[:TOPIC_WORDS])


def from_alarm(text, key='', quiet=False, source='', urgent=False):
    """A notice from an old-style alarm line (the hub's machine and talk alarms): 🔴 that buzzed = urgent, a quiet
    line (news, not a call to act) and a 🟢 = inbox only, everything else waits for the digest."""
    title = str(text).split('\n', 1)[0]
    level = 'urgent' if urgent or (title.startswith('🔴') and not quiet) else 'inbox' if quiet else 'normal'
    return {'text': text, 'key': key, 'source': source, 'level': level}

