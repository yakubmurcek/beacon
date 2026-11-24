"""AI Rank: a League-style rank of build -> ship -> users -> money, at http://beacon/rank.

Revived from the parked league-rank repo (2026-11-22, task 1826), then re-anchored twice that day on what the owner said
a tier is worth: Challenger ~$20,000 a month passive, Grandmaster ~$300 passive, even Diamond needs someone really
paying, and money counts only once it is his: his half of the Robux (split with Steps), and only once the game has
earned the 30,000 Robux DevEx pays out from; minus what went on ads. A live game with paid players and no profit is
"Gold IV: trying but not enough".

  Every gate and LP looks at the last 30 days ("a month"), so the rank falls when users and money stop.
    Iron..Bronze   agent sessions; LP from sessions, tasks done and merged PRs (mechanics)
    Silver         something live a stranger can open (a release: a site's first production deploy of a week,
                   an App Store version, a Roblox update)
    Gold           100 new users (Roblox first-time players + App Store first downloads); LP = how close to break-even
    Platinum       in the black: money in his pocket beat the ad spend
    Emerald        2,000 Kč profit in the month; Diamond 10,000 Kč (a client, real sales)
    Master         passive profit (his Robux share + App Store - ads) ~$100 a month; Grandmaster ~$300; Challenger ~$20,000
  LP inside a tier is how far toward the next gate, plainly linear for money (log only above Grandmaster).

Where the numbers come from: collector/rank.py on the home server (timer ai-rank, hourly) POSTs per-day outside numbers to
/api/rank/ingest; tasks done and agent sessions are read here from the hub's own tables. A source that failed is not
sent, so its days keep the last good values; the page shows it as not read.

Tables: rank_days (day, key, value), rank_releases (key, day, kind, what), rank_meta (k, v).
GET /api/rank: {"now": {tier, division, lp, label, title, score, next, warning}, "signals": [...],
                "history": [{day, tier, lp, score, label}] (31 days), "releases": [...], "sources": {...}, "at"}.
"""
import datetime
import json
import logging
import math
import sqlite3
import threading
import time

log = logging.getLogger('beacon.rank')

TIERS = [  # key, AI-era title, has divisions
    ('IRON', 'Prompt Tourist', True), ('BRONZE', 'Chatbot Native', True), ('SILVER', 'First-Time Claude', True),
    ('GOLD', 'Vibe Coder', True), ('PLATINUM', 'AI Native', True), ('EMERALD', 'Force Multiplier', True),
    ('DIAMOND', 'Solo Studio', True), ('MASTER', 'Market Player', False), ('GRANDMASTER', 'AI Founder', False),
    ('CHALLENGER', 'AI Era Survivor', False),
]
INDEX = {k: i for i, (k, _, _) in enumerate(TIERS)}
DIVISIONS = ['IV', 'III', 'II', 'I']
OUTSIDE = ('roblox_new', 'roblox_dau', 'robux', 'robux_kc', 'appstore_new', 'appstore_kc', 'invoiced_kc', 'ads_kc', 'deploys', 'prs',
           # read only by the Team page's outcome numbers (hub/outcomes.py), never by the score
           'appstore_paid', 'appstore_seen', 'roblox_payers', 'roblox_visits', 'tiktok_views', 'emails_sent', 'calls', 'bank_in')
KEEP_DAYS = 120
HISTORY = 31
SOURCE_NAMES = {'roblox': 'Roblox', 'appstore': 'App Store', 'vercel': 'Vercel', 'github': 'GitHub',
                'invoiced': 'Invoiced (Lighthouse)', 'hub': 'Beacon'}  # the rank's own (mail, calls, bank, reach feed Team)

SCHEMA = """
CREATE TABLE IF NOT EXISTS rank_days (day TEXT NOT NULL, key TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY (day, key));
CREATE TABLE IF NOT EXISTS rank_releases (key TEXT PRIMARY KEY, day TEXT NOT NULL, kind TEXT, what TEXT);
CREATE TABLE IF NOT EXISTS rank_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def _log_lp(x, full):
    """0..100: grows fast at first, `full` reaches 100."""
    if x <= 0:
        return 0
    return max(0, min(100, int(math.log1p(x) / math.log1p(full) * 100)))


USERS = ('roblox_new', 'appstore_new')
ROBUX_SHARE = 0.5                              # Clean the Snow is split with Steps
DEVEX_MIN = 30_000                             # Robux are not money until the game has earned enough to cash out
PROFIT_PLATINUM = 0                            # in the black: more than 0 Kč after ads
PROFIT_EMERALD = 2_000
PROFIT_DIAMOND = 10_000                        # Kč profit in a month: someone really paying
PASSIVE_MASTER = 2_200                         # ~$100 a month
PASSIVE_GM = 6_700                             # ~$300 a month
PASSIVE_CHALLENGER = 445_000                   # ~$20,000 a month
USERS_GOLD = 100


def cashable(series, day):
    """True once the Robux earned up to `day` reach the DevEx minimum."""
    return sum((v or {}).get('robux', 0) for d, v in series.items() if d <= day) >= DEVEX_MIN


def day_money(vals, robux_ok):
    """(passive, all) Kč that are really his on one day, before ads."""
    passive = (vals.get('robux_kc', 0) * ROBUX_SHARE if robux_ok else 0) + vals.get('appstore_kc', 0)
    return passive, passive + vals.get('invoiced_kc', 0)


def metrics_for(series, day, lag=0):
    """30-day sums ending on `day` (inclusive) from {day: {key: value}}. `lag` is kept for callers."""
    d = datetime.date.fromisoformat(day)
    robux_ok = cashable(series, day)
    out = dict.fromkeys(('sessions', 'tasks', 'prs', 'releases', 'users', 'robux', 'spend', 'passive', 'money'), 0.0)
    for i in range(30):
        vals = series.get((d - datetime.timedelta(days=i)).isoformat()) or {}
        for k in ('sessions', 'tasks', 'prs', 'releases', 'robux'):
            out[k] += vals.get(k, 0)
        out['users'] += sum(vals.get(k, 0) for k in USERS)
        out['spend'] += vals.get('ads_kc', 0)
        p, a = day_money(vals, robux_ok)
        out['passive'] += p
        out['money'] += a
    out['profit'] = out['money'] - out['spend']
    out['passive_profit'] = out['passive'] - out['spend']
    return out


def decide_tier(m):
    if m['passive_profit'] >= PASSIVE_CHALLENGER:
        return 'CHALLENGER'
    if m['passive_profit'] >= PASSIVE_GM:
        return 'GRANDMASTER'
    if m['passive_profit'] >= PASSIVE_MASTER:
        return 'MASTER'
    if m['profit'] >= PROFIT_DIAMOND:
        return 'DIAMOND'
    if m['profit'] >= PROFIT_EMERALD:
        return 'EMERALD'
    if m['profit'] > PROFIT_PLATINUM:
        return 'PLATINUM'
    if m['users'] >= USERS_GOLD:
        return 'GOLD'
    if m['releases'] >= 1:
        return 'SILVER'
    if m['sessions'] >= 1 or m['prs'] >= 1:  # mechanics alone stop here: the grind ceiling
        return 'BRONZE'
    return 'IRON'


def _part(x, lo, hi):
    """0..99: linear share of the way from lo to hi."""
    return max(0, min(99, int((x - lo) / (hi - lo) * 100)))


def lp_for(tier, m):
    if tier in ('IRON', 'BRONZE'):  # below the ceiling every mechanic counts
        return _log_lp(m['sessions'] * 0.5 + m['tasks'] * 2 + m['prs'] * 8, 600)
    if tier == 'SILVER':
        return _part(m['users'], 0, USERS_GOLD)
    if tier == 'GOLD':  # how close to break-even: money that is his against what went on ads
        return _part(m['money'], 0, m['spend']) if m['spend'] > 0 else 0
    if tier == 'PLATINUM':
        return _part(m['profit'], 0, PROFIT_EMERALD)
    if tier == 'EMERALD':
        return _part(m['profit'], PROFIT_EMERALD, PROFIT_DIAMOND)
    if tier == 'DIAMOND':
        return _part(m['passive_profit'], 0, PASSIVE_MASTER)
    if tier == 'MASTER':
        return _part(m['passive_profit'], PASSIVE_MASTER, PASSIVE_GM)
    if tier == 'GRANDMASTER':  # $300 to $20,000 a month: log, or it would sit at 0 for years
        return max(0, min(99, int(math.log(m['passive_profit'] / PASSIVE_GM) / math.log(PASSIVE_CHALLENGER / PASSIVE_GM) * 100)))
    return _log_lp(m['passive_profit'] - PASSIVE_CHALLENGER, PASSIVE_CHALLENGER * 4)


def division_for(tier, lp):
    if not TIERS[INDEX[tier]][2]:
        return None
    return DIVISIONS[min(3, lp // 25)]


def score(m):
    tier = decide_tier(m)
    lp = lp_for(tier, m)
    div = division_for(tier, lp)
    name = tier.capitalize() if tier != 'GRANDMASTER' else 'Grandmaster'
    return {'tier': tier, 'division': div, 'lp': lp, 'title': TIERS[INDEX[tier]][1],
            'label': f'{name} {div}' if div else name, 'score': INDEX[tier] * 100 + lp}


def next_step(tier, m):
    if tier == 'GOLD':
        if m['robux'] > 0 and m['money'] == 0:
            return 'Earn more than you spend on ads, in money you can cash out → Platinum'
        return 'Earn more than you spend on ads → Platinum'
    return {
        'IRON': 'Start building → Bronze',
        'BRONZE': 'Put something live that a stranger can open → Silver',
        'SILVER': f'Get {USERS_GOLD} real users in a month → Gold',
        'PLATINUM': 'Make 2,000 Kč profit in a month → Emerald',
        'EMERALD': 'Make 10,000 Kč profit in a month (a client or sales) → Diamond',
        'DIAMOND': 'Make ~$100 a month passive profit (apps, games) → Master',
        'MASTER': 'Make ~$300 a month passive profit → Grandmaster',
        'GRANDMASTER': 'Make ~$20,000 a month passive profit → Challenger',
    }.get(tier, 'Keep it growing')


WARN_KEYS = {'SILVER': (('releases',), 'release'), 'GOLD': (USERS, 'new user'),
             'PLATINUM': (('robux_kc', 'appstore_kc', 'invoiced_kc'), 'money'),
             'EMERALD': (('robux_kc', 'appstore_kc', 'invoiced_kc'), 'money'),
             'DIAMOND': (('robux_kc', 'appstore_kc', 'invoiced_kc'), 'money')}


def warning(tier, series, day, lag=0):
    """The day the tier drops if nothing new comes in: walk the coming days with no new numbers and score each.
    Shown only when that is within 3 weeks."""
    if INDEX[tier] < INDEX['SILVER']:
        return None
    d = datetime.date.fromisoformat(day)
    for k in range(1, 22):
        future = (d + datetime.timedelta(days=k)).isoformat()
        if INDEX[decide_tier(metrics_for(series, future))] < INDEX[tier]:
            keys, what = WARN_KEYS.get(tier, (('robux_kc', 'appstore_kc'), 'passive money'))
            last = next(((d - datetime.timedelta(days=i)).isoformat() for i in range(0, 31)
                         if sum((series.get((d - datetime.timedelta(days=i)).isoformat()) or {}).get(x, 0) for x in keys) > 0), None)
            return {'last': last, 'drop': future, 'what': what}
    return None


class Rank:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.state = None
        with store.connect() as con:
            con.executescript(SCHEMA)

    # ------------------------------------------------------------ ingest

    def ingest(self, body):
        if not isinstance(body, dict) or not isinstance(body.get('days', {}), dict):
            raise ValueError('days must be an object')
        rows = []
        for day, vals in (body.get('days') or {}).items():
            datetime.date.fromisoformat(day)
            for k, v in (vals or {}).items():
                if k in OUTSIDE and isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
                    rows.append((day, k, float(v)))
        rels = []
        for r in body.get('releases') or []:
            if isinstance(r, dict) and r.get('key') and r.get('day'):
                datetime.date.fromisoformat(str(r['day'])[:10])
                rels.append((str(r['key'])[:200], str(r['day'])[:10], str(r.get('kind') or '')[:20], str(r.get('what') or '')[:200]))
        sources = {k: v for k, v in (body.get('sources') or {}).items() if isinstance(v, dict)}
        cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
        with self.store.connect() as con:
            con.executemany('INSERT INTO rank_days (day, key, value) VALUES (?, ?, ?) '
                            'ON CONFLICT(day, key) DO UPDATE SET value=excluded.value', rows)
            con.executemany('INSERT INTO rank_releases (key, day, kind, what) VALUES (?, ?, ?, ?) '
                            'ON CONFLICT(key) DO UPDATE SET day=excluded.day, what=excluded.what', rels)
            con.execute('DELETE FROM rank_days WHERE day < ?', (cutoff,))
            con.execute('DELETE FROM rank_releases WHERE day < ?', (cutoff,))
            old = con.execute("SELECT v FROM rank_meta WHERE k='sources'").fetchone()
            merged = json.loads(old[0]) if old else {}
            now = time.time()
            for name, s in sources.items():
                s = dict(s, checked=now)
                if s.get('ok'):
                    s['good_at'] = now
                elif name in merged and merged[name].get('good_at'):
                    s['good_at'] = merged[name]['good_at']  # when it last worked
                merged[name] = s
            con.execute("INSERT OR REPLACE INTO rank_meta (k, v) VALUES ('sources', ?)", (json.dumps(merged),))
            r = body.get('rates')
            if isinstance(r, dict) and all(isinstance(r.get(k), (int, float)) and r[k] > 0 for k in ('USD', 'devex_usd')):
                rates = {'USD': float(r['USD']), 'devex_usd': float(r['devex_usd']), 'from': str(r.get('from') or '')[:40]}
                con.execute("INSERT OR REPLACE INTO rank_meta (k, v) VALUES ('rates', ?)", (json.dumps(rates),))
            con.execute("INSERT OR REPLACE INTO rank_meta (k, v) VALUES ('at', ?)", (json.dumps(now),))
        state = self.recompute()
        return {'ok': True, 'days': len(rows), 'releases': len(rels), 'tier_label': state['now']['label']}

    # ------------------------------------------------------------ read

    def local_days(self, since_ts):
        """Tasks done and agent sessions (with a prompt a person typed) per local day, from the hub's own tables."""
        out = {}
        with self.store.connect() as con:
            try:
                for (ts,) in con.execute("SELECT closed_at FROM tasks WHERE status='done' AND closed_at >= ?", (since_ts,)):
                    d = out.setdefault(self.store.local_date(ts), {})
                    d['tasks'] = d.get('tasks', 0) + 1
            except sqlite3.OperationalError:  # a hub without the tasks tables (tests)
                pass
            seen = set()
            for ts, machine, session in con.execute(
                    "SELECT min(ts), machine, session FROM events WHERE kind='prompt' AND ts >= ? GROUP BY machine, session",
                    (since_ts,)):
                if (machine, session) in seen:
                    continue
                seen.add((machine, session))
                d = out.setdefault(self.store.local_date(ts), {})
                d['sessions'] = d.get('sessions', 0) + 1
        return out

    def recompute(self):
        today = datetime.date.fromisoformat(self.store.today())
        start = today - datetime.timedelta(days=HISTORY + 40)
        since_ts = datetime.datetime.combine(start, datetime.time(), tzinfo=self.store.tz).timestamp()
        series = self.local_days(since_ts)
        with self.store.connect() as con:
            for day, key, value in con.execute('SELECT day, key, value FROM rank_days WHERE day >= ?', (start.isoformat(),)):
                series.setdefault(day, {})[key] = value
            releases = [dict(r) for r in con.execute(
                'SELECT key, day, kind, what FROM rank_releases WHERE day >= ? ORDER BY day DESC, key', (start.isoformat(),))]
            meta = {k: json.loads(v) for k, v in con.execute('SELECT k, v FROM rank_meta')}
        for r in releases:
            d = series.setdefault(r['day'], {})
            d['releases'] = d.get('releases', 0) + 1
        # how far the user numbers run behind: the older edge of Roblox's and Apple's last reported day
        srcs = meta.get('sources') or {}
        edges = [srcs[n]['through'] for n in ('roblox', 'appstore') if isinstance((srcs.get(n) or {}).get('through'), str)]
        try:
            lag = max(0, min(7, (today - datetime.date.fromisoformat(min(edges))).days)) if edges else 0
        except ValueError:
            lag = 0
        history = []
        for i in range(HISTORY - 1, -1, -1):
            day = (today - datetime.timedelta(days=i)).isoformat()
            m = metrics_for(series, day)
            history.append(dict(score(m), day=day))
        m = metrics_for(series, today.isoformat(), lag)
        now = dict(score(m), next=next_step(history[-1]['tier'], m),
                   warning=warning(history[-1]['tier'], series, today.isoformat(), lag))
        month_ago = history[0]
        now['change'] = {'from': month_ago['label'], 'score': now['score'] - month_ago['score']}
        state = {'now': now, 'metrics': m, 'lag': lag, 'signals': self.signals(series, today, srcs, lag),
                 'history': history, 'sources': self.source_list(meta.get('sources') or {}),
                 'releases': [r for r in releases if r['day'] >= (today - datetime.timedelta(days=29)).isoformat()],
                 'rates': meta.get('rates'), 'at': meta.get('at'), 'computed_at': time.time(),
                 'roblox_now': (meta.get('sources') or {}).get('roblox', {}).get('now')}
        with self.lock:
            self.state = state
        return state

    def signals(self, series, today, sources, lag=0):
        robux_ok = cashable(series, today.isoformat())

        def spark(fn):
            return [round(fn(series.get((today - datetime.timedelta(days=i)).isoformat()) or {}), 1) for i in range(29, -1, -1)]

        def keys(*ks):
            return lambda v: sum(v.get(k, 0) for k in ks)

        def ok(*names):
            return all((sources.get(n) or {}).get('ok', n == 'hub') for n in names)

        earned = sum((v or {}).get('robux', 0) for d, v in series.items() if d <= today.isoformat())
        rows = [
            ('profit', 'Profit', 'money that is his (his half of cashable Robux, App Store, invoiced) minus ads, in Kč',
             lambda v: day_money(v, robux_ok)[1] - v.get('ads_kc', 0), ('roblox', 'appstore', 'invoiced'), 'Platinum → Diamond', 'kc'),
            ('robux', 'Robux earned', f'Clean the Snow, before the split with Steps. Not money until {DEVEX_MIN:,} to cash out: '
             f'{earned:,.0f} so far' + ('' if robux_ok else ' (not cashable yet)'), keys('robux'), ('roblox',), None, 'count'),
            ('ads', 'Spent on ads', 'Roblox ads, entered by hand (Ads Manager is not readable)', keys('ads_kc'), ('hub',), None, 'kc'),
            ('users', 'New users', 'Roblox first-time players + App Store first downloads', keys(*USERS),
             ('roblox', 'appstore'), 'Gold', 'count'),
            ('releases', 'Releases', 'a site\'s first production deploy of a week, App Store versions, Roblox updates',
             keys('releases'), ('vercel', 'appstore', 'roblox'), 'Silver', 'count'),
            ('players', 'Roblox daily players', 'Clean the Snow, average a day', keys('roblox_dau'), ('roblox',), None, 'avg'),
            ('tasks', 'Tasks done', 'closed in Beacon (mechanics: count only below Silver)', keys('tasks'), ('hub',), None, 'count'),
            ('prs', 'PRs merged', 'his merged pull requests on GitHub (mechanics)', keys('prs'), ('github',), None, 'count'),
            ('sessions', 'Agent sessions', 'sessions with a prompt he typed (mechanics)', keys('sessions'), ('hub',), None, 'count'),
        ]
        out = []
        for key, label, how, fn, srcs, unlocks, unit in rows:
            sp = spark(fn)
            total = sum(sp)
            behind = lag if key in ('users', 'profit', 'robux', 'players') else 0  # compare whole weeks of known data
            wk = sp[:len(sp) - behind] if behind else sp
            out.append({'key': key, 'label': label, 'how': how, 'unlocks': unlocks, 'unit': unit,
                        'value': round(total / 30, 1) if unit == 'avg' else round(total, 1),
                        'last7': round(sum(wk[-7:]), 1), 'prev7': round(sum(wk[-14:-7]), 1), 'behind': behind,
                        'spark': sp, 'ok': ok(*srcs)})
        return out

    def source_list(self, sources):
        out = []
        for name, label in SOURCE_NAMES.items():
            if name == 'hub':
                continue
            s = sources.get(name)
            out.append({'name': label, 'ok': bool(s and s.get('ok')), 'checked': (s or {}).get('checked'),
                        'good_at': (s or {}).get('good_at'), 'why': (s or {}).get('why')})
        return out

    def get(self):
        with self.lock:
            st = self.state
        # tasks and sessions are the hub's own: fresh within 5 minutes even between collector runs
        if st is None or time.time() - st['computed_at'] > 300:
            try:
                st = self.recompute()
            except Exception:
                log.exception('get failed')
                if st is None:
                    raise
        return st
