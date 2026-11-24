"""The game build log at http://beacon/buildlog: every game's sessions as a day-by-day story.

2026-11-22: "it should be transparent what we did … sessions are disposable … the list of it so I
can navigate". Nothing here is copied: each piece is read where it already lives.

  - sessions, time, cost, prompts: this hub's own store (every machine's collector);
  - what Claude said (each turn's last reply, the ✅/⚠️ line): ai-corpus on this box (CORPUS_DB, read-only);
  - screenshots and each game repo's commits: buildlog-shots on the home server (BUILDLOG_URL), fed by
    game-buildlog capture/shots.py on the machines that ran the sessions.

Games are the projects in settings `projects` whose `what` says game.

  GET /api/buildlog                      {"games": [{key, name, what, sessions, active_min, cost, days, first, last, cover}]}
  GET /api/buildlog/game?project=<key>   {"game": {...}, "days": [{date, active_min, cost, sessions: [...], more, changes}]}
  GET /api/buildlog/session?session=<id>[&machine=]  the full record: turns (prompt, cost, reply, steps, shots), links
  GET /buildlog/img/<path>?w=360         a screenshot, passed through from the home server
"""
import datetime
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

import settings
from apps import NET_ERRORS

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS_DB = os.environ.get('CORPUS_DB', os.path.join(settings.p('corpus'), 'corpus.db'))
BUILDLOG_URL = os.environ.get('BUILDLOG_URL', settings.APPS['buildlog'])
SPAN = 400          # days back the store can range over in one go
SHOWN = 6           # sessions shown per day; the rest fold into "+N more"
SMALL_MIN = 3       # a session under this many active minutes with no prompt is plumbing, not work
RESULT = re.compile(r'^\s*(✅|⚠️|❓|❌|🟢|🔴)')
ARTIFACT = re.compile(r'https://claude\.ai/(?:code/)?artifact/[\w-]+')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def load_games():
    data = settings.CONFIG['projects']
    return {k: {'key': k, 'name': v.get('name') or k, 'what': v.get('what'), 'stage': v.get('stage')}
            for k, v in data.items() if isinstance(v, dict) and 'game' in (v.get('what') or '').lower()}


def uuid_of(session_id):
    m = UUID.search(session_id or '')
    return m.group(0) if m else None


def first_line(text, cap=220):
    for line in (text or '').splitlines():
        line = line.strip().strip('*').strip()
        if line:
            return line[:cap]
    return ''


class BuildLog:
    def __init__(self, store, titles=None):
        self.store = store
        self.titles = titles
        self.games = load_games()
        self._shots = {}  # uuid -> (time, {n, pick})
        self._lock = threading.Lock()
        self._shots_down_until = 0
        self._per_game = (0, None)  # (time, result): 400 days of aggregates, rebuilt at most every 60 s

    # ------------------------------------------------------------ sources

    def shots_api(self, path, timeout=4):
        if time.time() < self._shots_down_until:  # it just failed: don't make every load wait out the timeout again
            return None
        try:
            with urllib.request.urlopen(BUILDLOG_URL + path, timeout=timeout) as r:
                return json.load(r)
        except NET_ERRORS:
            self._shots_down_until = time.time() + 60
            return None  # the home server asleep or the service down: the timeline still shows, without pictures

    def shots(self, uuids):
        """{id: {n, pick}} for these sessions (an id may be uuid~from~to, a window), 2 minutes cached; the home server down = {}."""
        now = time.time()
        with self._lock:
            fresh = {u: v for u, (t, v) in ((u, self._shots.get(u, (0, None))) for u in uuids) if now - t < 120}
        need = [u for u in uuids if u not in fresh]
        for i in range(0, len(need), 400):
            part = need[i:i + 400]
            got = self.shots_api('/shots?ids=' + ','.join(part))
            if got is None:
                break
            with self._lock:
                for u in part:
                    self._shots[u] = (now, got.get(u))
                    fresh[u] = got.get(u)
        return {u: v for u, v in fresh.items() if v}

    def corpus(self):
        if not os.path.exists(CORPUS_DB):
            return None
        con = sqlite3.connect(f'file:{CORPUS_DB}?mode=ro', uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        return con

    def results(self, keys):
        """{(uuid, date): first line of the day's last result reply} from ai-corpus."""
        out = {}
        con = self.corpus()
        if con is None:
            return out
        try:
            for u, date in keys:
                s, e = self.store.day_bounds(date)
                lo = datetime.datetime.fromtimestamp(s, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
                hi = datetime.datetime.fromtimestamp(e, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
                rows = con.execute("SELECT text FROM items WHERE thread_id = ? AND kind = 'session' AND sender = 'assistant' "
                                   "AND ts >= ? AND ts < ? ORDER BY ts DESC LIMIT 12", (u, lo, hi)).fetchall()
                pick = next((r['text'] for r in rows if RESULT.match(r['text'] or '')), None)
                pick = pick or next((r['text'] for r in rows if len(first_line(r['text'])) > 40), None)
                if pick:
                    out[(u, date)] = first_line(pick)
        finally:
            con.close()
        return out

    # ------------------------------------------------------------ views

    def day_list(self, days=SPAN):
        today = datetime.date.fromisoformat(self.store.today())
        dates = [(today - datetime.timedelta(days=i)).isoformat() for i in range(days)]
        return dates, self.store.day_aggs(dates)

    def per_game(self):
        """{game: {date: [session finalized for that day]}} over the last SPAN days (cached 60 s; callers copy before editing)."""
        import copy
        t, cached = self._per_game
        if cached is not None and time.time() - t < 60:
            return copy.deepcopy(cached)
        out = self._build_per_game()
        self._per_game = (time.time(), out)
        return copy.deepcopy(out)

    def _build_per_game(self):
        from store import finalize_session
        dates, aggs = self.day_list()
        out = {g: {} for g in self.games}
        for d in dates:
            for s in aggs[d]['sessions'].values():
                if s.get('project') in out:
                    out[s['project']].setdefault(d, []).append(finalize_session(s, light=True))
        return out

    def view(self):
        games = []
        per = self.per_game()
        lasts = {}
        for g, days in per.items():
            ss = [s for v in days.values() for s in v]
            if not ss:
                continue
            last = max(ss, key=lambda s: s['end'])
            lasts[g] = uuid_of(last['id'])
            games.append({**self.games[g], 'sessions': len({s['key'] for s in ss}), 'days': len(days),
                          'active_min': sum(s['active_min'] for s in ss), 'cost': round(sum(s['cost'] for s in ss), 2),
                          'first': min(days), 'last': max(days), 'last_ts': last['end']})
        shots = self.shots([u for u in lasts.values() if u])
        covers = self.covers(per, shots)
        for x in games:
            x['cover'] = covers.get(x['key'])
        games.sort(key=lambda x: -x['last_ts'])
        return {'games': games}

    def covers(self, per, shots):
        """Each game's newest screenshot: from its latest sessions that have one."""
        out = {}
        recent = {g: [uuid_of(s['id']) for d in sorted(days, reverse=True)[:7] for s in days[d]] for g, days in per.items()}
        got = self.shots(sorted({u for ids in recent.values() for u in ids if u}))
        for g, ids in recent.items():
            for u in ids:
                if got.get(u):
                    out[g] = got[u]['pick'][-1]
                    break
        return out

    def game(self, project):
        if project not in self.games:
            raise ValueError('not a game')
        days = self.per_game()[project]
        if self.titles:
            for v in days.values():
                self.titles.annotate(v)
        uuids = sorted({uuid_of(s['id']) for v in days.values() for s in v} - {None})
        shots = self.shots(uuids)
        first = min(days) if days else self.store.today()
        changes = (self.shots_api(f'/changes?repo={urllib.parse.quote(project)}&since={first}', timeout=8) or {}).get('days', {})
        keys = []
        out_days = []
        for d in sorted(set(days) | set(changes), reverse=True):
            ss = sorted(days.get(d, []), key=lambda s: (-(s['prompts'] > 0), -s['active_min']))
            real = [s for s in ss if s['prompts'] > 0 or s['active_min'] >= SMALL_MIN]
            shown, rest = real[:SHOWN], ss[len(real[:SHOWN]):]
            lo, hi = (datetime.datetime.fromtimestamp(x, datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')
                      for x in self.store.day_bounds(d))
            for s in shown:
                u = uuid_of(s['id'])
                s['shots_key'] = f'{u}~{lo}~{hi}' if u else None
                if u:
                    keys.append((u, d))
            out_days.append({'date': d, 'active_min': sum(s['active_min'] for s in ss),
                             'cost': round(sum(s['cost'] for s in ss), 2), 'sessions': shown,
                             'more': {'n': len(rest), 'active_min': sum(s['active_min'] for s in rest),
                                      'cost': round(sum(s['cost'] for s in rest), 2)},
                             'changes': changes.get(d, [])})
        did = self.results(keys)
        windows = self.shots([s['shots_key'] for day in out_days for s in day['sessions'] if s['shots_key']])
        for day in out_days:
            for s in day['sessions']:
                s['did'] = did.get((uuid_of(s['id']), day['date']))
                s['shots'] = windows.get(s.pop('shots_key'))
        ss = [s for v in days.values() for s in v]
        game = {**self.games[project], 'sessions': len({s['key'] for s in ss}), 'days': len(days),
                'active_min': sum(s['active_min'] for s in ss), 'cost': round(sum(s['cost'] for s in ss), 2),
                'commits': sum(len(v) for v in changes.values()),
                'shots': sum((v or {}).get('n', 0) for v in shots.values())}
        return {'game': game, 'days': out_days}

    def session(self, machine, session):
        s = self.store.session(machine, session)
        if not s or s.get('project') not in self.games:  # the build log shows game sessions, nothing else
            return None
        if self.titles:
            self.titles.annotate([s])
        u = uuid_of(session)
        shots = (self.shots_api(f'/shots/{u}') or {}).get('items', []) if u else []
        msgs = []
        con = self.corpus() if u else None
        if con is not None:
            try:
                msgs = [dict(r) for r in con.execute(
                    "SELECT ts, sender, text FROM items WHERE thread_id = ? AND kind = 'session' ORDER BY ts", (u,))]
            finally:
                con.close()
        for m in msgs:
            m['t'] = datetime.datetime.fromisoformat(m['ts'].replace('Z', '+00:00')).timestamp()
        for x in shots:
            x['t'] = datetime.datetime.fromisoformat(x['ts'].replace('Z', '+00:00')).timestamp()
        turns = [t for t in s.pop('turns', []) if t.get('text') or t.get('calls')]
        edges = [t['ts'] for t in turns[1:]] + [float('inf')]
        links = []
        out = []
        for t, end in zip(turns, edges):
            replies = [m for m in msgs if m['sender'] == 'assistant' and t['ts'] - 2 <= m['t'] < end - 2]
            pics = [x['path'] for x in shots if t['ts'] - 2 <= x['t'] < end - 2]
            final = next((m for m in reversed(replies) if RESULT.match(m['text'] or '')), replies[-1] if replies else None)
            for m in replies:
                links += [l for l in ARTIFACT.findall(m['text'] or '') if l not in links]
            out.append({'ts': t['ts'], 'last': t['last'], 'prompt': (t['text'] or '')[:4000], 'cost': t['cost'],
                        'calls': t['calls'], 'tools': t['tools'],
                        'reply': (final['text'] or '')[:6000] if final else None,
                        'steps': [first_line(m['text'], 300) for m in replies if m is not final][-40:],
                        'nsteps': max(0, len(replies) - 1), 'shots': pics})
        return {'session': s, 'turns': out, 'shots': len(shots), 'artifacts': links,
                'game': self.games.get(s.get('project'))}

    def image(self, handler, path, query=''):
        from apps import proxy
        return proxy(handler, BUILDLOG_URL, '/img/' + path + ('?' + query if query else ''))
