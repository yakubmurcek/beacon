"""Labs: ask Beacon in words, get the answer drawn as live cards (http://beacon/labs).

The bet (2026-11-22): software ends up as a conversation, typed or spoken, with the answer rendered on the fly
instead of a hand-built dashboard; the layout is remembered and improves itself. This is the first experiment.

- The model never writes markup. It answers in JSONL, one card per line, from a small allow-listed catalog
  (CARD_TYPES), and the page draws each card with textContent (the pattern of Vercel's json-render and Google's A2UI,
  in plain JS because the hub has no build step).
- A card can bind to a named data source (SOURCES) instead of carrying numbers. The server fills it in on every
  render, so a saved answer shows again instantly with today's numbers and no model call.
- Each answer is saved as a view (labs_views). Asking something close to a saved question streams that view first,
  live, then the model refreshes it from current facts, keeping the layout and adding or dropping a card only when
  something changed (a warning, a failing box). What it changed is said on the answer.
- Sonnet through `claude -p` on his subscription, like titles.py (no tools, no settings, no session saved), at most
  LIMIT_RUNNING at once and DAY_CAP a day.
"""

import datetime
import json
import os
import re
import subprocess
import threading
import time
import traceback
import unicodedata

from settings import OWNER_NAME

CLAUDE = os.environ.get('LABS_CLAUDE') or os.path.expanduser('~/.local/bin/claude')
MODEL = os.environ.get('LABS_MODEL', 'claude-sonnet-5-5')
DAY_CAP = int(os.environ.get('LABS_DAY_CAP', '200'))
LIMIT_RUNNING = 2
TIMEOUT = 90
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS labs_views (
  key TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  cards TEXT NOT NULL,          -- the card specs, sources unresolved
  questions TEXT NOT NULL,      -- the questions that led here, newest first
  asks INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL,
  updated REAL NOT NULL,
  changes TEXT NOT NULL DEFAULT '[]'  -- [{at, what}] the model's own notes on how it changed the view
);
CREATE TABLE IF NOT EXISTS labs_calls (at REAL NOT NULL);
'''

CARD_TYPES = ('say', 'number', 'bars', 'line', 'list', 'progress', 'alert', 'suggest', 'act')
TONES = ('good', 'warn', 'bad', '')
STOP = set('a an the and or of to in on for is are was be me my i you it this that what how much many do does did '
           'with at by from show tell give get have has whats je jsem se na v ve a co kolik jak mi mam ma to za '
           'please now right currently'.split())


def fold(s):
    s = unicodedata.normalize('NFKD', str(s or '').lower())
    return ''.join(c for c in s if not unicodedata.combining(c))


def words(s):
    return {w for w in re.findall(r'[a-z0-9]+', fold(s)) if w not in STOP and len(w) > 1}


def similarity(a, b):
    a, b = words(a), words(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def project_names():
    """The plain names the pages show (web/names.js), never folder names."""
    try:
        with open(os.path.join(WEB, 'names.js'), encoding='utf-8') as f:
            return dict(re.findall(r"'([\w.-]+)':\s*'([^']+)'", f.read()))
    except OSError:
        return {}


def clip(s, n):
    s = re.sub(r'\s+', ' ', str(s or '')).strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'


class Labs:
    def __init__(self, store, tasks=None, questions=None, machines=None, rank=None, titles=None, narrator=None, model=None):
        self.store, self.tasks, self.questions, self.machines, self.rank, self.titles, self.narrator = \
            store, tasks, questions, machines, rank, titles, narrator
        self.model = model or self.run_model
        self.cardrules = None  # hub/cardrules.py, set by the server
        self.names = project_names()
        self.running = threading.BoundedSemaphore(LIMIT_RUNNING)
        self.lock = threading.Lock()
        self.cache = {}  # (source, days) -> (at, data)
        self.ranges = {}  # days -> (at, store.range of the last that many days)
        self.range_lock = threading.Lock()
        with store.connect() as con:
            con.executescript(SCHEMA)

    # ------------------------------------------------------------ data sources

    def pname(self, p):
        if not p or p.startswith('('):  # "(projects root)", "(home)": sessions started outside any project
            return 'No project'
        return self.names.get(p) or p.replace('-', ' ').capitalize()

    def range_days(self, days):
        with self.range_lock:  # one build at a time: an ask during the warm-up waits for it instead of redoing it
            hit = self.ranges.get(days)
            if hit and time.time() - hit[0] < 120:
                return hit[1]
            to = datetime.date.fromisoformat(self.store.today())
            frm = to - datetime.timedelta(days=days - 1)
            r = self.store.range(frm.isoformat(), to.isoformat())
            r.pop('sessions', None)
            self.ranges[days] = (time.time(), r)
            return r

    @staticmethod
    def day_label(iso, days):
        d = datetime.date.fromisoformat(iso)
        return d.strftime('%a') if days <= 7 else f'{d.day}.{d.month}.'

    def series(self, days, field, unit, scale=1.0):
        r = self.range_days(days)
        pts = [{'label': self.day_label(d['date'], days), 'value': round((d.get(field) or 0) * scale, 1)}
               for d in r.get('days', [])]
        prev = r.get('previous') or {}
        return {'kind': 'series', 'unit': unit, 'points': pts,
                'total': round((r['totals'].get(field) or 0) * scale, 1),
                'prev': round((prev.get(field) or 0) * scale, 1) if prev else None,
                'span': f'last {days} days'}

    def breakdown(self, days, key, field, unit, scale=1.0, top=7):
        r = self.range_days(days)
        rows = r.get(key) or []
        items = sorted(({'label': self.pname(x.get('name')) if key == 'by_project' else x.get('name') or '?',
                         'value': round((x.get(field) or 0) * scale, 1)} for x in rows), key=lambda x: -x['value'])
        merged = {}
        for x in items:  # two folders can share one plain name
            merged[x['label']] = round(merged.get(x['label'], 0) + x['value'], 1)
        items = [{'label': k, 'value': v} for k, v in sorted(merged.items(), key=lambda kv: -kv[1]) if v > 0]
        if len(items) > top:
            rest = round(sum(x['value'] for x in items[top:]), 1)
            items = items[:top] + [{'label': 'Other', 'value': rest}]
        return {'kind': 'breakdown', 'unit': unit, 'items': items, 'span': f'last {days} days'}

    def models(self, days):
        r = self.range_days(days)
        fam = r.get('cost_by_family') or {}
        items = [{'label': k, 'value': round(v, 1)} for k, v in sorted(fam.items(), key=lambda kv: -kv[1]) if v > 0]
        return {'kind': 'breakdown', 'unit': '$', 'items': items, 'span': f'last {days} days'}

    def sessions_live(self, days=None):
        now = self.store.live_now()
        live = [x for m in now['machines'] for x in m['sessions'] if x.get('state') in ('working', 'waiting')]
        try:
            if self.titles:
                self.titles.annotate(live)
            if self.narrator:
                self.narrator.annotate(now)  # `now` on working sessions: what it does, in one plain sentence
        except Exception:
            traceback.print_exc()
        live.sort(key=lambda x: (x.get('state') != 'working', -(x.get('last') or 0)))
        items = [{'title': clip(x.get('title') or self.pname(x.get('project')), 70),
                  'sub': clip(f"{x.get('machine')} · " + self.doing(x), 110),
                  'tone': 'good' if x['state'] == 'working' else 'warn'} for x in live]
        return {'kind': 'items', 'items': items, 'count': len(items),
                'working': sum(x['state'] == 'working' for x in live)}

    @staticmethod
    def doing(x):
        """A plain line, never a raw shell command or a tool dump."""
        for t in (x.get('now'), x.get('doing')):
            t = str(t or '').strip()
            if t and not re.match(r'^(\$ |Background: |\{|\[)', t):
                return t
        return 'working' if x.get('state') == 'working' else 'waiting for you'

    def machine_list(self, days=None):
        if not self.machines:
            return {'kind': 'items', 'items': [], 'count': 0}
        tone = {'ok': 'good', 'warn': 'warn', 'bad': 'bad'}
        items = [{'title': m['machine'], 'sub': clip(', '.join(m['problems']) or m.get('role') or m['state'], 110),
                  'tone': tone.get(m['state'], '')}
                 for m in self.machines.view()['machines'] if m['state'] not in ('offline', 'asleep') or m.get('always_on')]
        return {'kind': 'items', 'items': items, 'count': len(items),
                'problems': sum(x['tone'] in ('warn', 'bad') for x in items)}

    def ai_jobs(self, days=None):
        """Jobs that run a model by themselves (hub/aijobs.py), priciest first, with model and effort in words."""
        aj = getattr(self, 'aijobs', None)
        if not aj:
            return {'kind': 'items', 'items': [], 'count': 0}
        v = aj.view()
        items = []
        for j in v['jobs'][:15]:
            model = ', '.join(m['name'] for m in j['models'][:2]) or ', '.join(j['asks']['models']) or 'model not known'
            effort = (j['efforts'][0]['name'] if j['efforts'] else (j['asks']['efforts'] or [''])[0])
            cost = f"${j['cost']:.2f}" if j['measured'] else 'not run this week'
            items.append({'title': clip(j['name'], 70),
                          'sub': clip(f"{j['machine']} · {model}{' · ' + effort + ' effort' if effort else ''} · "
                                      f"{j['runs']} runs · {cost}", 110),
                          'tone': 'warn' if j['models'] and j['models'][0]['name'].startswith('Opus') and j['runs'] >= 10 else ''})
        return {'kind': 'items', 'items': items, 'count': len(v['jobs']),
                'week_cost': v['total']['cost'], 'share_of_all': v['total']['share_of_all']}

    def ai_jobs_spend(self, days=None):
        aj = getattr(self, 'aijobs', None)
        jobs = aj.view()['jobs'] if aj else []
        items = [{'label': clip(j['name'], 40), 'value': round(j['cost'], 2)} for j in jobs if j['cost'] > 0]
        if len(items) > 7:
            items = items[:7] + [{'label': 'Other', 'value': round(sum(x['value'] for x in items[7:]), 2)}]
        return {'kind': 'breakdown', 'unit': '$', 'items': items, 'span': 'last 7 days'}

    def decisions(self, days=None):
        qs = self.questions.list('open', limit=100) if self.questions else []
        items = [{'title': clip(q['question'], 110), 'sub': self.pname(q.get('project')),
                  'tone': 'warn' if q.get('blocking') else ''} for q in qs]
        return {'kind': 'items', 'items': items, 'count': len(items)}

    def bad_questions(self, days=30):
        qs = self.questions.bad(time.time() - days * 86400) if self.questions else []
        items = [{'title': clip(q['question'], 110),
                  'sub': ' · '.join(x for x in (('👎 ' + (q.get('flag') or 'flagged by him')) if q.get('flagged_at') else '',
                                                '; '.join(w.split(':')[0] for w in q['lint']), q.get('asked_by') or '',
                                                self.pname(q.get('project'))) if x),
                  'tone': 'warn' if q.get('flagged_at') else ''} for q in qs]
        # What was held back before he saw it, or reworded, and what his 👎 taught (hub/cardrules.py).
        held = {'refused': 'sent back to its agent', 'dropped': 'no card: it asked nothing', 'reworded': 'reworded',
                'learned': '👎 taught a new rule', 'repeat': '👎 on a rule already written'}
        rules = {r['id']: r['text'] for r in self.cardrules.rules()} if self.cardrules else {}
        for c in self.cardrules.checks(time.time() - days * 86400) if self.cardrules else []:
            if c['verdict'] in held:
                rule = next((rules[x] for x in c['rules'] if x in rules), '')
                items.append({'title': clip(c['question'] or '', 110), 'sub': ' · '.join(
                    x for x in (held[c['verdict']], clip(rule or (c.get('why') if c['kind'] != 'ending' else '') or '', 90),
                                c.get('asked_by') or '') if x),
                    'tone': 'warn' if c['verdict'] == 'repeat' else ''})
        return {'kind': 'items', 'items': items, 'count': len(items)}

    def task_items(self, status, due=None):
        ts = self.tasks.list(status, due=due, limit=60) if self.tasks else []
        items = [{'title': clip(t['title'], 100), 'sub': self.pname(t.get('project')) + (f" · due {t['due'][:10]}" if t.get('due') else ''),
                  'tone': 'good' if t.get('status') == 'doing' else ''} for t in ts]
        return {'kind': 'items', 'items': items, 'count': len(items)}

    def tasks_by_project(self, days=None):
        ts = self.tasks.list('open', limit=2000) if self.tasks else []
        counts = {}
        for t in ts:
            k = self.pname(t.get('project'))
            counts[k] = counts.get(k, 0) + 1
        items = [{'label': k, 'value': v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
        if len(items) > 8:
            items = items[:8] + [{'label': 'Other', 'value': sum(x['value'] for x in items[8:])}]
        return {'kind': 'breakdown', 'unit': 'tasks', 'items': items, 'total': len(ts)}

    def rank_signal(self, key, unit, days):
        r = self.rank.get() if self.rank else None
        sig = next((s for s in (r or {}).get('signals', []) if s.get('key') == key), None)
        if not sig:
            return {'kind': 'series', 'unit': unit, 'points': [], 'total': 0, 'prev': None}
        spark = sig.get('spark') or []
        lag = (r or {}).get('lag') or 0
        end = datetime.date.fromisoformat(self.store.today()) - datetime.timedelta(days=lag)
        spark = spark[:len(spark) - lag] if lag else spark  # the last days are not in yet
        spark = spark[-days:]
        pts = [{'label': self.day_label((end - datetime.timedelta(days=len(spark) - 1 - i)).isoformat(), days),
                'value': round(v or 0, 1)} for i, v in enumerate(spark)]
        return {'kind': 'series', 'unit': unit, 'points': pts, 'total': round(sig.get('last7') or 0, 1),
                'prev': round(sig.get('prev7') or 0, 1), 'span': 'last 7 days (numbers arrive about 2 days late)',
                'all_time': round(sig.get('value') or 0, 1), 'how': sig.get('how')}

    def rank_now(self, days=None):
        r = (self.rank.get() if self.rank else None) or {}
        now = r.get('now') or {}
        return {'kind': 'metric', 'value': now.get('label') or '—', 'score': now.get('score'),
                'next': now.get('next'), 'title': now.get('title')}

    SOURCES = {
        'spend_days': ('work value of all agent work per day, in $ at API list price. NOT money spent: he pays a flat '
                       'subscription, so call it "work value", never "spent" or "cost"',
                       lambda s, d: s.series(d, 'cost', '$')),
        'active_days': ('hours he had agents working per day', lambda s, d: s.series(d, 'active_min', 'h', 1 / 60)),
        'prompts_days': ('messages he typed to agents per day', lambda s, d: s.series(d, 'prompts', 'messages')),
        'spend_projects': ('work value (API list price, not money spent) by project, $', lambda s, d: s.breakdown(d, 'by_project', 'cost', '$')),
        'time_projects': ('agent hours by project', lambda s, d: s.breakdown(d, 'by_project', 'active_min', 'h', 1 / 60)),
        'spend_machines': ('work value (API list price, not money spent) by machine, $', lambda s, d: s.breakdown(d, 'by_machine', 'cost', '$')),
        'spend_models': ('work value (API list price, not money spent) by model family, $', lambda s, d: s.models(d)),
        'sessions_live': ('agent sessions open right now: working, or waiting for him', lambda s, d: s.sessions_live()),
        'machines': ('his machines and their problems (disk, failing jobs, offline)', lambda s, d: s.machine_list()),
        'decisions_open': ('decisions agents are waiting on him for', lambda s, d: s.decisions()),
        'bad_questions': ('decision cards he could not answer as asked: his 👎 flags (with his words) and the ones '
                          'the hub caught (no context, nothing to tap, the same words as another card), cards held back or reworded '
                          'before he saw them, the rules his 👎 taught, and who asked them',
                          lambda s, d: s.bad_questions(30)),
        'tasks_doing': ('tasks an agent is working on now', lambda s, d: s.task_items('doing')),
        'tasks_due': ('open tasks due today or overdue', lambda s, d: s.task_items('open', 'today')),
        'tasks_projects': ('open tasks per project', lambda s, d: s.tasks_by_project()),
        'users_days': ('new real users per day: Roblox first-time players + App Store downloads',
                       lambda s, d: s.rank_signal('users', 'users', d)),
        'money_days': ('money earned per day in Kč: Robux at the DevEx rate + App Store + invoices',
                       lambda s, d: s.rank_signal('money', 'Kč', d)),
        'rank': ('his AI Rank league tier, score and what lifts it next', lambda s, d: s.rank_now()),
        'ai_jobs': ('background jobs that run a model by themselves (timers, loops, agents started by agents): '
                    'machine, model, effort, runs and $ this week, priciest first', lambda s, d: s.ai_jobs()),
        'ai_jobs_spend': ('$ this week per background AI job', lambda s, d: s.ai_jobs_spend()),
    }
    DAYS_SOURCES = ('spend_days', 'active_days', 'prompts_days', 'spend_projects', 'time_projects', 'spend_machines',
                    'spend_models', 'users_days', 'money_days')

    def source(self, name, days=7):
        if name not in self.SOURCES:
            return None
        days = max(1, min(int(days or 7), 90)) if name in self.DAYS_SOURCES else 0
        key = (name, days)
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < 30:
            return hit[1]
        try:
            data = self.SOURCES[name][1](self, days or 7)
        except Exception:
            traceback.print_exc()
            return None
        self.cache[key] = (time.time(), data)
        return data

    def warm(self, wait=False):
        """Builds the digest in the background when the page opens (or at start), so his first question doesn't wait."""
        hit = self.ranges.get(30)
        if (hit and time.time() - hit[0] < 45) or not self.lock.acquire(blocking=False):
            return
        if wait:
            try:
                self.digest()
                self.range_days(30)
            except Exception:
                traceback.print_exc()
            finally:
                self.lock.release()
            return

        def run():
            try:
                self.digest()
                self.range_days(30)
            except Exception:
                traceback.print_exc()
            finally:
                self.lock.release()
        threading.Thread(target=run, daemon=True).start()

    def resolve(self, cards):
        """Fresh data on every card bound to a source; literal cards pass as they are."""
        out = []
        for c in cards:
            c = dict(c)
            if c.get('source'):
                c['data'] = self.source(c['source'], c.get('days') or 7)
            out.append(c)
        return out

    def digest(self):
        """What the model knows: every source's current numbers, compact (the raw APIs are 600 KB)."""
        lines = []
        for name, (desc, _) in self.SOURCES.items():
            d = self.source(name, 7)
            if d is None:
                continue
            d30 = None
            if name in ('users_days', 'money_days') or (name in ('spend_days', 'active_days') and self.ranges.get(30)):
                d30 = self.source(name, 30)  # 30 days of agent work only once built: cold, it takes a minute
            small = dict(d)
            if 'items' in small:
                small['items'] = small['items'][:10]
            lines.append(f'- {name}: {desc}\n  now (7 days): {json.dumps(small, ensure_ascii=False, separators=(",", ":"))}')
            if d30:
                lines.append(f'  30 days: total {d30.get("total")} {d30.get("unit")}, previous 30 days {d30.get("prev")}')
        return '\n'.join(lines)

    # ------------------------------------------------------------ views

    @staticmethod
    def view_row(r):
        return {'key': r['key'], 'title': r['title'], 'cards': json.loads(r['cards']),
                'questions': json.loads(r['questions']), 'asks': r['asks'], 'updated': r['updated'],
                'changes': json.loads(r['changes'] or '[]')}

    def views(self):
        with self.store.connect() as con:
            rows = con.execute('SELECT * FROM labs_views ORDER BY asks DESC, updated DESC LIMIT 40').fetchall()
        return [self.view_row(r) for r in rows]

    def view(self, key):
        with self.store.connect() as con:
            r = con.execute('SELECT * FROM labs_views WHERE key = ?', (key,)).fetchone()
        return self.view_row(r) if r else None

    def forget(self, key):
        with self.store.connect() as con:
            n = con.execute('DELETE FROM labs_views WHERE key = ?', (key,)).rowcount
        return {'ok': bool(n)}

    def match(self, q):
        best, score = None, 0.0
        for v in self.views():
            s = max([similarity(q, x) for x in v['questions']] + [similarity(q, v['title'])])
            if s > score:
                best, score = v, s
        return best if score >= 0.5 else None

    def save(self, key, title, cards, question, change):
        now = time.time()
        old = self.view(key)
        qs = [question] + [x for x in (old['questions'] if old else []) if fold(x) != fold(question)]
        changes = (old['changes'] if old else [])
        if change:
            changes = ([{'at': now, 'what': clip(change, 160)}] + changes)[:10]
        with self.store.connect() as con:
            con.execute('''INSERT INTO labs_views (key, title, cards, questions, asks, created, updated, changes)
                           VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                           ON CONFLICT(key) DO UPDATE SET title = excluded.title, cards = excluded.cards,
                             questions = excluded.questions, asks = asks + 1, updated = excluded.updated,
                             changes = excluded.changes''',
                        (key, clip(title, 60), json.dumps(cards, ensure_ascii=False), json.dumps(qs[:8], ensure_ascii=False),
                         now, now, json.dumps(changes, ensure_ascii=False)))

    def bump(self, key):
        with self.store.connect() as con:
            con.execute('UPDATE labs_views SET asks = asks + 1 WHERE key = ?', (key,))

    # ------------------------------------------------------------ the model

    def calls_today(self):
        start = datetime.datetime.combine(datetime.date.fromisoformat(self.store.today()), datetime.time()).timestamp() - 3600
        with self.store.connect() as con:
            con.execute('DELETE FROM labs_calls WHERE at < ?', (time.time() - 3 * 86400,))
            return con.execute('SELECT COUNT(*) FROM labs_calls WHERE at >= ?', (start,)).fetchone()[0]

    def prompt(self, q, history, cached, saved):
        skeleton = [{'key': v['key'], 'title': v['title'], 'asked': v['questions'][:2]} for v in saved[:20]]
        hist = '\n'.join(f'- he asked: {clip(h.get("q"), 200)} → you showed: {clip(h.get("title"), 60)}; said: {clip(h.get("say"), 200)}'
                         for h in (history or [])[-4:] if isinstance(h, dict)) or '(nothing yet)'
        prev = (f'You answered a question like this before. The layout he saw last time (keep it: same cards, same order, '
                f'unless today\'s facts call for adding a card, e.g. a new warning, or dropping one that is now empty):\n'
                f'{json.dumps({"key": cached["key"], "title": cached["title"], "cards": cached["cards"]}, ensure_ascii=False)}\n'
                if cached else '')
        return f'''You are Beacon, {OWNER_NAME}'s AI studio assistant. He runs a one-person studio where AI agents build games, apps and
client sites on his machines. He talks to you (often by voice, on his phone) and you answer by DRAWING the answer as
cards, not by writing a report. He has ADHD: first the answer in one short spoken-style sentence, then the visuals
that prove it. Visual beats text. Plain words, no ids, no folder names, no jargon.

Today is {self.store.today()} (Europe/Prague).

LIVE DATA (sources you can bind cards to; the page fills them with fresh numbers on every view):
{self.digest()}

CARDS (one JSON object per line). Use "source" (and "days" for day-based sources) whenever a source holds the
numbers, so the card stays live; use literal values only for something no source holds.
- {{"type":"say","text":"…"}}  the answer itself, 1-2 short sentences he could hear read aloud. Always first. Use real numbers from LIVE DATA.
- {{"type":"number","title":"…","source":"…","days":7}}  big number (series: total vs previous period; items: count; metric: value).
   literal: {{"type":"number","title":"…","value":"12","unit":"tasks","note":"…"}}
- {{"type":"bars","title":"…","source":"…","days":7}}  bars with the value written on each bar (series per day, or a breakdown).
   literal: {{"type":"bars","title":"…","unit":"…","items":[{{"label":"…","value":3}}]}}
- {{"type":"line","title":"…","source":"…","days":30}}  a trend over days.
- {{"type":"list","title":"…","source":"…","limit":5}}  short list of items (tone colours each row). literal items: [{{"title":"…","sub":"…","tone":"good|warn|bad"}}]
- {{"type":"progress","title":"…","value":3,"max":10,"label":"…"}}  progress toward something.
- {{"type":"alert","tone":"bad|warn|good","text":"…"}}  something he must not miss. Only when it's real.
- {{"type":"suggest","items":["…","…"]}}  2-3 short follow-up questions he might ask next, in the language of HIS QUESTION below. Always last.
Optional on number/bars/line/list/progress: "size":"half" (two half cards sit side by side; use them in pairs).

RULES
- 3 to 6 cards between say and suggest. Pick the visual that answers at a glance. Never repeat the same fact twice.
- Answer in the language he asked in (Czech or English). Titles short (2-5 words).
- If the data can't answer it, say so in "say" and show the closest thing you do have.
- The one thing you can do: answer an open decision for him (OPEN DECISIONS below), only when he clearly says which
  one and what answer. Add {{"type":"act","decision":<id>,"answer":"<an option's exact label, or his own words>",
  "text":"<read-back he hears, e.g. 'Answer the voice question with: keep the American voice.'>"}} right before suggest.
  The page reads the act text back and asks for his yes itself: your say line only states what you understood,
  never asks to confirm and never says it is done. Anything else (send, pay, submit, add
  tasks): say you can't do that by voice yet and show what you know.

OUTPUT: JSONL only, no prose, no code fences. FIRST line:
{{"view":"<short-kebab-key>","title":"<2-4 word name for this view>","changed":"<only when refreshing a previous layout: a few words on what you added or dropped and why, else empty>"}}
then the cards, one per line. Reuse an existing key when his question means the same as a saved view.

OPEN DECISIONS you may answer (id: question → options; risky ones need his tap and are not here): {self.open_decisions()}

SAVED VIEWS (his earlier questions): {json.dumps(skeleton, ensure_ascii=False)}
{prev}
CONVERSATION SO FAR:
{hist}

HIS QUESTION: {q}'''

    def open_decisions(self):
        qs = [q for q in (self.questions.list('open', limit=30) if self.questions else []) if not q.get('blocking')]
        return '; '.join(f"{q['id']}: {clip(q['question'], 140)} → "
                         + (' | '.join(o.get('label', '') for o in q.get('options') or []) or 'his own words')
                         for q in qs) or '(none)'

    def run_model(self, prompt):
        """Yields text pieces as Sonnet writes them."""
        p = subprocess.Popen([CLAUDE, '-p', prompt, '--model', MODEL, '--setting-sources', '', '--tools', '',
                              '--strict-mcp-config', '--no-chrome', '--no-session-persistence',
                              '--output-format', 'stream-json', '--verbose', '--include-partial-messages'],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True,
                             cwd='/tmp', env={**os.environ, 'HOME': os.path.expanduser('~'), 'TASKS_HOOKS': '0'})
        timer = threading.Timer(TIMEOUT, p.kill)
        timer.start()
        try:
            for line in p.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get('type') == 'stream_event':
                    e = ev.get('event') or {}
                    if e.get('type') == 'content_block_delta' and (e.get('delta') or {}).get('type') == 'text_delta':
                        yield e['delta']['text']
        finally:
            timer.cancel()
            if p.poll() is None:
                p.kill()
            p.wait()

    @staticmethod
    def clean_card(c):
        """Only catalog cards with plain values reach the page."""
        if not isinstance(c, dict) or c.get('type') not in CARD_TYPES:
            return None
        out = {'type': c['type']}
        for k in ('title', 'text', 'unit', 'note', 'label', 'size', 'source'):
            if isinstance(c.get(k), (str, int, float)) and not isinstance(c.get(k), bool):
                out[k] = clip(c[k], 300)
        if out.get('source') and out['source'] not in Labs.SOURCES:
            out.pop('source')
        if out.get('size') not in (None, 'half'):
            out.pop('size')
        tone = c.get('tone') if c.get('tone') in TONES else None
        if tone:
            out['tone'] = tone
        if out['type'] == 'act':  # a decision to answer, read back on the page and done only on his yes
            if not isinstance(c.get('decision'), int) or isinstance(c.get('decision'), bool) or not str(c.get('answer') or '').strip():
                return None
            out['decision'] = c['decision']
            out['answer'] = clip(c['answer'], 300)
        for k in ('days', 'limit'):
            if isinstance(c.get(k), (int, float)) and not isinstance(c.get(k), bool):
                out[k] = max(1, min(int(c[k]), 90))
        for k in ('value', 'max'):
            if isinstance(c.get(k), (int, float, str)) and not isinstance(c.get(k), bool):
                out[k] = c[k] if not isinstance(c[k], str) else clip(c[k], 40)
        if isinstance(c.get('items'), list):
            items = []
            for x in c['items'][:12]:
                if isinstance(x, str):
                    items.append(clip(x, 120))
                elif isinstance(x, dict):
                    y = {k: clip(x[k], 160) for k in ('title', 'sub', 'label') if isinstance(x.get(k), (str, int, float))}
                    if isinstance(x.get('value'), (int, float)) and not isinstance(x.get('value'), bool):
                        y['value'] = x['value']
                    if x.get('tone') in TONES:
                        y['tone'] = x['tone']
                    items.append(y)
            out['items'] = items
        if out['type'] in ('say', 'alert', 'act') and not out.get('text'):
            return None
        return out

    def ask(self, q, history=None, key=None):
        """Yields events for the page, one dict per line: view, card, refresh, done or error."""
        q = clip(q, 500)
        if not q:
            yield {'type': 'error', 'text': 'Ask something first.'}
            return
        cached = self.view(key) if key else self.match(q)
        if cached:
            yield {'type': 'view', 'key': cached['key'], 'title': cached['title'], 'cached': True,
                   'updated': cached['updated'], 'changes': cached['changes'][:1]}
            for c in self.resolve(cached['cards']):
                yield {'type': 'card', 'card': c}
            yield {'type': 'cached_done'}
        if self.calls_today() >= DAY_CAP:
            if cached:
                self.bump(cached['key'])
            yield {'type': 'error', 'text': f'Labs has used its {DAY_CAP} answers for today. Back tomorrow.'}
            return
        if not self.running.acquire(timeout=20):
            yield {'type': 'error', 'text': 'Still answering your last question. Try again in a moment.'}
            return
        try:
            with self.store.connect() as con:
                con.execute('INSERT INTO labs_calls (at) VALUES (?)', (time.time(),))
            prompt = self.prompt(q, history, cached, self.views())
            buf, head, cards, started = '', None, [], False
            for piece in self.model(prompt):
                buf += piece
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    ev = self.line(line, head, cards)
                    if ev is None:
                        continue
                    if ev['type'] == 'view':
                        head = ev
                        if not started:
                            started = True
                            yield {**ev, 'refresh': bool(cached), 'same': bool(cached) and ev['key'] == cached['key']}
                    else:
                        yield ev
            ev = self.line(buf, head, cards)
            if ev and ev['type'] == 'card':
                yield ev
            if cards and not head:  # cards with no first line: name the view after the question
                head = {'key': '-'.join(sorted(words(q)))[:48] or 'answer', 'title': clip(q, 40), 'changed': ''}
            if not head or not cards:
                yield {'type': 'error', 'text': 'The model gave no answer this time. Try again.'}
                return
            self.save(head['key'], head['title'], [c for c in cards if c['type'] != 'act'], q, head.get('changed'))  # an act is for now only
            yield {'type': 'done', 'key': head['key']}
        except Exception:
            traceback.print_exc()
            yield {'type': 'error', 'text': 'Something broke while answering. Try again.'}
        finally:
            self.running.release()

    def line(self, line, head, cards):
        line = line.strip().strip('`').strip()
        if not line or not line.startswith('{'):
            return None
        try:
            obj = json.loads(line)
        except ValueError:
            return None
        if head is None and isinstance(obj, dict) and 'view' in obj:
            k = re.sub(r'[^a-z0-9-]+', '-', fold(obj.get('view'))).strip('-')[:48] or 'answer'
            return {'type': 'view', 'key': k, 'title': clip(obj.get('title') or 'Answer', 60),
                    'changed': clip(obj.get('changed') or '', 160)}
        c = self.clean_card(obj)
        if not c:
            return None
        cards.append(c)
        return {'type': 'card', 'card': self.resolve([c])[0]}
