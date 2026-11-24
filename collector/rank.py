#!/usr/bin/env python3
"""ai-rank collect: the outside numbers behind Beacon's AI Rank (http://beacon/rank), sent to the hub per day.

Runs on the home server (systemd timer ai-rank, hourly), where the numbers already are; it only reads, never writes a source:
  Roblox (Clean the Snow)  /var/lib/game-analytics/*.facts.json (game-analytics' daily Open Cloud reading):
                           daily active users, new users, net Robux per day; games.roblox.com for playing now,
                           visits and the last publish (public, no key)
  App Store                app-store-metrics' SQLite (Apple's daily sales report): first downloads, proceeds;
                           itunes.apple.com/lookup for each app's current version date (public)
  Vercel                   production deploys (`vercel api`, his CLI login there), one per project per day
  GitHub                   merged PRs by him (`gh search prs`)
  Invoiced Kč              Lighthouse's scoreboard (his one-tap log), through the hub's /lighthouse pass-through
  Emails sent              his iCloud Sent folder over IMAP (dates only; the vault's app password)
  Calls held               transcribe's CALLS.md (origin/main, fetched read-only): one row per recorded call
  Bank money in            budget's SQLite (his AirBank/Revolut CSV imports): inflows except transfers
  Reach                    rival-watch's public readings: his TikTok videos' views (yt-dlp, every 2 h) and
                           Clean the Snow's visits (games.roblox.com, every 30 min), as growth per day
Tasks done and agent sessions are the hub's own data: the hub adds them when it scores. The Team page's outcome
numbers (hub/outcomes.py) read the same days; keys only it uses never touch the rank.

A source that fails is sent as {"ok": false, "why": ...} and its days are left out, so the hub keeps what it had
(a failed read never zeroes a good day or demotes him). Money is in Kč: Robux at the DevEx rate ($0.0038 per earned
Robux, create.roblox.com/docs/production/monetization/developer-exchange, checked 2026-11-22), other currencies at
the Czech National Bank's rate of the day.

  ai-rank-collect            collect and POST to the hub (~/.config/agent-usage/config.json: hub + token)
  ai-rank-collect --dry      print the payload instead
"""
import datetime
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
import zoneinfo

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hub'))
import settings  # noqa: E402  (hub/settings.py: the deployment's names)

DAYS = 75                 # backfill window: 30 days shown + 30 before them + slack
RANK = settings.CONFIG['rank']  # the deployment's accounts: its game, its handles, what is not income
UNIVERSE = str(RANK['roblox_universe'])  # the Roblox game scored
FACTS = '/var/lib/game-analytics'
ASC_TOML = '/etc/app-store-metrics/config.toml'
ASC_DB = '/var/lib/app-store-metrics/metrics.db'
DEVEX_USD = 0.0038
PRAGUE = zoneinfo.ZoneInfo('Europe/Prague')  # the hub's days are Prague days
SCOREBOARD = settings.p('scoreboard')  # Lighthouse's one-tap log of outward acts
GH_USER = settings.OWNER['github']
CONFIG = os.path.expanduser('~/.config/agent-usage/config.json')
ICLOUD_USER = settings.OWNER['mail_user']          # his iCloud mail; the app password is in the vault
CALLS_REPO = settings.p('calls_repo')
BUDGET_DB = settings.p('budget_db')
WATCH = settings.p('watch')                        # rival-watch: public counts, light rate
NOT_EARNED = re.compile(RANK['not_earned'], re.I)   # bank inflows that are not money earned from someone
OWN_TIKTOK = tuple(RANK['tiktok'])                 # his own accounts; add one to targets.json there and here


def today():
    return datetime.datetime.now(PRAGUE).date()


def prague_day(iso):
    """An ISO timestamp (UTC 'Z' or with an offset) as its Prague day."""
    return datetime.datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(PRAGUE).date().isoformat()


def since_day():
    return today() - datetime.timedelta(days=DAYS)


def get_json(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': 'beacon-ai-rank', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def run(cmd, timeout=120):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1:] or ['exit %d' % p.returncode])
    return p.stdout


def rates():
    """Kč per unit of each currency: the Czech National Bank's daily table, and for currencies it doesn't list (TWD,
    AED, VND... Apple pays proceeds in those too) a public USD table converted through the CNB's dollar."""
    out, src = {'CZK': 1.0}, []
    try:
        url = ('https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/'
               'kurzy-devizoveho-trhu/denni_kurz.txt')
        req = urllib.request.Request(url, headers={'User-Agent': 'beacon-ai-rank'})
        text = urllib.request.urlopen(req, timeout=15).read().decode('utf-8')
        for line in text.splitlines()[2:]:
            parts = line.split('|')
            if len(parts) == 5:
                out[parts[3]] = float(parts[4].replace(',', '.')) / float(parts[2])
        src.append('cnb')
    except Exception:
        pass
    try:
        er = get_json('https://open.er-api.com/v6/latest/USD', timeout=15)
        if er.get('result') == 'success':
            usd = out.get('USD') or er['rates']['CZK']
            for cur, per_usd in er['rates'].items():
                if cur not in out and per_usd:
                    out[cur] = usd / per_usd
            src.append('er-api')
    except Exception:
        pass
    if 'USD' not in out:
        raise RuntimeError('no exchange rates')
    return out, '+'.join(src)


def add(days, day, key, value):
    if day < since_day().isoformat():
        return
    d = days.setdefault(day, {})
    d[key] = d.get(key, 0) + value


def roblox(days, kc):
    files = sorted(glob.glob(os.path.join(FACTS, '????-??-??.md.facts.json')))
    if not files:
        raise RuntimeError('no game-analytics facts files')
    seen, payer_days = {}, set()
    for f in files:  # oldest first: a newer reading of the same day wins
        try:
            facts = json.load(open(f)).get('facts') or {}
        except ValueError:
            continue
        window = sorted((facts.get('dau') or {}).keys())
        for key, out in (('dau', 'roblox_dau'), ('newUsers', 'roblox_new'), ('robuxNetByDay', 'robux')):
            vals = facts.get(key)
            if not isinstance(vals, dict):
                continue  # that query failed in this reading: leave its days alone
            # a successful query returns no row for a day with nothing in it: inside the window that is a real 0
            for day in window:
                seen.setdefault(day, {})[out] = float(vals.get(day) or 0)
        cvr = facts.get('payerCvrByDay')  # share of the day's players who paid; days without the query keep none
        if isinstance(cvr, dict) and isinstance(facts.get('dau'), dict):
            for day in window:
                rate, dau = float(cvr.get(day) or 0), float(facts['dau'].get(day) or 0)
                seen.setdefault(day, {})['roblox_payers'] = dau * rate  # rate is a fraction; the page rounds the sum
                payer_days.add(day)
    through = max(seen) if seen else None
    for day, vals in seen.items():
        for k, v in vals.items():
            add(days, day, k, v)
        if 'robux' in vals:
            add(days, day, 'robux_kc', vals['robux'] * DEVEX_USD * kc['USD'])
    g = get_json('https://games.roblox.com/v1/games?universeIds=' + UNIVERSE)['data'][0]
    now = {'playing': g.get('playing'), 'visits': g.get('visits'), 'favorites': g.get('favoritedCount'),
           'updated': g.get('updated'), 'name': 'Clean the Snow'}
    releases = []
    if g.get('updated'):
        releases.append({'key': 'roblox:' + g['updated'], 'day': prague_day(g['updated']), 'kind': 'roblox',
                         'what': 'Clean the Snow update on Roblox'})
    # buyers are read since 2026-11-22 (each reading covers 8 days): the Team page says from when they are counted
    since = {'roblox_payers': min(payer_days)} if payer_days else {}
    robux_ever = sum(v.get('robux', 0) for v in seen.values())  # every day any reading covered: the cash-out gate
    return {'ok': True, 'through': through, 'now': now, 'since': since, 'robux_ever': robux_ever}, releases


def app_store(days, kc):
    apps = {}
    try:
        import tomllib
        cfg = tomllib.load(open(ASC_TOML, 'rb'))
        apps = {str(a['id']): a.get('name', str(a['id'])) for a in cfg.get('apps', [])}
    except Exception:
        pass
    db = sqlite3.connect(f'file:{ASC_DB}?mode=ro', uri=True)
    rows = db.execute('SELECT report_date, product_type_identifier, units, developer_proceeds, currency_of_proceeds, '
                      'apple_identifier, parent_identifier FROM sales WHERE report_date >= ?',
                      (since_day().isoformat(),)).fetchall()
    unconverted = []
    for day, ptype, units, proceeds, cur, apple_id, parent in rows:
        units = float(units or 0)
        ptype = str(ptype or '').strip()
        if ptype in ('1', '1F', '1T', 'F1', '1E', '1EP', '1EU'):  # first downloads (not updates or redownloads)
            add(days, day, 'appstore_new', units)
        elif ptype.startswith('IA') or ptype.startswith('IAY'):  # in-app purchases and subscriptions (incl. renewals)
            add(days, day, 'appstore_paid', units)
        money = units * float(proceeds or 0)
        if money:
            rate = kc.get(str(cur or '').strip())
            if rate is None:  # never guess a rate: a currency nobody lists stays out of the sum, and says so
                unconverted.append(f'{money:g} {cur}')
                continue
            add(days, day, 'appstore_kc', money * rate)
    seen_through = None
    try:  # listing impressions: people the App Store showed the app to (Analytics Reports, a few days late)
        seen_through = db.execute("SELECT MAX(date) FROM app_store_discovery_and_engagement_standard").fetchone()[0]
        for day, n in db.execute("SELECT date, SUM(CAST(counts AS INTEGER)) FROM app_store_discovery_and_engagement_standard "
                                 "WHERE event = 'Impression' AND date >= ? GROUP BY date", (since_day().isoformat(),)):
            add(days, day, 'appstore_seen', float(n or 0))
    except sqlite3.Error:
        pass
    releases = []
    for app_id, name in apps.items():
        try:
            r = get_json('https://itunes.apple.com/lookup?id=' + app_id)['results'][0]
            when = r.get('currentVersionReleaseDate') or r.get('releaseDate')
            if when:
                releases.append({'key': f'appstore:{app_id}:{r.get("version")}', 'day': prague_day(when), 'kind': 'appstore',
                                 'what': f'{name} {r.get("version")} on the App Store'})
        except Exception:
            pass
    through = (today() - datetime.timedelta(days=2)).isoformat()  # Apple's daily report lags 1-2 days
    by_key = {'appstore_seen': seen_through} if seen_through and seen_through < through else {}
    return {'ok': True, 'apps': list(apps.values()), 'through': through, 'through_by': by_key,
            'unconverted': unconverted[:20]}, releases


def vercel(days):
    """Production deploys. A release is one site's first production deploy of a week: a site redeployed every day
    (a timer refreshing it) is one release a week, not seven."""
    since_ms = int(datetime.datetime.combine(since_day(), datetime.time(), PRAGUE).timestamp() * 1000)
    seen, until, weeks = set(), None, {}
    for _ in range(20):
        url = f'/v6/deployments?target=production&limit=100&since={since_ms}' + (f'&until={until}' if until else '')
        page = json.loads(run(['vercel', 'api', url], timeout=60))
        deps = page.get('deployments') or []
        for d in deps:
            if d.get('state', d.get('readyState')) != 'READY':
                continue
            date = datetime.datetime.fromtimestamp(d['created'] / 1000, PRAGUE).date()
            day, name = date.isoformat(), d.get('name')
            if (name, day) not in seen:
                seen.add((name, day))
                add(days, day, 'deploys', 1)
            monday = (date - datetime.timedelta(days=date.weekday())).isoformat()
            weeks[(name, monday)] = min(day, weeks.get((name, monday), day))
        nxt = (page.get('pagination') or {}).get('next')
        if not deps or not nxt:
            break
        until = nxt
    releases = [{'key': f'vercel:{name}:{monday}', 'day': day, 'kind': 'web', 'what': name}  # the page names it
                for (name, monday), day in weeks.items()]
    # every site that went live on each day of the last 5 weeks, for the Team page's "Shipped" (no rank effect)
    cut = (today() - datetime.timedelta(days=35)).isoformat()
    sites = sorted(({'name': n, 'day': d} for n, d in seen if d >= cut), key=lambda x: (x['day'], x['name']))
    return {'ok': True, 'sites': sites}, releases


def github(days):
    out = run(['gh', 'search', 'prs', '--author', GH_USER, '--merged', '--merged-at', '>=' + since_day().isoformat(),
               '--limit', '1000', '--json', 'closedAt'], timeout=120)
    for pr in json.loads(out):
        add(days, prague_day(pr['closedAt']), 'prs', 1)
    return {'ok': True}


def invoiced(days, hub):
    """Kč he logged as invoiced on Lighthouse's /today, each on its own day (undo lines cancel their entry)."""
    try:
        lines = [json.loads(x) for x in open(SCOREBOARD) if x.strip()]
    except FileNotFoundError:
        # Lighthouse creates the file on the first tap: none means nothing was ever logged, if its API agrees
        sb = get_json(hub.rstrip('/') + '/lighthouse/api/scoreboard', timeout=20)
        data = sb.get('data') or {}
        weeks = (data.get('history') or []) + [{'totals': data.get('totals') or {}}]
        if not sb.get('ok') or any((w.get('totals') or {}).get('invoiced') for w in weeks):
            raise RuntimeError(f'{SCOREBOARD} missing while Lighthouse shows invoices (did Lighthouse move?)')
        return {'ok': True}
    undone = {x.get('ref') for x in lines if x.get('kind') == 'undo'}
    for e in lines:
        if e.get('kind') == 'invoiced' and e.get('amount') and e.get('id') not in undone:
            add(days, prague_day(e['at']), 'invoiced_kc', float(e['amount']))
    return {'ok': True}


def mail(days):
    """Emails he sent from iCloud: the Sent folder's own dates, read over IMAP (headers only, read-only). The corpus
    copy missed sent mail (its Sent folder stopped on 6 Nov while iCloud had more), so the folder is the source."""
    import imaplib
    pw = run(['secrets', 'get', 'ICLOUD_MAIL_APP_PASSWORD'], timeout=30).strip()
    m = imaplib.IMAP4_SSL('imap.mail.me.com', timeout=60)
    try:
        m.login(ICLOUD_USER, pw)
        typ, _ = m.select('"Sent Messages"', readonly=True)
        if typ != 'OK':
            raise RuntimeError('no Sent Messages folder')
        since = (since_day() - datetime.timedelta(days=1)).strftime('%d-%b-%Y')
        typ, data = m.search(None, 'SINCE', since)
        ids = data[0].split() if typ == 'OK' and data and data[0] else []
        if ids:
            typ, rows = m.fetch(b','.join(ids).decode(), '(INTERNALDATE)')
            for row in rows or []:
                raw = row.decode() if isinstance(row, bytes) else ''
                mm = re.search(r'INTERNALDATE "([^"]+)"', raw)
                if mm:
                    when = datetime.datetime.strptime(mm.group(1), '%d-%b-%Y %H:%M:%S %z')
                    add(days, when.astimezone(PRAGUE).date().isoformat(), 'emails_sent', 1)
    finally:
        try:
            m.logout()
        except Exception:
            pass
    return {'ok': True, 'through': today().isoformat()}


def calls(days):
    """Recorded calls, one row each in transcribe's CALLS.md, read from origin/main (fetched, never checked out)."""
    try:
        run(['git', '-C', CALLS_REPO, 'fetch', '-q', 'origin', 'main'], timeout=60)
    except Exception:
        pass  # offline: the last fetched copy is still the best list we have
    text = run(['git', '-C', CALLS_REPO, 'show', 'origin/main:CALLS.md'])
    recent = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 5 or not re.match(r'^\d{4}-\d{2}-\d{2}$', cells[0]):
            continue
        add(days, cells[0], 'calls', 1)
        if cells[0] >= (today() - datetime.timedelta(days=31)).isoformat():
            recent.append({'day': cells[0], 'what': cells[4][:90]})
    when = run(['git', '-C', CALLS_REPO, 'log', '-1', '--format=%cI', 'origin/main']).strip()
    # known up to the day the list last changed: a call not transcribed yet is not a 0
    through = prague_day(when) if when else today().isoformat()
    return {'ok': True, 'through': max(through, max((r['day'] for r in recent), default=through)), 'recent': recent[:12],
            'updated': when}


def bank(days):
    """Money other people paid him: the budget's Income rows minus his own money (card top-ups, his own and his
    family's transfers, the state, bank interest) and minus Apple and Roblox payouts (counted at the source, so
    not twice). The statements are CSV files he drops in, so the data stops where his last import did."""
    db = sqlite3.connect(f'file:{BUDGET_DB}?mode=ro', uri=True)
    through = db.execute('SELECT MAX(booked_on) FROM transactions').fetchone()[0]
    for day, who, n in db.execute("SELECT booked_on, merchant, COALESCE(amount_base, amount) FROM transactions "
                                  "WHERE COALESCE(amount_base, amount) > 0 AND category = 'Income' AND booked_on >= ?",
                                  (since_day().isoformat(),)):
        if not NOT_EARNED.search(who or ''):
            add(days, day, 'bank_in', float(n or 0))
    return {'ok': True, 'through': through}


def jsonl(path):
    with open(path) as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def prague_ts_day(t):
    return datetime.datetime.fromtimestamp(t, PRAGUE).date().isoformat()


def reach(days):
    """Views his own TikTok videos gained each day, and Clean the Snow's visits each day, from rival-watch's
    public snapshots. A video's views at its first sighting count on the day it was posted; every later rise
    counts on the day it was seen. Roblox visits count from the second reading on (the first is the baseline)."""
    vids, newest = {}, 0
    for r in jsonl(os.path.join(WATCH, 'tiktok.jsonl')):
        if r.get('acct') in OWN_TIKTOK and isinstance(r.get('views'), (int, float)):
            vids.setdefault(r['id'], []).append(r)
            newest = max(newest, r['t'])
    for snaps in vids.values():
        snaps.sort(key=lambda r: r['t'])
        last = 0
        for i, r in enumerate(snaps):
            gain = r['views'] - last
            if gain > 0:
                day = prague_ts_day(r.get('posted') or r['t']) if i == 0 else prague_ts_day(r['t'])
                add(days, day, 'tiktok_views', gain)
            last = max(last, r['views'])
    prev, first, last_t = None, None, 0
    for r in jsonl(os.path.join(WATCH, 'games.jsonl')):
        if str(r.get('id')) != UNIVERSE or not isinstance(r.get('visits'), (int, float)):
            continue
        first, last_t = first or r['t'], r['t']
        if prev is not None and r['visits'] > prev:
            add(days, prague_ts_day(r['t']), 'roblox_visits', r['visits'] - prev)
        prev = r['visits'] if prev is None else max(prev, r['visits'])
    if prev is None and not newest:
        raise RuntimeError('rival-watch has no readings')
    down = {}  # one part stale does not take the other down
    if not newest or time.time() - newest > 12 * 3600:
        down['tiktok_views'] = 'TikTok last read ' + (prague_ts_day(newest) if newest else 'never')
    if prev is None or time.time() - last_t > 3 * 3600:
        down['roblox_visits'] = 'Roblox visits last read ' + (prague_ts_day(last_t) if prev is not None else 'never')
    return {'ok': True, 'down': down, 'through': today().isoformat(), 'tiktok': list(OWN_TIKTOK), 'videos': len(vids),
            'read_at': newest,
            # visits before rival-watch's first reading are not in the days: counting starts the day after it
            'since': {'roblox_visits': prague_ts_day(first + 86400) if first else today().isoformat()}}


def collect(hub):
    kc, kc_from = rates()  # no rates at all: the run fails (systemd says so) rather than guess
    days, releases, sources = {}, [], {}

    def step(name, fn, *args):
        part = {}
        try:
            res = fn(part, *args)
            status, rel = res if isinstance(res, tuple) else (res, [])
            sources[name] = status
            releases.extend(rel)
            for day, vals in part.items():  # only a source that worked touches its days
                for k, v in vals.items():
                    days.setdefault(day, {})[k] = days.get(day, {}).get(k, 0) + v
        except Exception as e:
            sources[name] = {'ok': False, 'why': str(e)[:200]}

    step('roblox', roblox, kc)
    step('appstore', app_store, kc)
    step('vercel', vercel)
    step('github', github)
    step('invoiced', invoiced, hub)
    step('mail', mail)
    step('calls', calls)
    step('bank', bank)
    step('reach', reach)
    # keys a working source owns are sent for every day of the window (0 where nothing happened), so the hub
    # can tell "nothing" from "not read"
    owned = {'appstore': ('appstore_new', 'appstore_kc', 'appstore_paid'), 'vercel': ('deploys',), 'github': ('prs',),
             'invoiced': ('invoiced_kc',), 'mail': ('emails_sent',), 'calls': ('calls',), 'bank': ('bank_in',)}
    d0 = since_day()
    for name, keys in owned.items():
        if not sources.get(name, {}).get('ok'):
            continue
        end = datetime.date.fromisoformat(sources[name].get('through') or today().isoformat())
        for i in range((end - d0).days + 1):
            day = (d0 + datetime.timedelta(days=i)).isoformat()
            for k in keys:
                days.setdefault(day, {}).setdefault(k, 0)
    return {'at': time.time(), 'rates': {'USD': kc['USD'], 'from': kc_from, 'devex_usd': DEVEX_USD},
            'sources': sources, 'days': days, 'releases': releases}


def main():
    cfg = json.load(open(CONFIG))
    hub = cfg['hub']
    payload = collect(hub)
    if '--dry' in sys.argv:
        print(json.dumps(payload, indent=1, sort_keys=True))
        return
    req = urllib.request.Request(hub.rstrip('/') + '/api/rank/ingest', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + cfg['token']})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read())
    bad = [n for n, s in payload['sources'].items() if not s.get('ok')]
    print(f"ai-rank: {res.get('tier_label', '?')}, sources down: {', '.join(bad) or 'none'}")
    if len(bad) == len(payload['sources']):
        sys.exit(1)  # every source failed: that is a broken run, let systemd say so


if __name__ == '__main__':
    main()
