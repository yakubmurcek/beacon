"""AI jobs (http://beacon/ai): every job that runs a language model by itself, what it runs on and what it costs.

A job is anything that starts a model run nobody typed: a systemd timer or daemon, a launchd agent, a cron
line, a scheduled task, a transient unit (answer-run-*), a /loop. Two sources meet here:

- What the boxes say (collector/host.py + collector/llm.py): each job's scripts are read for model calls
  (`claude -p`, `opencode run`, Hermes, a provider's API) and the model and effort they ask for; and every
  10 s, which job started each running agent process (systemd unit from its cgroup, launchd label).
  That `session -> job` pair is kept in `job_runs`.
- What the runs did (the events table): every call of a session nobody typed, with its model, effort and
  tokens, priced at read time like the rest of the hub.

A session is put on a job by its `job_runs` link; one never seen running (before this page existed, or too
short for a 10-second look) by the start of its first prompt matching a linked run of the same job on the
same machine. Unattended sessions that match no job group by machine and that prompt start ("Other runs").
Direct API callers (no log of their own) show the model their script names and "not measured", never a guess.
"""
import bisect
import datetime
import json
import re
import time
import traceback

from settings import OWNER
from store import call_cost
from swr import SWR

LAUNCHD_PREFIX = OWNER['launchd_prefix']  # his own launchd agents' label prefix

DAYS = 7
CACHE_S = 60
SCHEDULED = '[scheduled] '
COMMANDISH = re.compile(r'\.exe\b|^\S*[\\/]\S*\s|--\w')  # a command line standing in for a description

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
  machine TEXT NOT NULL,
  run TEXT NOT NULL,          -- the agent session, or pid:<pid>:<start> for a run that keeps no log
  job TEXT NOT NULL,          -- the job id as the box lists it (systemd:czech-harvest)
  agent TEXT,
  session TEXT,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  model TEXT,                 -- from its command line (--model), for a run that keeps no log
  effort TEXT,
  PRIMARY KEY (machine, run)
);
CREATE INDEX IF NOT EXISTS job_runs_seen ON job_runs(last_seen);
"""

# A transient unit's per-run suffix: answer-run-180-1795358745 is one run of answer-run.
RUN_SUFFIX = re.compile(r'(?:-\d+)+$')
KIND_WORD = {'timer': 'timer', 'daemon': 'always on', 'service': 'service', 'launchd': 'Mac agent',
             'cron': 'cron', 'schtask': 'scheduled task', 'transient': 'one-off'}


def group_of(job_id):
    """The job a run belongs to: its id without a transient unit's per-run numbers."""
    kind, _, name = (job_id or '').partition(':')
    return f'{kind}:{RUN_SUFFIX.sub("", name) or name}' if name else job_id


def model_name(m):
    """A model id as people say it: claude-haiku-4-5-20251001 -> Haiku 4.5, sonnet -> Sonnet."""
    if not m:
        return None
    m = re.sub(r'^(?:anthropic|openrouter|deepseek|minimax)/', '', m.strip())
    m = re.sub(r'-\d{8}$', '', re.sub(r'^claude-', '', m))
    m = re.sub(r'-(\d+)-(\d+)$', r' \1.\2', m)
    m = re.sub(r'^deepseek-', 'DeepSeek ', m, flags=re.I)
    m = re.sub(r'^gpt-', 'GPT-', m)
    m = m.replace('-', ' ') if ' ' in m or not re.search(r'\d', m) else m
    return m[:1].upper() + m[1:]


# Prompts agents send each other, named for what they are rather than by their first words.
KNOWN_PROMPTS = (
    (re.compile(r'^first run `?tasks doing', re.I), 'Agent sessions working a task'),
)


def asked_models(models):
    """The models a script names, in words, an alias dropped when the same family is named in full."""
    names = [model_name(m) for m in models or [] if m]
    full = {n.split()[0].lower() for n in names if ' ' in n}
    out = []
    for n in names:
        if (' ' in n or n.lower() not in full) and n not in out:
            out.append(n)
    return out


def prompt_key(text):
    """The stable start of a scheduled prompt: numbers, dates, temp paths and ids taken out."""
    t = (text or '')
    t = t[len(SCHEDULED):] if t.startswith(SCHEDULED) else t
    for rx, name in KNOWN_PROMPTS:
        if rx.search(t):
            return name.lower()
    t = re.sub(r'/tmp/\S+|https?://\S+|\b[0-9a-f]{8}-[0-9a-f-]{27}\b', ' ', t)
    t = re.sub(r'[#?]?\d[\d.:-]*', ' ', t.lower())
    words = re.findall(r'[\w\']+', t)
    return ' '.join(words[:5]) or None


def prompt_label(text):
    """A scheduled prompt's first sentence, for a group no job name covers."""
    t = (text or '')
    t = t[len(SCHEDULED):] if t.startswith(SCHEDULED) else t
    for rx, name in KNOWN_PROMPTS:
        if rx.search(t):
            return name
    t = re.sub(r'`[^`]*`|https?://\S+', '', t)
    t = re.sub(r'/tmp/\S+\s*', '', t)
    t = re.sub(r'\s*(?:for |on )?\d{4}-\d{2}-\d{2}\b|\s*\((?:#|\?|task )\d+[^)]*\)|\s*[#?]\d+\b', '', t)
    t = ' '.join(t.split())
    first = re.split(r'(?<=[.!?:])\s', t, maxsplit=1)[0]
    return (first[:90] + '…') if len(first) > 90 else first


class AIJobs:
    def __init__(self, store, titles=None):
        self.store = store
        self.titles = titles
        self._cache = SWR(limit=2)
        self.extra = []  # the hub's own model jobs that no collector sees (hub/talk.py): f(days) -> item or None
        with store.connect() as con:
            con.executescript(SCHEMA)
            have = [r[1] for r in con.execute('PRAGMA table_info(job_runs)')]
            for col in ('model', 'effort'):
                if col not in have:
                    con.execute(f'ALTER TABLE job_runs ADD COLUMN {col} TEXT')

    # ------------------------------------------------------------ ingest

    def save(self, machine, host):
        """A collector's `llm_runs`: which job started each agent process running right now."""
        runs = host.get('llm_runs') if isinstance(host, dict) else None
        if not isinstance(runs, list) or not runs:
            return
        now = time.time()
        rows = []
        for r in runs[:200]:
            if not isinstance(r, dict) or not isinstance(r.get('job'), str):
                continue
            sess = r.get('session') if isinstance(r.get('session'), str) else None
            run = sess or f"pid:{r.get('pid')}:{int(r.get('started') or 0)}"
            opt = lambda v: str(v)[:60] if isinstance(v, str) and v else None  # noqa: E731
            rows.append((str(machine)[:64], run[:200], r['job'][:200], str(r.get('agent') or '')[:40] or None,
                         sess and sess[:200], now, now, opt(r.get('model')), opt(r.get('effort'))))
        try:
            with self.store.connect() as con:
                con.executemany(
                    'INSERT INTO job_runs(machine, run, job, agent, session, first_seen, last_seen, model, effort) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(machine, run) DO UPDATE SET '
                    'last_seen = excluded.last_seen, job = excluded.job, '
                    'model = coalesce(excluded.model, job_runs.model), effort = coalesce(excluded.effort, job_runs.effort)',
                    rows)
        except Exception as e:  # never fail a push over this
            print(f'aijobs: {machine}: runs not stored: {type(e).__name__}: {e}', flush=True)

    # ------------------------------------------------------------ the view

    def view(self, now=None):
        if now is not None:  # tests ask for a fixed moment: build it fresh
            return self.build(now)
        # A build takes seconds: the last one is served at once and rebuilt behind it (hub/swr.py).
        return self._cache.get('view', self.build, fresh=CACHE_S, stale=6 * 3600)

    def build(self, now=None):
        now = now or time.time()
        # The window is the 7 calendar days the bars show (today and the 6 before), from local midnight.
        today = datetime.datetime.fromtimestamp(now, self.store.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        start = (today - datetime.timedelta(days=DAYS - 1)).timestamp()
        days = [self.store.local_date(start + 86400 * i + 43200) for i in range(DAYS)]
        day0 = days[0]
        with self.store.connect() as con:
            hosts = {r['machine']: json.loads(r['data']) for r in con.execute('SELECT machine, data FROM hosts')}
            links = con.execute('SELECT machine, run, job, agent, session, first_seen, last_seen, model, effort FROM job_runs '
                                'WHERE last_seen >= ?', (start,)).fetchall()
            prompts = con.execute("SELECT machine, session, text, ts FROM events WHERE kind = 'prompt' AND ts >= ? "
                                  'ORDER BY ts', (start - 86400,)).fetchall()
            calls = con.execute("SELECT machine, session, agent, model, speed, effort, ts, input, output, cache_read, "
                                'cache_write_5m, cache_write_1h, cost_reported FROM events '
                                "WHERE kind = 'call' AND ts >= ?", (start,)).fetchall()

        # Every job the boxes list, for its name, schedule and what its scripts ask for.
        listed = {}
        for machine, data in hosts.items():
            for j in data.get('jobs') or []:
                if isinstance(j, dict) and j.get('id'):
                    listed[(machine, j['id'])] = j

        # Sessions: who typed what. A session with any typed prompt is his, not a job's.
        first, typed, timeline = {}, set(), {}
        for r in prompts:
            k = (r['machine'], r['session'])
            if not (r['text'] or '').startswith(SCHEDULED):
                typed.add(k)
            first.setdefault(k, (r['text'], r['ts']))
            t = timeline.setdefault(k, ([], []))
            t[0].append(r['ts'])
            t[1].append(r['text'] or '')

        linked = {}   # (machine, session) -> group id
        bare = {}     # (machine, group) -> [(seen, model, effort)]: runs that keep no log
        linked_runs = {}
        for r in links:
            g = group_of(r['job'])
            if r['session']:
                linked[(r['machine'], r['session'])] = g
                linked_runs[(r['machine'], r['session'])] = (g, r['first_seen'], r['model'], r['effort'])
            else:
                bare.setdefault((r['machine'], g), []).append((r['first_seen'], r['model'], r['effort']))
        # A session a job's own command started with a prompt (Windows has no process-to-task link): its first
        # prompt starts as that command's prompt does, on that machine, and only one job starts that way.
        launch, launched = {}, {}
        for (machine, jid), j in listed.items():
            for p in (j.get('ai') or {}).get('launches') or []:
                launch.setdefault((machine, p), set()).add(group_of(jid))
        for k, (text, _) in first.items():
            words = prompt_key(text).split() if text and k not in linked else []
            jobs = launch.get((k[0], ' '.join(words[:3]))) if len(words) >= 3 else None
            if jobs and len(jobs) == 1:
                launched[k] = next(iter(jobs))
        # A run never seen live: the same prompt start as a linked run of a job on that machine.
        by_prompt = {}
        for (m, s), g in linked.items():
            key = prompt_key((first.get((m, s)) or (None,))[0])
            if key:
                by_prompt.setdefault((m, key), g)

        groups = {}

        def group(gid, machine, label=None):
            g = groups.get((machine, gid))
            if g is None:
                g = groups[(machine, gid)] = {
                    'id': f'{machine}/{gid}', 'job': gid, 'machine': machine, 'label': label, 'sessions': set(),
                    'runs_bare': 0, 'calls': 0, 'cost': 0.0, 'unpriced': 0, 'tokens': 0,
                    'models': {}, 'efforts': {}, 'agents': {}, 'days': {d: 0.0 for d in days}, 'last': None,
                    'sample': None, 'starts': {}, 'bare_models': {}, 'bare_efforts': {}}
            return g

        total_cost, total_calls, all_cost = 0.0, 0, 0.0
        with_calls = set()
        for r in calls:
            cost, _, priced = call_cost(dict(r))
            all_cost += cost
            k = (r['machine'], r['session'])
            run = r['session']
            gid = linked.get(k)
            if gid is None and k in launched:
                # A session a task started: its turns until he types into it are the job's, the rest are his.
                ts, texts = timeline.get(k, ([], []))
                i = bisect.bisect_right(ts, r['ts']) - 1
                if i > 0 and not texts[i].startswith(SCHEDULED):
                    continue
                gid = launched[k]
                g = group(gid, r['machine'])
            elif gid is None and k in typed:
                # His own session; only a turn a /loop or cron fired in it (its last prompt was scheduled) is a job's.
                ts, texts = timeline.get(k, ([], []))
                i = bisect.bisect_right(ts, r['ts']) - 1
                if i < 0 or not texts[i].startswith(SCHEDULED):
                    continue
                lkey = prompt_key(texts[i])
                g = group('loop:' + (lkey or 'loop'), r['machine'], 'Loop: ' + (prompt_label(texts[i]) or 'scheduled turns'))
                run = f"{r['session']}@{ts[i]}"
            elif gid is None:
                text = (first.get(k) or (None,))[0]
                pkey = prompt_key(text)
                gid = by_prompt.get((r['machine'], pkey)) if pkey else None
                if gid is None:
                    gid = 'prompt:' + (pkey or r['agent'] or 'unknown')
                    g = group(gid, r['machine'], prompt_label(text) if text else None)
                else:
                    g = group(gid, r['machine'])
            else:
                g = group(gid, r['machine'])
            with_calls.add(k)
            if run not in g['sessions']:
                g['starts'][run] = (first.get(k) or (None, r['ts']))[1]
            g['starts'][run] = min(g['starts'].get(run) or r['ts'], r['ts'])
            g['sessions'].add(run)
            g['sample'] = g['sample'] or r['session']
            g['calls'] += 1
            g['cost'] += cost
            g['unpriced'] += 0 if priced else 1
            tok = sum(r[f] or 0 for f in ('input', 'output', 'cache_read', 'cache_write_5m', 'cache_write_1h'))
            g['tokens'] += tok
            name = model_name(r['model']) or 'unknown'
            mm = g['models'].setdefault(name, {'calls': 0, 'cost': 0.0})
            mm['calls'] += 1
            mm['cost'] += cost
            if r['effort']:
                g['efforts'][r['effort']] = g['efforts'].get(r['effort'], 0) + 1
            g['agents'][r['agent']] = g['agents'].get(r['agent'], 0) + 1
            day = self.store.local_date(r['ts'])
            if day in g['days']:
                g['days'][day] += cost
            g['last'] = max(g['last'] or 0, r['ts'])
            total_cost += cost
            total_calls += 1
        # A linked run that logged no calls (`claude -p --no-session-persistence`: the hub's own titles and Labs,
        # login nudges) still ran: count it, with the model its command line asked for.
        for k, (gid, seen, model, effort) in linked_runs.items():
            if k not in with_calls:
                bare.setdefault((k[0], gid), []).append((seen, model, effort))
        for (machine, gid), runs in bare.items():
            g = group(gid, machine)
            g['runs_bare'] += len(runs)
            g['last'] = max([g['last'] or 0] + [x[0] for x in runs])
            for _, model, effort in runs:
                if model:
                    n = model_name(model)
                    g['bare_models'][n] = g['bare_models'].get(n, 0) + 1
                if effort:
                    g['bare_efforts'][effort] = g['bare_efforts'].get(effort, 0) + 1

        self.merge_unlinked(groups, listed)

        # Jobs whose scripts call a model but ran nothing this week still belong on the page.
        for (machine, jid), j in listed.items():
            if j.get('ai') and (machine, group_of(jid)) not in groups:
                group(group_of(jid), machine)

        out = []
        for (machine, gid), g in groups.items():
            j = listed.get((machine, gid)) or self.job_like(listed, machine, gid)
            ai = (j or {}).get('ai') or {}
            runs = len(g['sessions']) + g['runs_bare']
            measured = g['calls'] > 0
            tools = ai.get('tools') or []
            providers = ai.get('providers') or []
            item = {
                'id': g['id'], 'machine': machine,
                'name': self.name_of(gid, j, g),
                'what': (j or {}).get('desc') if j and j.get('desc') and j.get('kind') not in ('launchd', 'cron')
                and not COMMANDISH.search(j['desc']) else None,
                'kind': ('loop in a session' if gid.startswith('loop:') else 'no job linked yet' if gid.startswith('prompt:')
                         else KIND_WORD.get((j or {}).get('kind') or ('transient' if gid.startswith('systemd:') else ''))),
                'schedule': (j or {}).get('desc') if j and j.get('kind') in ('launchd', 'cron') else None,
                'next_run': (j or {}).get('next_run'), 'state': (j or {}).get('state'),
                'job_last_run': (j or {}).get('last_run'),
                'linked': not gid.startswith(('prompt:', 'loop:')),
                'tools': sorted(g['agents'], key=g['agents'].get, reverse=True) or tools,
                'providers': providers,
                'asks': {'models': asked_models(ai.get('models')), 'efforts': ai.get('efforts') or []},
                'runs': runs, 'runs_logged': len(g['sessions']), 'calls': g['calls'], 'tokens': g['tokens'],
                'cost': round(g['cost'], 4), 'per_run': round(g['cost'] / len(g['sessions']), 4) if g['sessions'] else None,
                'priced': g['unpriced'] == 0 and measured,
                'floor': measured and g['unpriced'] / g['calls'] >= 0.1,
                'share': round(g['cost'] / total_cost, 4) if total_cost else 0,
                'models': sorted(({'name': k, 'calls': v['calls'], 'cost': round(v['cost'], 4)}
                                  for k, v in g['models'].items()), key=lambda x: (-x['cost'], -x['calls']))
                          + [{'name': k, 'calls': 0, 'cost': 0, 'nolog': v} for k, v in g['bare_models'].items()
                             if k not in g['models']],
                'efforts': sorted(({'name': k, 'calls': v} for k, v in g['efforts'].items()), key=lambda x: -x['calls'])
                           + [{'name': k, 'calls': 0, 'nolog': v} for k, v in g['bare_efforts'].items() if k not in g['efforts']],
                'days': [round(g['days'][d], 4) for d in days],
                'last': g['last'],
                'measured': measured,
                'unmeasurable': not measured and bool(providers or set(tools) - {'Claude Code', 'OpenCode', 'Codex'}),
                'sample': g['sample'],
            }
            out.append(item)
        self.name_unnamed(out)
        for f in self.extra:
            try:
                item = f(days)
            except Exception:
                traceback.print_exc()
                item = None
            if item:
                out.append(item)
                total_cost += item['cost']
                total_calls += item['calls']
        if self.extra and total_cost:
            for item in out:
                item['share'] = round(item['cost'] / total_cost, 4)
        out.sort(key=lambda x: (-x['cost'], -x['runs'], x['name'].lower()))
        return {'now': now, 'days': days, 'from': day0, 'total': {
            'cost': round(total_cost, 2), 'calls': total_calls, 'all_cost': round(all_cost, 2),
            'share_of_all': round(total_cost / all_cost, 4) if all_cost else None,
            'jobs': len(out), 'measured': sum(1 for x in out if x['measured'])}, 'jobs': out}

    @staticmethod
    def absorb(groups, key, machine, gid):
        """Moves prompt group `key` onto job `gid` (machine-local), adding it up if the job has runs already."""
        src = groups.pop(key)
        dst = groups.get((machine, gid))
        if dst is None:
            src['job'], src['id'], src['label'] = gid, f'{machine}/{gid}', None
            groups[(machine, gid)] = src
            return
        for f in ('runs_bare', 'calls', 'cost', 'unpriced', 'tokens'):
            dst[f] += src[f]
        dst['sessions'] |= src['sessions']
        dst['starts'].update(src['starts'])
        for name, v in src['models'].items():
            mm = dst['models'].setdefault(name, {'calls': 0, 'cost': 0.0})
            mm['calls'] += v['calls']
            mm['cost'] += v['cost']
        for f in ('efforts', 'agents'):
            for k2, v in src[f].items():
                dst[f][k2] = dst[f].get(k2, 0) + v
        for d, v in src['days'].items():
            dst['days'][d] = dst['days'].get(d, 0) + v
        dst['last'] = max(dst['last'] or 0, src['last'] or 0) or None
        dst['sample'] = dst['sample'] or src['sample']

    def merge_unlinked(self, groups, listed):
        """Runs from before the page linked them, put on their job:
        1. the prompt's first three words are how a prompt in that job's scripts starts (one job only);
        2. else the session began within 3 minutes after the job's last start, on a model family it asks for."""
        starts = {}
        for (machine, jid), j in listed.items():
            for p in (j.get('ai') or {}).get('prompts') or []:
                starts.setdefault((machine, p), set()).add(group_of(jid))
        for key in [k for k in groups if k[1].startswith('prompt:')]:
            words = key[1][len('prompt:'):].split()
            jobs = starts.get((key[0], ' '.join(words[:3]))) if len(words) >= 3 else None
            if jobs and len(jobs) == 1:
                self.absorb(groups, key, key[0], next(iter(jobs)))
        for (machine, jid), j in listed.items():
            t0, ai = j.get('last_start'), j.get('ai')
            if not ai or not t0 or j.get('kind') != 'timer':
                continue  # a daemon's start says nothing about when it calls a model
            families = {m.split()[0].lower() for m in (model_name(x) for x in ai.get('models') or []) if m}
            if not families:
                continue  # a job that names no model: a run starting near it is only a coincidence
            best = None
            for key, g in groups.items():
                if key[0] != machine or not key[1].startswith('prompt:'):
                    continue
                top = max(g['models'].items(), key=lambda kv: kv[1]['cost'], default=(None, None))[0]
                if not top or top.split()[0].lower() not in families:
                    continue  # it ran mostly on a model this job never asks for
                d = min((s - t0 for s in g['starts'].values() if s and 0 <= s - t0 <= 180), default=None)
                if d is not None and (best is None or d < best[0]):
                    best = (d, key)
            if best:
                self.absorb(groups, best[1], machine, group_of(jid))

    @staticmethod
    def job_like(listed, machine, gid):
        """A transient run's own unit is gone; any listed job of that family (answer-run-…) names it."""
        for (m, jid), j in listed.items():
            if m == machine and group_of(jid) == gid:
                return j
        return None

    @staticmethod
    def name_of(gid, job, g):
        if gid.startswith('loop:'):
            return g['label'] or 'Loop in a session'
        if gid.startswith('prompt:'):
            return g['label'] or 'Sessions another agent started'
        name = gid.split(':', 1)[1].rsplit('\\', 1)[-1]  # a Windows task sits under a folder path (\\Name)
        name = re.sub('^' + re.escape(LAUNCHD_PREFIX), '', name)
        if ' ' in name:  # already words ("Smash the Car session"): keep its capitals
            return name.strip()
        name = name.replace('-', ' ').replace('_', ' ').strip()
        return name[:1].upper() + name[1:]

    def name_unnamed(self, items):
        """'Other runs' groups: the session's own name (custom from --name, else Haiku's) beats a prompt cut."""
        if not self.titles:
            return
        # only a single run: one session's own name would mislabel a group of different runs
        sess = [{'id': x['sample'], 'machine': x['machine'], 'first_prompt': None, 'project': None}
                for x in items if x['sample'] and not x['linked'] and x['runs_logged'] == 1]
        try:
            self.titles.annotate(sess)
        except Exception:
            return
        named = {(s['machine'], s['id']): s.get('title') for s in sess if s.get('title_from') in ('name', 'task')}
        for x in items:
            t = named.get((x['machine'], x['sample']))
            if t and not x['linked'] and x['runs_logged'] == 1:
                x['name'] = t
