"""Outcomes: the results strip on top of http://beacon/team. Only what reached people, never the work behind it
(2026-11-22: "outcome only, the path to it does not matter"; no headline number, all equal weight).

Seven numbers for today, this week (since Monday) and this month (since the 1st), local days (the settings' timezone):
  reach     TikTok views of his own videos, App Store listing impressions, the game's visits
  users     new people: Roblox first-time players, App Store first downloads
  paying    App Store purchases and subscriptions; Roblox buyers (payer rate x daily players, when read)
  money     Kč in: App Store proceeds, bank income, and his half of the Robux at the cash-out rate once the game has
            earned the 30,000 Robux Roblox pays out from (hub/rank.py's rule); against the monthly burn line
  shipped   what went live: App Store versions, Roblox updates, sites deployed to production
  emails    sent from his mailbox
  calls     recorded calls (transcribe)

Nothing is logged by hand: every number comes from collector/rank.py on the home server (hourly), which the AI Rank already
runs, through the rank tables (rank_days, rank_releases, rank_meta). Each part carries its source, a link, and how
old it is; a source that failed shows as not read and stays out of the sum, never as a 0. A source that runs late
(Apple, Roblox: 1-2 days) says the last day it covers.

GET /api/outcomes: {"at", "collected_at", "stale", "periods": {"today"|"week"|"month": {"from", "to", "cards": [...]}},
                    "burn": {"low", "high"}, "sources": {...}}
"""
import datetime
import json
import sqlite3
import threading
import time
import zoneinfo

from settings import CONFIG, PROJECT_NAMES, TIMEZONE
from rank import DEVEX_MIN, ROBUX_SHARE  # money is his only once it is: his half, and only once DevEx pays out

OUT = CONFIG['outcomes']
BURN = tuple(OUT['burn'])  # Kč a month the business costs: the line the money card is measured against
LINKS = OUT['links']
TZ = zoneinfo.ZoneInfo(TIMEZONE)  # the server runs on UTC; the owner reads local time
STALE_SEC = 3 * 3600   # the hourly collector missing three runs: every number on the strip says so

SOURCES = {  # name in rank_meta -> label, link (the links are the deployment's own accounts: settings `outcomes.links`)
    name: (label, LINKS.get(name, '')) for name, label in (
        ('appstore', 'App Store'), ('roblox', 'Roblox'), ('reach', 'TikTok + Roblox visits'), ('vercel', 'Vercel'),
        ('bank', 'Bank statements'), ('mail', 'Mail'), ('calls', 'Call recordings'))
}

# (card, part label, day key, source, unit); a part's value is the sum of its key over the period's days
PARTS = [
    ('reach', 'TikTok views', 'tiktok_views', 'reach', 'count'),
    ('reach', 'Roblox visits', 'roblox_visits', 'reach', 'count'),
    ('reach', 'App Store views', 'appstore_seen', 'appstore', 'count'),
    ('users', 'Roblox new players', 'roblox_new', 'roblox', 'count'),
    ('users', 'App downloads', 'appstore_new', 'appstore', 'count'),
    ('paying', 'Roblox buyers', 'roblox_payers', 'roblox', 'count'),
    ('paying', 'App purchases', 'appstore_paid', 'appstore', 'count'),
    ('money', 'Robux, his half', 'robux_kc', 'roblox', 'kc'),
    ('money', 'App Store', 'appstore_kc', 'appstore', 'kc'),
    ('money', 'Bank', 'bank_in', 'bank', 'kc'),
    ('emails', 'Sent', 'emails_sent', 'mail', 'count'),
    ('calls', 'Recorded', 'calls', 'calls', 'count'),
]
SHORT = {'TikTok views': 'TikTok', 'Roblox visits': 'Roblox', 'App Store views': 'App Store', 'Roblox new players': 'Roblox',
         'App downloads': 'App Store', 'Roblox buyers': 'Roblox', 'App purchases': 'App Store', 'Robux, his half': 'Robux'}  # on the card, the card says what
CARDS = [  # key, label, emoji, unit
    ('reach', 'Reach', '👀', 'count'),
    ('users', 'New users', '🙋', 'count'),
    ('paying', 'Paying', '💳', 'count'),
    ('money', 'Kč in', '💰', 'kc'),
    ('shipped', 'Shipped', '🚀', 'count'),
    ('emails', 'Emails sent', '✉️', 'count'),
    ('calls', 'Calls', '📞', 'count'),
]
SITE_NAMES = CONFIG['site_names']  # Vercel project -> what he calls the site


def site_name(project):
    return SITE_NAMES.get(project) or PROJECT_NAMES.get(project) or str(project).replace('-', ' ').capitalize()


def periods(today):
    d = datetime.date.fromisoformat(today)
    return {'today': (d, d), 'week': (d - datetime.timedelta(days=d.weekday()), d), 'month': (d.replace(day=1), d)}


def day_list(start, end):
    return [(start + datetime.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def age_note(name, src, start, end, now, through=None):
    """How old the part is, in words: the day it covers when it runs behind, else when it was read."""
    if not src:
        return 'never read'
    if not src.get('ok'):
        good = src.get('good_at')
        return 'not read' + (f' since {datetime.datetime.fromtimestamp(good, TZ).strftime("%-d %b %H:%M")}' if good else '')
    through = through or src.get('through')
    if isinstance(through, str) and through < end.isoformat():
        day = datetime.date.fromisoformat(through)
        late = (end - day).days
        return f'through {day.strftime("%-d %b")}' + (f', {late} days old' if late > 7 else '')
    at = src.get('read_at') or src.get('checked')
    if at:
        mins = max(0, int((now - at) / 60))
        return 'read just now' if mins < 2 else f'read {mins} min ago' if mins < 90 else f'read {mins // 60} h ago'
    return ''


def build(series, releases, sources, today, now, collected_at, robux_ever=None):
    """robux_ever: Robux the game has earned in all (the cash-out gate); None = count every day in `series`."""
    if isinstance((sources.get('roblox') or {}).get('robux_ever'), (int, float)):
        robux_ever = sources['roblox']['robux_ever']  # every day the game reported, not just the days kept here
    if robux_ever is None:
        robux_ever = sum((v or {}).get('robux', 0) for v in series.values())
    cashable = robux_ever >= DEVEX_MIN
    out = {}
    for pkey, (start, end) in periods(today).items():
        days = day_list(start, end)
        cards = {k: {'key': k, 'label': lab, 'icon': ic, 'unit': unit, 'value': 0, 'parts': [], 'down': 0}
                 for k, lab, ic, unit in CARDS}
        for card, label, key, src_name, unit in PARTS:
            src = sources.get(src_name) or {}
            through = (src.get('through_by') or {}).get(key) or src.get('through')
            age = age_note(src_name, src, start, end, now, through)
            down = (src.get('down') or {}).get(key)
            state = 'ok'
            if not src.get('ok') or down:
                val, state = None, 'down'
                if down:
                    age = down
            elif key == 'roblox_payers' and not any(key in v for v in series.values()):
                val, age, state = None, 'not read yet', 'late'  # the payer rate never came in: missing, not zero buyers
            elif isinstance(through, str) and through < start.isoformat():
                val, state = None, 'late'  # the source's data stops before this period starts: unknown, not 0
                late = (end - datetime.date.fromisoformat(through)).days
                if late <= 7:
                    age = f'{SOURCES[src_name][0]} reports {late} days late'
                else:
                    state = 'down'  # weeks old: something stopped (or waits for his statements), say it loudly
            else:
                val = sum((series.get(d) or {}).get(key, 0) for d in days)
                val = round(val * ROBUX_SHARE if key == 'robux_kc' else val)
                since = (src.get('since') or {}).get(key)
                if isinstance(since, str) and since > start.isoformat():  # counting began inside the period
                    age = f'counted since {datetime.date.fromisoformat(since).strftime("%-d %b")}'
            part = {'label': label, 'short': SHORT.get(label, label),
                    'value': val, 'unit': unit, 'source': SOURCES[src_name][0],
                    'link': SOURCES[src_name][1], 'ok': val is not None, 'state': state, 'age': age}
            if key == 'robux_kc' and part['ok'] and not cashable:
                # shown, but not money yet: Roblox pays out only from 30,000 earned Robux (DevEx)
                part['counted'] = False
                part['age'] = f'not cashable yet: {round(robux_ever):,} of {DEVEX_MIN:,} R$ earned'
            c = cards[card]
            c['parts'].append(part)
            if part['ok'] and part.get('counted', True):
                c['value'] += part['value']
            elif part['state'] == 'down':
                c['down'] += 1
        # shipped: App Store versions and Roblox updates from the releases, sites from Vercel's per-day list
        sh = cards['shipped']
        lo, hi = start.isoformat(), end.isoformat()
        items = {}
        for r in releases:
            if lo <= r['day'] <= hi and r.get('kind') in ('appstore', 'roblox'):
                items[r['key']] = {'what': r['what'], 'day': r['day'], 'kind': r['kind']}
        v = sources.get('vercel') or {}
        if v.get('ok') and isinstance(v.get('sites'), list):
            per = {}
            for s in v['sites']:
                if lo <= str(s.get('day')) <= hi:
                    per.setdefault(s.get('name'), []).append(s['day'])
            for name, ds in per.items():
                items['web:' + str(name)] = {'what': site_name(name) + (f' ×{len(ds)}' if len(ds) > 1 else ''),
                                             'day': max(ds), 'kind': 'web'}
        sh['items'] = sorted(items.values(), key=lambda x: x['day'], reverse=True)
        sh['value'] = len(items)
        for name, label in (('appstore', 'App Store'), ('roblox', 'Roblox'), ('vercel', 'Sites')):
            src = sources.get(name) or {}
            n = sum(1 for x in items.values() if x['kind'] == {'vercel': 'web'}.get(name, name))
            sh['parts'].append({'label': label, 'value': n if src.get('ok') else None, 'unit': 'count',
                                'source': SOURCES[name][0], 'link': SOURCES[name][1], 'ok': bool(src.get('ok')),
                                'state': 'ok' if src.get('ok') else 'down',
                                'age': age_note(name, {k: v for k, v in src.items() if k != 'through'}, end, end, now)})
            if not src.get('ok'):
                sh['down'] += 1
        calls = sources.get('calls') or {}
        cards['calls']['items'] = [{'what': c['what'], 'day': c['day']} for c in calls.get('recent') or []
                                   if lo <= c['day'] <= hi] if calls.get('ok') else []
        # the burn line for this period: the month's line, or its share of the days so far
        share = len(days) / 30 if pkey != 'month' else 1
        cards['money']['burn'] = {'low': round(BURN[0] * share), 'high': round(BURN[1] * share)}
        for c in cards.values():
            c['known'] = any(p['ok'] for p in c['parts'])
            if not c['known']:
                c['value'] = None
        out[pkey] = {'from': lo, 'to': hi, 'cards': [cards[k] for k, *_ in CARDS]}
    stale = collected_at is None or now - collected_at > STALE_SEC
    return {'at': now, 'collected_at': collected_at, 'stale': stale, 'periods': out,
            'burn': {'low': BURN[0], 'high': BURN[1]}}


class Outcomes:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.cache = (0, None)

    def read(self):
        today = self.store.today()
        since = (datetime.date.fromisoformat(today) - datetime.timedelta(days=40)).isoformat()
        series, releases, meta, robux_ever = {}, [], {}, 0
        with self.store.connect() as con:
            try:
                robux_ever = con.execute("SELECT COALESCE(SUM(value), 0) FROM rank_days WHERE key = 'robux'").fetchone()[0]
                for day, key, value in con.execute('SELECT day, key, value FROM rank_days WHERE day >= ?', (since,)):
                    series.setdefault(day, {})[key] = value
                releases = [dict(zip(('key', 'day', 'kind', 'what'), r)) for r in con.execute(
                    'SELECT key, day, kind, what FROM rank_releases WHERE day >= ?', (since,))]
                meta = {k: json.loads(v) for k, v in con.execute('SELECT k, v FROM rank_meta')}
            except sqlite3.OperationalError:  # no rank tables yet (a fresh hub): every source reads as never read
                pass
        return build(series, releases, meta.get('sources') or {}, today, time.time(), meta.get('at'), robux_ever)

    def view(self):
        with self.lock:
            at, data = self.cache
            if data is None or time.time() - at > 60:
                data = self.read()
                self.cache = (time.time(), data)
            return data
