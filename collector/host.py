"""What this machine is and what runs on it by itself, for the hub's machines view and its alarms.

collect.py adds report() to the live snapshot it pushes every 10 s. Cheap numbers (RAM, disk,
load, uptime) are read every run; the job list and versions cost subprocess calls, so they are
cached in the collector's state dir and refreshed every JOBS_EVERY / VERSIONS_EVERY seconds.

Jobs are read from the scheduler itself, never from a list someone keeps by hand, so a new job
is watched the moment it is installed (2026-11-22: alarms were lost for jobs nobody listed):
  - Linux: every systemd timer and its service, plus any failed or crash-looping service.
    alarm@X.service.service (the OnFailure Telegram hook) folds into X as alarm_failed.
  - macOS: his launchd agents (~/Library/LaunchAgents, not com.apple.*) and their last exit code.
  - Windows: non-Microsoft scheduled tasks, read with a hidden PowerShell (no console window).
  - his crontab lines are listed as not watched: cron keeps no result.

Each job: id, name, kind, desc, state (ok | failed | running | never | unknown), last_run,
next_run (epoch s, or None), code (last exit code), alarm (a failure hook is wired), alarm_failed,
own (his, not a package's or an app's), started (when a running job started), why (the failed
run's first error-looking line). The hub decides overdue, hung and gone from these.
"""
import glob
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import time

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hub'))
import settings  # noqa: E402  (hub/settings.py: the deployment's names)

JOBS_EVERY = 120        # seconds between job scans
VERSIONS_EVERY = 3600   # seconds between version checks
FRESH_EVERY = 600       # seconds between outdated-copy reports (hub/freshness.py)
WIN = sys.platform == 'win32'
MAC = sys.platform == 'darwin'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
ERRORISH = re.compile(r'denied|fatal|error|fail|abort|cannot|could not|timed? ?out|traceback|refused', re.I)


def run(cmd, timeout=20, encoding=None, strict=False):
    """stdout, or '' when the command is missing or times out. strict: raise instead, so a scan that
    could not read the scheduler keeps the last good job list rather than reporting an empty one."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding=encoding,
                              creationflags=NO_WINDOW, errors='replace').stdout
    except (OSError, subprocess.SubprocessError):
        if strict:
            raise
        return ''


# ---------------------------------------------------------------- stats

def stats(cache):
    """RAM, disks, load, uptime, cpu %. Missing pieces are left out, never guessed."""
    out = {'cpus': os.cpu_count()}
    try:
        if WIN:
            out.update(windows_stats())
        elif MAC:
            out.update(mac_stats())
        else:
            out.update(linux_stats(cache))
    except Exception as e:  # numbers are nice to have; the job list matters more
        print(f'host stats: {type(e).__name__}: {e}', file=sys.stderr)
    out['disks'] = disks()
    return out


def linux_stats(cache):
    mem = {}
    with open('/proc/meminfo') as f:
        for line in f:
            k, v = line.split(':', 1)
            mem[k] = int(v.split()[0]) * 1024
    with open('/proc/uptime') as f:
        up = float(f.read().split()[0])
    with open('/proc/stat') as f:
        cpu = [int(x) for x in f.readline().split()[1:]]
    idle, total = cpu[3] + cpu[4], sum(cpu)
    prev = cache.get('cpu')
    cache['cpu'] = [idle, total]
    pct = None
    if prev and total > prev[1]:
        pct = round(100 * (1 - (idle - prev[0]) / (total - prev[1])))
    temp = None
    for p in glob.glob('/sys/class/thermal/thermal_zone*/temp'):
        try:
            with open(p) as f:
                t = int(f.read()) / 1000
            temp = max(temp or 0, t)
        except (OSError, ValueError):
            pass
    return {'mem_total': mem.get('MemTotal'), 'mem_avail': mem.get('MemAvailable'),
            'load': [round(x, 2) for x in os.getloadavg()], 'uptime_s': round(up),
            'cpu_pct': pct, 'temp_c': round(temp) if temp else None}


def mac_stats():
    total = int(run(['sysctl', '-n', 'hw.memsize']) or 0)
    page = 16384
    free = 0
    for line in run(['vm_stat']).splitlines():
        m = re.match(r'Mach Virtual Memory Statistics: \(page size of (\d+) bytes\)', line)
        if m:
            page = int(m.group(1))
        m = re.match(r'Pages (free|inactive|speculative|purgeable):\s+(\d+)', line)
        if m:
            free += int(m.group(2)) * page
    boot = re.search(r'sec = (\d+)', run(['sysctl', '-n', 'kern.boottime']))
    return {'mem_total': total or None, 'mem_avail': free or None,
            'load': [round(x, 2) for x in os.getloadavg()],
            'uptime_s': round(time.time() - int(boot.group(1))) if boot else None}


def windows_stats():
    import ctypes

    class MEM(ctypes.Structure):
        _fields_ = [('len', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total', ctypes.c_ulonglong),
                    ('avail', ctypes.c_ulonglong), ('tp', ctypes.c_ulonglong), ('ap', ctypes.c_ulonglong),
                    ('tv', ctypes.c_ulonglong), ('av', ctypes.c_ulonglong), ('ext', ctypes.c_ulonglong)]
    m = MEM()
    m.len = ctypes.sizeof(MEM)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    return {'mem_total': m.total, 'mem_avail': m.avail,
            'uptime_s': round(ctypes.windll.kernel32.GetTickCount64() / 1000)}


def disks():
    """Real local filesystems: size and free bytes, one row per device."""
    if WIN:
        mounts = [f'{d}:\\' for d in 'CDEFG' if os.path.exists(f'{d}:\\')]
    elif MAC:
        mounts = ['/System/Volumes/Data' if os.path.isdir('/System/Volumes/Data') else '/']
    else:
        mounts, seen = [], set()
        try:
            with open('/proc/mounts') as f:
                for line in f:
                    dev, mnt, fs = line.split()[:3]
                    if fs in ('ext4', 'xfs', 'btrfs', 'zfs', 'ext3', 'f2fs') and dev not in seen:
                        seen.add(dev)
                        mounts.append(mnt.replace('\\040', ' '))
        except OSError:
            mounts = ['/']
    out = []
    for mnt in mounts:
        try:
            u = shutil.disk_usage(mnt)
        except OSError:
            continue
        out.append({'mount': mnt, 'size': u.total, 'free': u.free})
    return out


# ---------------------------------------------------------------- versions

def first_line(cmd):
    return (run(cmd, timeout=10).strip().splitlines() or [''])[0][:60] or None


def versions():
    here = os.path.dirname(os.path.abspath(__file__))
    v = {'os': os_name(), 'python': platform.python_version(),
         'collector': first_line(['git', '-C', here, 'log', '-1', '--format=%h %cs'])}
    for name, cmd in (('claude', ['claude', '--version']), ('codex', ['codex', '--version']),
                      ('tailscale', ['tailscale', 'version'])):
        exe = shutil.which(cmd[0]) or (os.path.expanduser('~/.local/bin/' + cmd[0])
                                       if os.path.exists(os.path.expanduser('~/.local/bin/' + cmd[0])) else None)
        if exe:
            v[name] = first_line([exe] + cmd[1:])
    return v


# ---------------------------------------------------------------- outdated copies (hub/freshness.py)

def git(path, *args, timeout=10):
    return run(['git', '-C', path, *args], timeout=timeout).strip()


def checkout(path):
    """Local facts only, no fetch: a box whose login broke still says what it has."""
    sha, _, when = git(path, 'log', '-1', '--format=%H %ct').partition(' ')
    if not sha:
        return None
    return {'sha': sha, 'time': int(when or 0), 'branch': git(path, 'rev-parse', '--abbrev-ref', 'HEAD'),
            'dirty': bool(git(path, 'status', '--porcelain', '--untracked-files=no'))}


def file_hash(path):
    import hashlib
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def existing(paths):
    """The first listed path that exists here, by its real path (~/projects and ~/Projects are one on a Mac)."""
    seen = []
    for p in paths:
        full = os.path.realpath(os.path.expanduser(p))
        if os.path.exists(full) and full not in seen:
            seen.append(full)
    return seen[0] if seen else None


# The tools a box updates by itself when the hub says it trails the newest machine. A fixed list: the hub only
# names a tool, it never sends a command (its answer is an open read on the tailnet).
UPDATERS = {'claude': ['claude', 'update']}


def fresh(hub, machine):
    """What this box has of each copy the hub lists for it; a clean checkout the hub calls stale is pulled first."""
    import urllib.parse
    import urllib.request
    url = hub.rstrip('/') + '/api/freshness?' + urllib.parse.urlencode({'machine': machine})
    with urllib.request.urlopen(url, timeout=10) as r:
        inv = json.load(r)
    out = {'checkouts': [], 'files': [], 'pulled': []}
    for c in inv.get('checkouts') or []:
        path = existing(c.get('paths') or [])
        if not path:
            continue
        if c['name'] in (inv.get('pull') or []):  # the self-fix: he hears only when this did not help
            run(['git', '-C', path, 'pull', '--ff-only', '-q'], timeout=60)
            out['pulled'].append(c['name'])
        got = checkout(path)
        if got:
            out['checkouts'].append({'name': c['name'], **got})
    for tool in inv.get('update') or []:
        cmd = UPDATERS.get(tool)
        exe = cmd and (shutil.which(cmd[0]) or (os.path.expanduser('~/.local/bin/' + cmd[0])
                                                if os.path.exists(os.path.expanduser('~/.local/bin/' + cmd[0])) else None))
        if exe:  # in the background: an update takes minutes and the collector runs every 10 s
            try:
                subprocess.Popen([exe] + cmd[1:], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, start_new_session=not WIN)
                out['updated'] = out.get('updated', []) + [tool]
            except OSError:
                pass
    for f in inv.get('files') or []:
        path = existing(f.get('paths') or [])
        digest = file_hash(path) if path else None
        if digest:
            out['files'].append({'name': f['name'], 'sha256': digest})
    return out


def os_name():
    if MAC:
        return 'macOS ' + platform.mac_ver()[0]
    if WIN:
        return f'Windows {platform.release()} ({platform.version()})'
    try:
        with open('/etc/os-release') as f:
            rel = dict(line.rstrip().split('=', 1) for line in f if '=' in line)
        return rel.get('PRETTY_NAME', '').strip('"') + ' · ' + platform.release()
    except OSError:
        return platform.platform()


# ---------------------------------------------------------------- jobs: Linux

SHOW = ('Id', 'Description', 'ActiveState', 'SubState', 'Result', 'ExecMainStatus',
        'ExecMainStartTimestamp', 'ExecMainExitTimestamp', 'OnFailure', 'NRestarts',
        'Transient', 'TriggeredBy', 'FragmentPath', 'UnitFileState', 'Type', 'ExecStart')
# His own units live in /etc/systemd/system (linux-setup installs them); packages own /usr/lib.
OWN_UNIT = '/etc/systemd/system/'


def show(units):
    """systemctl show for many units in one call: {unit id: {prop: value}}."""
    if not units:
        return {}
    text = run(['systemctl', 'show', '--timestamp=unix', '-p', ','.join(SHOW), '--'] + list(units), timeout=30,
               strict=True)
    out = {}
    for block in text.split('\n\n'):
        p = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if p.get('Id'):
            out[p['Id']] = p
    return out


def exec_words(v):
    """systemctl's ExecStart ("{ path=… ; argv[]=/usr/bin/x --y ; … }", one block per line) as words."""
    words = []
    for m in re.finditer(r'argv\[\]=(.*?) ; ', v or ''):
        words += m.group(1).split()
    return words


_AI_CACHE = {}


def ai_of(words):
    """Whether a job's scripts call a language model (collector/llm.py), for the hub's AI jobs page."""
    try:
        import llm
        return llm.sniff(words, _AI_CACHE)
    except Exception as e:  # a script it can't read must not cost the whole job list
        print(f'host ai: {type(e).__name__}: {e}', file=sys.stderr)
        return None


def ts(v):
    v = (v or '').lstrip('@')
    return int(v) if v.isdigit() and int(v) > 0 else None


def why_failed(unit):
    """The failed run's first error-looking line (else its last line), like unit-alarm does."""
    lines = [x for x in run(['journalctl', '-u', unit, '-n', '60', '-o', 'cat', '--no-pager', '-q']).splitlines()
             if x.strip() and not re.match(r'(Starting|Started|Finished|Failed to start) ', x)
             and not re.search(r': (Main process exited|Failed with result|Triggering OnFailure|Consumed|Deactivated)', x)]
    hit = next((x for x in reversed(lines) if ERRORISH.search(x)), None)
    return (hit or (lines[-1] if lines else ''))[:200] or None


def service_state(p):
    if p.get('ActiveState') in ('activating', 'deactivating', 'reloading') and p.get('SubState') != 'auto-restart':
        return 'running'
    if p.get('ActiveState') == 'failed' or p.get('SubState') == 'auto-restart' or p.get('Result') not in (None, '', 'success'):
        return 'failed'
    if not ts(p.get('ExecMainStartTimestamp')) and p.get('ActiveState') != 'active':
        return 'never'
    return 'ok'


def linux_jobs():
    timers = json.loads(run(['systemctl', 'list-timers', '--all', '-o', 'json', '--no-pager'], strict=True))
    failed = run(['systemctl', 'list-units', '--all', '--type=service', '--plain', '--no-legend', '--no-pager',
                  '--state=failed,auto-restart'], strict=True).split()
    failed = [u for u in failed if u.endswith('.service')]
    services = {t['activates'] for t in timers if t.get('activates')}
    # Long-running services too, healthy or not, so a new watcher daemon is listed the day it starts
    # (his rule 2026-11-22: every background job is a unit, so Inside lists it).
    running = run(['systemctl', 'list-units', '--type=service', '--state=running', '--plain', '--no-legend',
                   '--no-pager'], strict=True).split()
    enabled = run(['systemctl', 'list-unit-files', '--type=service', '--state=enabled', '--plain', '--no-legend',
                   '--no-pager'], strict=True).split()
    daemons = {u for u in running + enabled if u.endswith('.service') and not u.endswith('@.service')} - services
    info = show(sorted(services | set(failed) | daemons | {t['unit'] for t in timers if t.get('unit')}))
    alarm_bad = {u[len('alarm@'):-len('.service')] for u in failed if u.startswith('alarm@')}
    jobs = []

    def job(unit, p, timer=None):
        state = service_state(p)
        why = None
        last = ts(p.get('ExecMainExitTimestamp')) or ts(p.get('ExecMainStartTimestamp'))
        if timer and timer.get('last'):
            last = max(last or 0, timer['last'] // 1_000_000)
            if state == 'never':  # it ran before this boot: systemd forgot the result, not the run
                state = 'ok'
        tp = info.get(timer['unit'], {}) if timer else {}
        if tp and tp.get('ActiveState') != 'active' and tp.get('UnitFileState') == 'enabled':
            state, why = 'failed', 'its timer is stopped: it will not run again'  # enabled but not ticking
        name = unit[:-len('.service')]
        kind = 'timer' if timer else 'daemon' if unit in daemon_units else 'service'
        return {'id': 'systemd:' + name, 'name': name, 'kind': kind,
                'desc': p.get('Description') or None, 'state': state, 'last_run': last,
                'next_run': timer['next'] // 1_000_000 if timer and timer.get('next') else None,
                'code': int(p['ExecMainStatus']) if p.get('ExecMainStatus', '').lstrip('-').isdigit() else None,
                'restarts': int(p.get('NRestarts') or 0),
                'alarm': 'alarm@' in (p.get('OnFailure') or ''), 'alarm_failed': unit in alarm_bad,
                'own': (p.get('FragmentPath') or '').startswith(OWN_UNIT) and p.get('Transient') != 'yes',
                'started': ts(p.get('ExecMainStartTimestamp')) if state == 'running' else None,
                'last_start': ts(p.get('ExecMainStartTimestamp')),
                'why': why or (why_failed(unit) if state == 'failed' else None),
                'ai': ai_of(exec_words(p.get('ExecStart')))}

    for t in timers:
        unit = t.get('activates')
        if unit and unit in info:
            jobs.append(job(unit, info[unit], t))
    daemon_units = set()
    for unit in sorted(daemons):
        p = info.get(unit) or {}
        if (not (p.get('FragmentPath') or '').startswith(OWN_UNIT) or p.get('Transient') == 'yes'
                or p.get('Type') == 'oneshot' or unit.startswith(('alarm@', 'snap.')) or p.get('TriggeredBy')):
            continue  # a package's daemon, or a boot-time one-shot: not a long-running job of his
        daemon_units.add(unit)
        j = job(unit, p)
        if j['state'] in ('never', 'ok') and p.get('ActiveState') != 'active' and p.get('Result') != 'success':
            j['state'], j['why'] = 'failed', 'enabled but not running'  # a clean stop was someone's choice
        jobs.append(j)
    for unit in failed:
        if unit in services or unit in daemon_units or unit.startswith('alarm@') or unit not in info:
            continue
        p = info[unit]
        if p.get('Transient') == 'yes' or (p.get('TriggeredBy') or '').endswith('.socket'):
            continue  # a systemd-run jail or one socket connection: gone when reset, never a job of his
        jobs.append(job(unit, p))
    return jobs + cron_jobs()


def cron_jobs():
    """His own crontab (the distro's /etc/cron.d is left out: packages own those)."""
    out = []
    for line in run(['crontab', '-l']).splitlines():
        line = line.strip()
        if not line or line.startswith('#') or re.match(r'^[A-Z_]+=', line):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6 and not line.startswith('@'):
            continue
        sched, cmd = (parts[0], line.split(None, 1)[1]) if line.startswith('@') else (' '.join(parts[:5]), parts[5])
        out.append({'id': 'cron:' + re.sub(r'\W+', '-', cmd)[:60], 'name': cmd[:80], 'kind': 'cron', 'desc': sched,
                    'state': 'unknown', 'last_run': None, 'next_run': None, 'code': None,
                    'alarm': 'telegram-alarm' in cmd, 'alarm_failed': False, 'own': True, 'why': None,
                    'ai': ai_of(cmd.split())})
    return out


# ---------------------------------------------------------------- jobs: macOS

def mac_jobs():
    loaded = {}
    for line in run(['launchctl', 'list']).splitlines()[1:]:
        parts = line.split('\t')
        if len(parts) == 3:
            loaded[parts[2]] = (parts[0], parts[1])
    jobs = []
    for path in sorted(glob.glob(os.path.expanduser('~/Library/LaunchAgents/*.plist'))):
        try:
            with open(path, 'rb') as f:
                p = plistlib.load(f)
        except Exception:
            continue
        label = p.get('Label') or os.path.basename(path)[:-6]
        if label.startswith('com.apple.') or p.get('Disabled'):
            continue
        pid, status = loaded.get(label, (None, None))
        code = int(status) if status and status.lstrip('-').isdigit() else None
        own = label.startswith(settings.OWNER['launchd_prefix'])
        keep = bool(p.get('KeepAlive'))
        if label not in loaded:
            # Installed but not loaded, so it never runs: broken if it is his, an app's own business if not.
            state = 'failed' if own else 'unknown'
        elif pid and pid != '-':
            state = 'running' if not keep else 'ok'
        elif code is not None and code < 0:
            state = 'unknown'  # killed by a signal (logout, shutdown, launchd stopping it): not a failed run
        elif code not in (None, 0):
            state = 'failed' if own else 'unknown'
        else:
            state = 'ok'
        log = p.get('StandardErrorPath') or p.get('StandardOutPath')
        last = None
        if log and os.path.exists(log):
            last = round(os.path.getmtime(log))  # the last time it wrote anything: a lower bound for its last run
        sched = (f"every {p['StartInterval']} s" if p.get('StartInterval') else
                 'calendar' if p.get('StartCalendarInterval') else 'always on' if keep else
                 'at login' if p.get('RunAtLoad') else 'on demand')
        why = None
        if state == 'failed':
            why = 'not loaded (launchctl list does not have it)' if label not in loaded else f'last exit code {code}'
            if log and os.path.exists(log):
                tail = [x for x in read_tail(log).splitlines() if x.strip()]
                hit = next((x for x in reversed(tail) if ERRORISH.search(x)), None)
                why = (hit or why)[:200]
        jobs.append({'id': 'launchd:' + label, 'name': label, 'kind': 'launchd', 'desc': sched, 'state': state,
                     'last_run': last, 'next_run': None, 'code': code, 'alarm': False, 'alarm_failed': False,
                     'own': own, 'why': why, 'ai': ai_of(p.get('ProgramArguments') or [p.get('Program')])})
    return jobs + cron_jobs()


def read_tail(path, size=4096):
    try:
        with open(path, 'rb') as f:
            f.seek(max(0, os.path.getsize(path) - size))
            return f.read().decode('utf-8', 'replace')
    except OSError:
        return ''


# ---------------------------------------------------------------- jobs: Windows

# Dates go out as Unix seconds, so a Czech or English Windows reads the same.
# UTF-8 out: in the console code page a task description's curly quotes came out as plain " and broke the JSON.
PS = r"""
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$o = Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object {
  $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
  function U($d) { if ($d -and $d.Year -gt 2000) { [int64](($d.ToUniversalTime()) - [datetime]'1970-01-01').TotalSeconds } else { $null } }
  [pscustomobject]@{ name = $_.TaskPath + $_.TaskName; state = [string]$_.State; desc = $_.Description;
    last = (U $i.LastRunTime); next = (U $i.NextRunTime); code = $i.LastTaskResult;
    run = (($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' ; ') }
}
@($o) | ConvertTo-Json -Compress
"""
# His own tasks run something from his projects or dotfiles, or through a hidden conhost (machines skill).
OWN_TASK = re.compile(r'\\(projects|\.local|\.config)\\|conhost\.exe --headless', re.I)
RUNNING, NEVER = 267009, 267011
# Ended rather than failed: killed at logoff or by a person (0x40010004, 0xC000013A), terminated by the user (0x41306).
ENDED = {0x40010004, 0xC000013A, 0x41306}  # 0x41301 running now, 0x41303 has not run yet


def windows_jobs():
    import base64  # -EncodedCommand: a multi-line -Command loses everything after its first line
    enc = base64.b64encode(PS.encode('utf-16-le')).decode()
    text = run(['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-EncodedCommand', enc], timeout=60,
               encoding='utf-8')
    try:
        rows = json.loads(text or '[]')
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    jobs = []
    for r in rows:
        code = r.get('code')
        if r.get('state') == 'Disabled':
            continue
        own = bool(OWN_TASK.search(r.get('run') or ''))
        state = ('running' if code == RUNNING or r.get('state') == 'Running' else 'never' if code == NEVER
                 else 'ok' if code in (0, None) else 'failed')
        if state == 'failed' and (code or 0) & 0xffffffff in ENDED:
            # Ended rather than failed (logoff, a person, the time limit). An own one-off cut off in the last day
            # still alarms: an overnight training run killed by a reboot or its limit never finished.
            recent = not r.get('next') and own and (r.get('last') or 0) > time.time() - 86400
            state = 'failed' if recent else 'unknown'
        if state == 'failed' and not own:
            state = 'unknown'  # an app's own updater (OneDrive, drivers): shown, not his to fix
        jobs.append({'id': 'schtask:' + r['name'], 'name': r['name'].lstrip('\\'), 'kind': 'schtask',
                     'desc': (r.get('desc') or r.get('run') or '')[:120] or None, 'state': state,
                     'last_run': r.get('last'), 'next_run': r.get('next'), 'code': code,
                     'alarm': False, 'alarm_failed': False, 'own': own,
                     'why': (('stopped before it finished' if (code or 0) & 0xffffffff in ENDED else
                              f'last result 0x{code & 0xffffffff:x}') if state == 'failed' else None),
                     'ai': ai_of((r.get('run') or '').split()) if own else None})
    return jobs


# ---------------------------------------------------------------- tailnet names

SERVE_EVERY = 120       # seconds between backend probes (the cheap connection sample runs every push)
PROXY = re.compile(r'^(?:https?|https\+insecure)://(?:127\.0\.0\.1|localhost|\[::1\]):(\d+)')


def serve_config():
    """{svc:name: [backend url, ...]} this box serves through `tailscale serve`; None without tailscale."""
    exe = shutil.which('tailscale') or ('/Applications/Tailscale.app/Contents/MacOS/Tailscale' if MAC else None)
    if not exe or not os.path.exists(exe):
        return None
    try:
        d = json.loads(run([exe, 'serve', 'status', '--json'], timeout=10, strict=True) or '{}')
    except (ValueError, OSError, subprocess.SubprocessError):
        return None
    out = {}
    for name, svc in (d.get('Services') or {}).items():
        targets = {h.get('Proxy') for web in (svc.get('Web') or {}).values()
                   for h in (web.get('Handlers') or {}).values() if h.get('Proxy')}
        out[name] = sorted(targets)
    return out


def busy_ports():
    """{port: 'live' | 'recent'} for backend ports someone came to through the tailnet (Linux).
    live: tailscaled holds a connection to it now (only its own sockets count, so a local client like
    this collector is no visit). recent: a connection the backend closed in the last minute (TIME-WAIT,
    no owner known: the hub's own port also counts its local clients). None where ss can't say."""
    if WIN or MAC:
        return None
    out = {}
    lines = run(['ss', '-tanpH'], timeout=5).splitlines()
    owners = any('users:' in line for line in lines)
    for line in lines:
        f = line.split()
        if len(f) < 5 or not f[4].startswith(('127.', '[::1]')):
            continue
        if f[0] == 'ESTAB' and (not owners or '"tailscaled"' in line):
            out[(f[4] if owners else f[3]).rsplit(':', 1)[-1]] = 'live'
        elif f[0] == 'TIME-WAIT':
            out.setdefault(f[3].rsplit(':', 1)[-1], 'recent')
    return out


def listeners():
    """{port: what runs there} for local listening ports (Linux): the systemd unit's Description, else
    the process name. Lets the names list say "Budget app" instead of a port."""
    if WIN or MAC:
        return {}
    out, units = {}, {}
    for line in run(['ss', '-ltnpH'], timeout=5).splitlines():
        f = line.split()
        m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        if len(f) < 4 or not m or m.group(1) == 'tailscaled':  # tailscaled also listens on node ports
            continue
        port = f[3].rsplit(':', 1)[-1]
        unit = None
        try:
            with open(f'/proc/{m.group(2)}/cgroup') as c:
                u = re.search(r'/([^/]+\.service)\s*$', c.read().strip().splitlines()[-1])
                unit = u.group(1) if u else None
        except OSError:
            pass
        if unit and unit not in units:
            units[unit] = run(['systemctl', 'show', '-p', 'Description', '--value', unit], timeout=5).strip()
        out.setdefault(port, units.get(unit) or m.group(1))
    return out


def probe(url):
    """(up, page): up when the backend answers, False when nothing listens or a proxy in front says the app behind
    is down; page False when it answers 404 at / (an API for an app, nothing to open in a browser)."""
    import ssl
    import urllib.request
    import urllib.error
    ctx = None
    if url.startswith('https+insecure://'):  # tailscale serve's own scheme: https, certificate not checked
        url, ctx = 'https://' + url[len('https+insecure://'):], ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Connection': 'close'}), timeout=2,
                                    context=ctx):
            return True, True
    except urllib.error.HTTPError as e:
        return e.code not in (502, 503, 504), e.code != 404
    except Exception:
        return False, False


def serve(cache, now):
    """Every tailnet name this box serves: backend, up (it answers), last_used (a connection to it seen).
    The connection sample runs on every push (10 s) before any probe, so the probe never counts as use."""
    ports = busy_ports()
    used = cache.setdefault('serve_used', {})
    conf = cache.get('serve_conf')
    if conf is None or not 0 <= now - cache.get('serve_at', 0) < SERVE_EVERY:
        conf = serve_config()
        if conf is None:
            return None
        from concurrent.futures import ThreadPoolExecutor
        todo = sorted({t for targets in conf.values() for t in targets})
        with ThreadPoolExecutor(8) as pool:  # a hung backend costs this push 2 s, not 2 s each
            res = dict(zip(todo, pool.map(probe, todo)))
        up, page = {t: r[0] for t, r in res.items()}, {t: r[1] for t, r in res.items()}
        try:
            cache['serve_what'] = listeners()
        except Exception:
            cache['serve_what'] = {}
        cache['serve_conf'], cache['serve_up'], cache['serve_page'], cache['serve_at'] = conf, up, page, now
        cache['probed_at'] = time.time()  # our own probe leaves TIME-WAITs for a minute: not a visit
    up, page = cache.get('serve_up') or {}, cache.get('serve_page') or {}
    cache.setdefault('probed_at', cache.get('serve_at', now))  # a cache from before probed_at existed
    out = []
    for name, targets in conf.items():
        tports = {m.group(1) for t in targets for m in [PROXY.match(t)] if m}
        hit = {ports[p] for p in tports if p in ports} if ports else set()
        if 'live' in hit or ('recent' in hit and now - cache.get('probed_at', 0) > 65):
            used[name] = now
        what = [cache.get('serve_what', {}).get(p) for p in sorted(tports)]
        out.append({'name': name, 'backends': targets, 'what': ', '.join(w for w in what if w) or None, 'up': all(up.get(t) for t in targets) if targets else None,
                    'page': any(page.get(t, True) for t in targets) if targets else None,
                    'last_used': round(used[name]) if used.get(name) else None})
    for name in [n for n in used if n not in conf]:
        del used[name]
    return {'at': round(cache['serve_at']), 'sampled': ports is not None, 'names': out}


# ---------------------------------------------------------------- report

def scan_jobs():
    try:
        return windows_jobs() if WIN else mac_jobs() if MAC else linux_jobs()
    except Exception as e:
        print(f'host jobs: {type(e).__name__}: {e}', file=sys.stderr)
        return None


def report(cache_path, now=None, hub=None, machine=None):
    """This machine for the hub: stats every call, jobs and versions from a cache refreshed on its own clock."""
    now = now or time.time()
    try:
        with open(cache_path) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    if not 0 <= now - cache.get('jobs_at', 0) < JOBS_EVERY * (2.5 if WIN else 1):  # stale, or the clock stepped back
        jobs = scan_jobs()
        if jobs is not None:
            cache['jobs'], cache['jobs_at'] = jobs, now
    if now - cache.get('versions_at', 0) >= VERSIONS_EVERY:
        cache['versions'], cache['versions_at'] = versions(), now
    if hub and machine and now - cache.get('fresh_at', 0) >= FRESH_EVERY:
        try:
            cache['fresh'] = fresh(hub, machine)
        except Exception as e:  # noqa: BLE001 - the hub away: keep the last report
            print(f'host fresh: {type(e).__name__}: {e}', file=sys.stderr)
        cache['fresh_at'] = now
    out = {'stats': stats(cache), 'versions': cache.get('versions') or {},
           'jobs': cache.get('jobs') or [], 'jobs_at': round(cache.get('jobs_at') or 0) or None}
    if cache.get('fresh'):
        out['fresh'] = cache['fresh']
    try:
        out['serve'] = serve(cache, now)
    except Exception as e:  # never lose the rest of the report over the names
        print(f'host serve: {type(e).__name__}: {e}', file=sys.stderr)
    try:
        tmp = cache_path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(cache, f)
        os.replace(tmp, cache_path)
    except OSError:
        pass
    return out


if __name__ == '__main__':  # python3 collector/host.py: print what this machine would report
    import tempfile
    print(json.dumps(report(os.path.join(tempfile.gettempdir(), 'agent-usage-host-test.json')), indent=1))
