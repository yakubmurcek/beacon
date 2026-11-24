"""Outdated copies (task 1886): every listed checkout and file on every machine, compared with its source.

Five times a bug traced back to one box running an old copy of a script, tool or config (the last: a CLI whose daily
GitHub refresh failed on an expired login, 2026-11-22). Each fix covered one copy; this watches all of them.

- The collector reports local facts only (collector/host.py `fresh`): each checkout's HEAD, commit time, branch and
  dirtiness, each file's sha256. It makes no network call for them, so a box whose login or network broke still
  reports what it has.
- The hub compares with the source on its own credentials: a GitHub repo's default-branch head (`gh api`, cached),
  or a file of the hub's own checkout. A copy older than the source past its grace is a machine problem: red on
  /machines and one Telegram line (hub/machines.py).
- The collector pulls a clean checkout the hub calls stale (`git pull --ff-only`), so he only hears when that failed.
- Two checks need no list: a tool (Claude Code, Codex) far behind the newest version on another machine, and a
  machine whose sessions post no cards through their hook while the nightly backstop keeps filling them in.

The list lives in the settings (`freshness` in config.example.json): checkouts by path and repo, files by path and
the file they copy (in the hub's checkout, or `repo` on GitHub), each with the machines it applies to and a grace in hours; `strict` lists the
machines where a checkout must stay on its branch.
"""
import hashlib
import os
import re
import sqlite3
import subprocess
import threading
import time

from settings import CONFIG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAD_EVERY = 600          # seconds between GitHub head lookups per repo
DEFAULT_GRACE_H = 26      # a daily refresh plus slack
HOOK_WINDOW = 2 * 86400   # cards looked at for the hook check
HOOK_MIN_MISSED = 3       # backstop-filled turns, with no hook card, before a machine counts as deaf


def spec():
    return CONFIG.get('freshness') or {}


def applies(item, machine):
    ms = item.get('machines')
    return not ms or machine in ms


def inventory(machine):
    """What one machine should report: its checkouts and files, by name and path(s)."""
    s = spec()
    def paths(item):
        p = item.get('path')
        return [p] if isinstance(p, str) else list(p or [])
    return {'checkouts': [{'name': c['name'], 'paths': paths(c)} for c in s.get('checkouts') or [] if applies(c, machine)],
            'files': [{'name': f['name'], 'paths': paths(f)} for f in s.get('files') or [] if applies(f, machine)]}


def version_tuple(text):
    m = re.search(r'(\d+)\.(\d+)\.(\d+)', text or '')
    return tuple(int(x) for x in m.groups()) if m else None


class Sources:
    """The source side, on the hub's own credentials: repo heads (cached) and its checkout's files."""

    def __init__(self, gh=None):
        self._heads = {}   # repo -> (checked at, (sha, commit time) or None)
        self._lock = threading.Lock()
        self.gh = gh or self._gh
        self.failing = {}  # repo -> since when its head can't be read (the watcher's own blind spot)

    @staticmethod
    def _gh(repo):
        out = subprocess.run(['gh', 'api', f'repos/{repo}/commits/HEAD', '--jq', '.sha + " " + .commit.committer.date'],
                             capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return None
        sha, _, date = out.stdout.strip().partition(' ')
        from datetime import datetime
        return sha, datetime.fromisoformat(date.replace('Z', '+00:00')).timestamp()

    def head(self, repo, now):
        with self._lock:
            at, got = self._heads.get(repo, (0, None))
            if now - at < HEAD_EVERY:
                return got
        try:
            got = self.gh(repo)
        except (OSError, subprocess.SubprocessError, ValueError):
            got = None
        with self._lock:
            if got:
                self.failing.pop(repo, None)
            else:
                self.failing.setdefault(repo, now)
                got = self._heads.get(repo, (0, None))[1]  # keep the last good head
            self._heads[repo] = (now, got)
        return got

    def repo_file(self, repo, source, now):
        """(sha256, last commit time) of a file in another repo, through GitHub, cached like the heads."""
        key = f'{repo}:{source}'
        with self._lock:
            at, got = self._heads.get(key, (0, None))
            if now - at < HEAD_EVERY:
                return got
        try:
            raw = subprocess.run(['gh', 'api', f'repos/{repo}/contents/{source}', '-H', 'Accept: application/vnd.github.raw'],
                                 capture_output=True, timeout=15)
            when = subprocess.run(['gh', 'api', f'repos/{repo}/commits?path={source}&per_page=1',
                                   '--jq', '.[0].commit.committer.date'], capture_output=True, text=True, timeout=15)
            from datetime import datetime
            got = (hashlib.sha256(raw.stdout).hexdigest(),
                   datetime.fromisoformat(when.stdout.strip().replace('Z', '+00:00')).timestamp()) \
                if raw.returncode == 0 and when.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            got = None
        with self._lock:
            if got:
                self.failing.pop(key, None)
            else:
                self.failing.setdefault(key, now)
                got = self._heads.get(key, (0, None))[1]
            self._heads[key] = (now, got)
        return got

    @staticmethod
    def file(source):
        """(sha256, last commit time) of a file in the hub's checkout, or None."""
        path = os.path.join(ROOT, source)
        try:
            with open(path, 'rb') as f:
                digest = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return None
        try:
            out = subprocess.run(['git', '-C', ROOT, 'log', '-1', '--format=%ct', '--', source],
                                 capture_output=True, text=True, timeout=5)
            changed = int(out.stdout.strip() or 0)
        except (OSError, subprocess.SubprocessError, ValueError):
            changed = 0
        return digest, changed or time.time()


def check(machine, fresh, sources, now):
    """Problems for one machine's `fresh` report: [{key, text, since, fix?}]. `since` is when it went stale."""
    s = spec()
    out = []
    got = {c['name']: c for c in (fresh or {}).get('checkouts') or []}
    for c in s.get('checkouts') or []:
        if not applies(c, machine) or c['name'] not in got:
            continue
        mine = got[c['name']]
        branch = c.get('branch', 'main')
        if mine.get('branch') != branch:
            if machine in (c.get('strict') or []):  # nobody works there: a side branch is a stuck copy
                out.append({'key': f"drift:{machine}:{c['name']}", 'since': mine.get('time'), 'name': c['name'],
                            'text': f"{c['name']} is stuck on branch {mine.get('branch')}, not {branch}"})
            continue  # elsewhere an agent's working branch: not a copy anyone runs
        head = sources.head(c['repo'], now)
        if not head or mine.get('sha') == head[0] or (mine.get('time') or 0) >= head[1]:
            continue  # the same, or ahead (a local commit not pushed yet)
        grace = 3600 * c.get('grace_h', DEFAULT_GRACE_H)
        if now - head[1] < grace:
            continue
        hours = round((now - head[1]) / 3600)
        dirty = ' (local edits block its update)' if mine.get('dirty') else ''
        out.append({'key': f"drift:{machine}:{c['name']}", 'since': head[1], 'fix': 'pull' if not mine.get('dirty') else None,
                    'name': c['name'], 'text': f"{c['name']} is outdated: missing {hours} h of changes{dirty}"})
    files = {f['name']: f for f in (fresh or {}).get('files') or []}
    for f in s.get('files') or []:
        if not applies(f, machine) or f['name'] not in files:
            continue
        src = sources.repo_file(f['repo'], f['source'], now) if f.get('repo') else sources.file(f['source'])
        if not src or files[f['name']].get('sha256') == src[0]:
            continue
        if now - src[1] < 3600 * f.get('grace_h', DEFAULT_GRACE_H):
            continue
        out.append({'key': f"drift:{machine}:{f['name']}", 'since': src[1], 'name': f['name'],
                    'text': f"{f['name']} is an old copy (the source changed {round((now - src[1]) / 3600)} h ago)"})
    return out


def version_lag(machines):
    """{machine: [problem]} for tools far behind the newest copy on another reporting machine."""
    lags = spec().get('version_lag') or {}
    out = {}
    for tool, allowed in lags.items():
        have = {m['machine']: version_tuple((m.get('versions') or {}).get(tool)) for m in machines if m['online']}
        have = {k: v for k, v in have.items() if v}
        if len(have) < 2:
            continue
        top = max(have.values())
        for name, v in have.items():
            if v[:2] < top[:2] or top[2] - v[2] > allowed:
                out.setdefault(name, []).append({
                    'key': f'drift:{name}:{tool}', 'since': None, 'name': tool, 'fix': 'update',
                    'text': f"{tool} {'.'.join(map(str, v))} is outdated (newest here {'.'.join(map(str, top))})"})
    return out


def deaf_hooks(con, now):
    """{machine: problem} for machines whose session cards all came from the backstop lately: their hooks are dead."""
    try:
        rows = con.execute('SELECT machine, source, COUNT(*) n FROM endings WHERE created_at > ? GROUP BY machine, source',
                           (now - HOOK_WINDOW,)).fetchall()
    except sqlite3.OperationalError:  # no endings table (a hub without the feed)
        return {}
    seen = {}
    for r in rows:
        seen.setdefault(r['machine'], {})[r['source']] = r['n']
    return {m: {'key': f'drift:{m}:hooks', 'since': None, 'name': 'session hooks',
                'text': f"its sessions post no cards: {c.get('backstop', 0)} came only from the nightly backstop (old tasks CLI or hooks?)"}
            for m, c in seen.items() if m and not c.get('hook') and c.get('backstop', 0) >= HOOK_MIN_MISSED}
