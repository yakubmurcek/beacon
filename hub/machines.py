"""Every machine, its health and its background jobs, and the alarms when one breaks.

The hub is the one core for this (2026-11-22, his ask: "everything running on its own must be
visible, or tell me on Telegram when it breaks"). Two faces read it: the machines page at
http://beacon/machines and Lighthouse's wall TV. Neither scrapes machines itself.

Where the data comes from:
  - each collector's live push carries `host` (collector/host.py): stats, versions, jobs;
  - `tailscale status --json` on this box (the hub's) for every tailnet device, collector or not;
  - node_exporter on the Pis (no collector there), the scrape Lighthouse used to do itself.

GET /api/machines (the contract both faces build on):
  {"now": epoch, "machines": [{
      "machine": "devbox", "role": "home server: dev, previews, agents", "always_on": true,
      "state": "ok" | "warn" | "bad" | "asleep" | "offline",
      "online": bool, "seen_at": epoch | null,        # last collector push (null: no collector)
      "problems": ["khazix-bench-preview keeps failing", ...],
      "stats": {"cpus", "mem_total", "mem_avail", "load", "uptime_s", "cpu_pct", "temp_c",
                "disks": [{"mount", "size", "free"}]} | null,
      "versions": {"os", "python", "collector", "claude", "codex", "tailscale"} | {},
      "tailscale": {"online", "last_seen", "os", "ip", "relay", "direct", "key_expiry"} | null,
      "jobs_at": epoch | null,
      "jobs": [{"id", "name", "kind": "timer"|"daemon"|"service"|"launchd"|"schtask"|"cron", "desc",
                "state" (what the machine said), "status": "ok"|"failed"|"overdue"|"gone"|
                "running"|"never"|"unknown", "last_run", "next_run", "code", "alarm",
                "alarm_failed", "why", "bad_since"}]}]}
  Machines sort bad first. Jobs sort failed/overdue/gone first, then by name.

Alarms (only where HUB_ALARMS=1, i.e. the live hub), through telegram-alarm:
  - one of HIS jobs (own: not a package's or an app's) failing for 20 min, whatever its own
    OnFailure alarm did (a lost or skipped unit-alarm, like the DNS-at-boot loss of 2026-11-21,
    still reaches him), overdue, hung (a timer's run going 6 h), or gone;
  - an always-on machine that stops reporting for 10 min (tailscale-only ones: offline 30 min);
  - each once per 24 h while it stays bad, a quiet 🟢 line after a good run (not a scan that caught it running);
  - the first run sends one summary of everything already broken instead of a line each.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from datetime import datetime

from settings import HUB, MACHINES
from apps import NET_ERRORS
import freshness  # noqa: E402

log = logging.getLogger('beacon.machines')

# What each box is for, and whether it should always answer (settings `machines`). A box not listed is shown, never alarmed.
HUB_MACHINE = HUB.get('machine')
ROLES = {name: (m.get('role'), bool(m.get('always_on'))) for name, m in MACHINES.items()}
NODE_EXPORTER = {name: m['node_exporter'] for name, m in MACHINES.items() if m.get('node_exporter')}

SILENT_SEC = 600          # an always-on collector quiet this long is down
TS_OFFLINE_SEC = 1800     # a tailscale-only always-on box offline this long is down
FAIL_GRACE_SEC = 1200     # a job failed this long is not a blip (unit-alarm's own grace is 20 min too)
OVERDUE_SEC = 900         # a scheduled run this late (as of the machine's own scan) did not happen
HUNG_SEC = 6 * 3600       # a timer's run going this long is hung
STALE_SCAN_SEC = 1800     # an online machine whose job scan has not changed this long: the scan is broken
GONE_KEEP_SEC = 7 * 86400  # a job that vanished is shown (and alarmed once) for a week
EVERY_SEC = 86400         # one Telegram line per problem per day
JOB_ORDER = {'failed': 0, 'overdue': 1, 'gone': 2, 'running': 3, 'ok': 4, 'never': 5, 'unknown': 6}
STATE_ORDER = {'bad': 0, 'warn': 1, 'ok': 2, 'asleep': 3, 'offline': 4}
BEHIND_SEC = 4 * 3600     # a box missing a commit this old (updates run hourly): its updater is stuck
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MISSING = {}             # collector hash -> (checked at, time of the oldest hub commit it lacks, or 0)


def missing_since(collector, now):
    """When the oldest commit a collector ('<hash> <day>') lacks landed in the hub's checkout, or None if it lacks
    none (or its hash is unknown here). Asked of git at most every 10 minutes per hash."""
    h = (collector or '').split(' ')[0]
    if not re.fullmatch(r'[0-9a-f]{7,40}', h):
        return None
    at, since = _MISSING.get(h, (0, 0))
    if now - at > 600:
        try:
            out = subprocess.run(['git', '-C', _HERE, 'log', '--format=%ct', f'{h}..HEAD'], capture_output=True,
                                 text=True, timeout=5)
            stamps = [int(x) for x in out.stdout.split()] if out.returncode == 0 else []
            since = min(stamps) if stamps else 0
        except (OSError, subprocess.SubprocessError, ValueError):
            since = 0
        _MISSING[h] = (now, since)
    return since or None


SCHEMA = """
CREATE TABLE IF NOT EXISTS hosts (machine TEXT PRIMARY KEY, seen_at REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs_seen (
  machine TEXT NOT NULL, job_id TEXT NOT NULL, name TEXT, kind TEXT,
  first_seen REAL, last_seen REAL, bad_since REAL, own INTEGER, PRIMARY KEY (machine, job_id));
CREATE TABLE IF NOT EXISTS alarms (key TEXT PRIMARY KEY, text TEXT, first_bad REAL, sent_at REAL);
"""


def epoch(iso):
    try:
        return datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError):
        return None


class Machines:
    def __init__(self, store, alarms=None):
        self.store = store
        self.alarms = os.environ.get('HUB_ALARMS') == '1' if alarms is None else alarms
        self._ts = (0, {})
        self._node = {}
        self._lock = threading.Lock()
        self.sources = freshness.Sources()
        # The alarm path watching itself, read by the home server's hub-watch through /api/live: a hub that
        # can't send (no token, as on 2026-11-06..08 when ~1,181 alarms were lost) must not be silent.
        self.health = {'alarms': self.alarms, 'tick_at': None, 'sent_at': None, 'send_failing_since': None}
        with store.connect() as con:
            con.executescript(SCHEMA)
            if 'own' not in [r[1] for r in con.execute('PRAGMA table_info(jobs_seen)')]:
                con.execute('ALTER TABLE jobs_seen ADD COLUMN own INTEGER')

    # ------------------------------------------------------------ ingest

    def save(self, machine, host):
        """A collector's `host` block: keep the newest, and remember every job it ever listed."""
        if not isinstance(host, dict):
            return
        try:
            self._save(machine, host)
        except Exception as e:  # a bad host block must never fail the push (the collector would resend forever)
            log.warning('%s: host block not stored: %s: %s', machine, type(e).__name__, e)

    def _save(self, machine, host):
        now = time.time()
        jobs = [j for j in host.get('jobs') or [] if isinstance(j, dict) and j.get('id')]
        with self.store.connect() as con:
            old = con.execute('SELECT data FROM hosts WHERE machine = ?', (machine,)).fetchone()
            con.execute('INSERT OR REPLACE INTO hosts(machine, seen_at, data) VALUES (?, ?, ?)',
                        (machine, now, json.dumps(host)))
            if old and json.loads(old['data']).get('jobs_at') == host.get('jobs_at'):
                return  # the same 2-minute job scan again: nothing new to remember
            for j in jobs:
                # bad_since: when it started failing, kept while a later run is still going (a job
                # that fails every run must not read as fixed whenever a scan catches it running).
                st = j.get('state')
                con.execute(
                    'INSERT INTO jobs_seen(machine, job_id, name, kind, first_seen, last_seen, bad_since, own) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(machine, job_id) DO UPDATE SET '
                    'name=excluded.name, kind=excluded.kind, last_seen=excluded.last_seen, own=excluded.own, '
                    "bad_since=CASE WHEN ? = 'failed' THEN COALESCE(jobs_seen.bad_since, excluded.last_seen) "
                    "WHEN ? = 'running' THEN jobs_seen.bad_since ELSE NULL END",
                    (machine, j['id'], j.get('name'), j.get('kind'), now, now, now if st == 'failed' else None,
                     int(j.get('own', True)), st, st))

    # ------------------------------------------------------------ sources

    def tailscale(self):
        """{name: device} from this box's tailscale, cached a minute. Empty where there is no tailscale."""
        at, devs = self._ts
        if time.time() - at < 60:
            return devs
        devs = {}
        exe = shutil.which('tailscale')
        if exe:
            try:
                d = json.loads(subprocess.run([exe, 'status', '--json'], capture_output=True, text=True,
                                              timeout=10).stdout or '{}')
            except (OSError, ValueError, subprocess.SubprocessError):
                d = {}
            peers = list((d.get('Peer') or {}).values()) + ([d['Self']] if d.get('Self') else [])
            for p in peers:
                name = (p.get('DNSName') or '').split('.')[0]
                if not name:
                    continue  # Funnel ingress nodes and the like
                devs[name] = {'online': bool(p.get('Online')) or p is d.get('Self'),
                              'last_seen': epoch(p.get('LastSeen')), 'os': p.get('OS') or None,
                              'ip': (p.get('TailscaleIPs') or [None])[0], 'relay': p.get('Relay') or None,
                              'direct': bool(p.get('CurAddr')), 'key_expiry': epoch(p.get('KeyExpiry'))}
        self._ts = (time.time(), devs)
        return devs

    def node_exporter(self, name, url):
        """Pi stats from node_exporter, cached 60 s: the same numbers a collector sends."""
        at, val = self._node.get(name, (0, None))
        if time.time() - at < 60:
            return val
        val = None
        try:
            with urllib.request.urlopen(url, timeout=2.5) as r:
                val = parse_node(r.read().decode('utf-8', 'replace'))
        except NET_ERRORS:
            pass  # the Pi is off or its exporter down: no stats, the collector's numbers still show
        self._node[name] = (time.time(), val)
        return val

    # ------------------------------------------------------------ the view

    def view(self, now=None):
        now = now or time.time()
        with self.store.connect() as con:
            hosts = {r['machine']: r for r in con.execute('SELECT * FROM hosts')}
            seen = {}
            for r in con.execute('SELECT * FROM jobs_seen WHERE last_seen > ?', (now - GONE_KEEP_SEC,)):
                seen.setdefault(r['machine'], {})[r['job_id']] = r
            pushes = {r['machine']: r['seen_at'] for r in con.execute('SELECT machine, seen_at FROM live')}
            deaf = freshness.deaf_hooks(con, now)
        ts = self.tailscale()
        names = set(hosts) | set(ts) | {n for n, (_, always) in ROLES.items() if always}
        out = [self.machine(n, hosts.get(n), seen.get(n, {}), pushes.get(n), ts.get(n), now) for n in names]
        out = [m for m in out if m['seen_at'] or m['always_on'] or m['tailscale'] and (m['tailscale']['online'] or
               (m['tailscale']['last_seen'] or 0) > now - 60 * 86400)]
        lag = freshness.version_lag(out)
        blind = sorted(k for k, since in self.sources.failing.items() if now - since > 3600)
        if blind:  # the watcher's own blind spot: a source it can't read checks nothing
            lag.setdefault(HUB_MACHINE, []).append({'key': f'drift:{HUB_MACHINE}:sources', 'since': None, 'name': 'GitHub login',
                                                    'text': f"can't read the source of {', '.join(blind)} to spot outdated copies"})
        for m in out:  # outdated copies the per-machine view can't see alone: tool versions, dead hooks
            if not m['online']:
                continue
            extra = lag.get(m['machine'], []) + ([deaf[m['machine']]] if m['machine'] in deaf else [])
            m['drift'] += extra
            m['problems'] += [d['text'] for d in extra]
            if extra and m['state'] == 'ok':
                m['state'] = 'warn'
        out.sort(key=lambda m: (STATE_ORDER[m['state']], not m['always_on'], m['machine']))
        return {'now': now, 'machines': out}

    def machine(self, name, host, seen, pushed, ts, now):
        role, always = ROLES.get(name, (None, False))
        data = json.loads(host['data']) if host else {}
        seen_at = max(pushed or 0, host['seen_at'] if host else 0) or None
        online = bool(seen_at and now - seen_at <= SILENT_SEC) if host or pushed else bool(ts and ts['online'])
        stats = data.get('stats') if online and host else None
        if stats is None and name in NODE_EXPORTER and ts and ts['online']:
            stats = self.node_exporter(name, NODE_EXPORTER[name])
        jobs = [self.job(j, seen.get(j['id']), data.get('jobs_at'), always) for j in data.get('jobs') or []
                if isinstance(j, dict) and j.get('id')]
        listed = {j['id'] for j in jobs}
        newest = max((r['last_seen'] for r in seen.values()), default=0)  # hub time of the last scan: no clock skew
        for jid, r in seen.items():  # listed before, missing now: disabled, deleted or broken
            if (jid not in listed and r['last_seen'] < newest - 60 and r['kind'] in ('timer', 'daemon', 'launchd', 'schtask')
                    and r['own'] != 0):  # an app's updater replaced on update, a package timer renamed: not news
                jobs.append({'id': jid, 'name': r['name'], 'kind': r['kind'], 'desc': None, 'state': None,
                             'status': 'gone', 'last_run': None, 'next_run': None, 'code': None,
                             'alarm': False, 'alarm_failed': False, 'own': True, 'bad_since': r['last_seen'],
                             'why': 'no longer running on this machine' if r['kind'] == 'daemon'
                             else 'no longer scheduled on this machine'})
        jobs.sort(key=lambda j: (JOB_ORDER.get(j['status'], 9), j['name'] or ''))
        mine = [j for j in jobs if j.get('own', True)]  # a package's broken timer is listed, not his problem
        scanned = max((r['last_seen'] for r in seen.values()), default=None)  # hub time of the last job scan
        problems = [problem_text(j) for j in mine if j['status'] in ('failed', 'overdue', 'gone')]
        if stats:
            for d in stats.get('disks') or []:
                if d.get('size') and d['free'] / d['size'] < 0.1:
                    problems.append(f"disk {d['mount']} {round(100 - 100 * d['free'] / d['size'])}% full")
        lacks = missing_since((data.get('versions') or {}).get('collector'), now) if online and host else None
        drift = []
        if lacks and now - lacks > BEHIND_SEC:
            drift.append({'key': f'drift:{name}:collector', 'since': lacks, 'name': 'collector',
                          'text': f'its collector stopped updating {round((now - lacks) / 3600)} h ago'})
        if online and host:
            try:
                drift += freshness.check(name, data.get('fresh'), self.sources, now)
            except Exception as e:  # noqa: BLE001 - a bad report never breaks the view
                log.warning('%s: freshness: %s: %s', name, type(e).__name__, e)
        problems += [d['text'] for d in drift]
        if online and host and scanned and now - scanned > STALE_SCAN_SEC:
            problems.insert(0, f'its job list stopped updating {round((now - scanned) / 60)} min ago')
        if online:
            state = 'bad' if any(j['status'] == 'failed' for j in mine) else 'warn' if problems else 'ok'
        elif always:
            state = 'bad'
            problems.insert(0, 'offline' if not (host or pushed) else 'stopped reporting')
        else:
            state = 'asleep' if (ts and ts['online']) or (seen_at and now - seen_at < 3 * 86400) else 'offline'
        return {'machine': name, 'role': role, 'always_on': always, 'state': state, 'online': online,
                'seen_at': seen_at, 'problems': problems, 'drift': drift, 'stats': stats, 'versions': data.get('versions') or {},
                'tailscale': ts, 'jobs_at': data.get('jobs_at'), 'jobs': jobs,
                'serve': data.get('serve') if online else None}

    def job(self, j, seen, jobs_at, always):
        j = dict(j)
        state = j.get('state')
        status = state if state in JOB_ORDER else 'unknown'
        nxt = j.get('next_run')
        late = OVERDUE_SEC if always else 2 * 3600  # a PC that just woke catches up its missed runs first
        if state in ('ok', 'never') and nxt and jobs_at and nxt < jobs_at - late:
            status = 'overdue'
            j['why'] = j.get('why') or 'its scheduled run did not start'
        elif state == 'running' and j.get('kind') == 'timer' and j.get('started') and jobs_at \
                and jobs_at - j['started'] > HUNG_SEC:  # a hung run also blocks every later one
            status = 'overdue'
            j['why'] = f"one run has been going for {round((jobs_at - j['started']) / 3600)} h: hung?"
        j['status'] = status
        j['bad_since'] = (seen['bad_since'] if seen and status == 'failed' else
                          (j.get('started') or nxt) if status == 'overdue' else None)
        return j

    def freshness_for(self, name):
        """A collector's list (hub/freshness.py): what to report, and the clean checkouts to pull now."""
        inv = freshness.inventory(name)
        m = next((x for x in self.view()['machines'] if x['machine'] == name), None)
        inv['pull'] = [d['name'] for d in (m or {}).get('drift') or [] if d.get('fix') == 'pull']
        inv['update'] = [d['name'] for d in (m or {}).get('drift') or [] if d.get('fix') == 'update']
        return inv

    # ------------------------------------------------------------ alarms

    def problems(self, view):
        """{key: (text, bad_since, ready)} for everything wrong right now. ready = it should reach him now;
        a failure still inside its blip grace, or one its own OnFailure alarm already told, is not ready.
        Jobs of a machine that is asleep are left out: neither broken nor fixed while it sleeps."""
        now = view['now']
        out = {}
        for m in view['machines']:
            name = m['machine']
            if m['always_on'] and not m['online']:
                ts = m['tailscale'] or {}
                since = m['seen_at'] or ts.get('last_seen') or now
                hours = (now - since) / 3600
                ago = f'{round(hours)} h' if hours >= 1.5 else f'{round(hours * 60)} min'
                what = 'stopped reporting' if m['seen_at'] else 'is offline'
                ready = now - since >= (SILENT_SEC if m['seen_at'] else TS_OFFLINE_SEC)
                out[f'machine:{name}'] = (f'🔴 {name} {what} ({ago})\n👉 reply here: check {name}\n'
                                          f'{m["role"] or ""}'.rstrip(), since, ready)
                continue
            if not m['online']:
                continue
            stale = [p for p in m['problems'] if p.startswith('its job list stopped')]
            if stale and m['always_on']:
                out[f'machine:{name}:scan'] = (f'🔴 {name}: {stale[0]}\n👉 reply here: fix the job scan on {name}',
                                               now, True)
            for d in m.get('drift') or []:  # an outdated copy (hub/freshness.py): its grace is already spent
                out[d['key']] = (f"🔴 {name}: {d['text']}\n👉 reply here: update {d['name']} on {name}",
                                 d.get('since') or now, True)
            for j in m['jobs']:
                if j['status'] not in ('failed', 'overdue', 'gone') or not j.get('own', True):
                    continue  # a package's or an app's job is shown, never alarmed
                # No trust in the job's own OnFailure alarm: unit-alarm exits 0 when it skips a send,
                # and a crash loop never fires it. A second, quiet line a day beats a silent miss.
                ready = j['status'] != 'failed' or now - (j['bad_since'] or now) >= FAIL_GRACE_SEC
                # A one-off run that failed (a training job, nothing scheduled after it) is told once, out loud.
                once = j['status'] == 'failed' and j.get('kind') == 'schtask' and not j.get('next_run')
                out[f'job:{name}:{j["id"]}' + (':gone' if j['status'] == 'gone' else ':once' if once else '')] = (
                    f'🔴 {name}: {problem_text(j)}\n👉 reply here: fix {j["name"]} on {name}'
                    + (f'\n{j["why"]}' if j.get('why') else ''), j['bad_since'] or now, ready)
        return out

    def tick(self, now=None, send=None):
        """One alarm pass: new problems go out (one summary on the very first pass), fixed ones get a quiet 🟢.
        Sends happen outside any DB transaction (telegram-alarm can take a minute), one short write each."""
        real = send is None
        send = send or telegram
        if real:
            send = self._tracked(send)
        view = self.view(now)
        now = view['now']
        listed = {(m['machine'], j['id']): j for m in view['machines'] for j in m['jobs']}
        online = {m['machine'] for m in view['machines'] if m['online']}
        with self._lock:
            with self.store.connect() as con:
                known = {r['key']: dict(r) for r in con.execute('SELECT * FROM alarms')}
            wrong = self.problems(view)
            if '_baseline' not in known:  # first pass: one summary of all that is already wrong, grace or not
                # A machine not quiet long enough yet may just not have reported since the hub started.
                wrong = {k: v for k, v in wrong.items() if v[2] or k.startswith('job:')}
                lines = [t.split('\n')[0][2:] for _, (t, _, _) in sorted(wrong.items())]
                text = (f'🟠 {len(lines)} thing{"s" * (len(lines) != 1)} already broken on the machines\n'
                        '👉 reply here: fix them\n' + '\n'.join('· ' + x for x in lines[:12])) if lines else None
                if text is None or send(text, '_baseline', quiet=True):
                    rows = [('_baseline', text, now, now)] + [(k, t, since, now) for k, (t, since, _) in wrong.items()]
                    self._write(rows)
                return wrong
            for k, (t, since, ready) in wrong.items():
                r = known.get(k)
                if not ready or r and r['sent_at'] and (now - r['sent_at'] < EVERY_SEC or k.endswith((':gone', ':once'))):
                    continue  # a vanished job or a failed one-off is told once, not every day
                if send(t, k, quiet=k.startswith('job:') and not k.endswith(':once')):
                    self._write([(k, t, r['first_bad'] if r else since, now)])
            for k, r in known.items():
                if k == '_baseline' or k in wrong:
                    continue
                kind, machine = k.split(':')[:2]
                if kind == 'machine':
                    fixed = machine in online
                elif kind == 'drift':
                    fixed = machine in online  # online and no longer outdated
                else:
                    if machine not in online:
                        continue  # asleep: neither broken nor fixed while it sleeps
                    jid = k[len(f'job:{machine}:'):].removesuffix(':gone')
                    j = listed.get((machine, jid))
                    if not j:  # no longer listed and no longer shown as gone: let it go quietly
                        self._delete(k)
                        continue
                    # Fixed means a good run after it broke, not a scan that caught it running or
                    # a reboot that made systemd forget the failure.
                    fixed = j['status'] == 'ok' and (j.get('last_run') or 0) > (r['first_bad'] or 0)
                if fixed:
                    head = (r['text'] or '').split('\n')[0][2:]
                    if send(f'🟢 fixed: {head}', k + ':ok', quiet=True):
                        self._delete(k)
        return wrong

    def _tracked(self, send):
        def wrapped(text, key, quiet=False):
            ok = send(text, key, quiet=quiet)
            if ok:
                self.health.update(sent_at=time.time(), send_failing_since=None)
            elif not self.health['send_failing_since']:
                self.health['send_failing_since'] = time.time()
            return ok
        return wrapped

    def _write(self, rows):
        with self.store.connect() as con:
            con.executemany('INSERT OR REPLACE INTO alarms(key, text, first_bad, sent_at) VALUES (?, ?, ?, ?)', rows)

    def _delete(self, key):
        with self.store.connect() as con:
            con.execute('DELETE FROM alarms WHERE key = ?', (key,))

    def watch(self, every=60, delay=180):
        """The alarm loop in a daemon thread. The delay lets every collector report after a hub restart."""
        if not self.alarms:
            return None

        def loop():
            time.sleep(delay)
            while True:
                try:
                    self.tick()
                    self.health['tick_at'] = time.time()
                except Exception:  # one bad pass must not end the watch
                    log.exception('a watch pass failed')
                time.sleep(every)
        t = threading.Thread(target=loop, name='machines-watch', daemon=True)
        t.start()
        return t


def problem_text(j):
    if j['status'] == 'failed':
        return f"{j['name']} {'failed' if j.get('kind') == 'schtask' and not j.get('next_run') else 'keeps failing'}"
    if j['status'] == 'overdue':
        return f"{j['name']} did not run when it should have"
    return f"{j['name']} is no longer {'running' if j.get('kind') == 'daemon' else 'scheduled'}"


NOTIFY = None  # f(text, key, quiet) -> bool: the live hub's notices (hub/notify.py); else telegram-alarm


def telegram(text, key, quiet=False):
    """True once the alarm is taken. A failed send (DNS down, no token) is tried again next minute."""
    if NOTIFY:
        return NOTIFY(text, key, quiet)
    exe = shutil.which('telegram-alarm') or '/usr/local/bin/telegram-alarm'
    cmd = [exe, '--from', '🖥️ Machines'] + (['--quiet'] if quiet else []) + [text]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning('telegram-alarm: %s', e)
        return False
    if r.returncode:
        log.warning('telegram-alarm exit %s: %s', r.returncode, (r.stderr or r.stdout).strip()[:200])
    return r.returncode == 0


def parse_node(text):
    """The few numbers a collector would send, from node_exporter's text format."""
    def gauge(name, label=''):
        m = re.search(rf'^{name}\{{[^}}]*{label}[^}}]*\}}\s+(\S+)|^{name}\s+(\S+)', text, re.M)
        return float(m.group(1) or m.group(2)) if m else None
    idle = total = 0.0
    for m in re.finditer(r'^node_cpu_seconds_total\{([^}]*)\}\s+(\S+)', text, re.M):
        total += float(m.group(2))
        if 'mode="idle"' in m.group(1):
            idle += float(m.group(2))
    boot = gauge('node_boot_time_seconds')
    temp = gauge('node_thermal_zone_temp') or gauge('node_hwmon_temp_celsius')
    size, free = gauge('node_filesystem_size_bytes', 'mountpoint="/"'), gauge('node_filesystem_avail_bytes', 'mountpoint="/"')
    return {'cpus': len(set(re.findall(r'^node_cpu_seconds_total\{[^}]*cpu="(\d+)"', text, re.M))) or None,
            'mem_total': gauge('node_memory_MemTotal_bytes'), 'mem_avail': gauge('node_memory_MemAvailable_bytes'),
            'load': [gauge('node_load1'), gauge('node_load5'), gauge('node_load15')],
            'uptime_s': round(time.time() - boot) if boot else None,
            'cpu_pct': None,  # since boot only, which says nothing about now
            'temp_c': round(temp / 1000 if temp and temp > 200 else temp) if temp else None,
            'disks': [{'mount': '/', 'size': size, 'free': free}] if size and free is not None else []}
