"""The studio view behind http://beacon/team: what each PROJECT's AI team is doing, in words a friend who knows
nothing about software gets at once (2026-11-22: "the highest abstraction … even for my friends, YouTubers";
"working on the next update", "fixing a bug a player told me on Discord", "studying the stats to earn more").

No agents, tasks, sessions or machines here: every live session is folded into its project (its task's project,
else its folder, else Haiku picks one of the known projects in the settings' `projects`). Each of the team's own
tools (the folders of `projects.hq`) is its own card of kind "hq", so a day spent on tools still shows what moves.
Every SAMPLE_SEC the studio regroups /api/live; a project whose jobs changed gets a fresh headline and kind from
Haiku, at most every MIN_GAP_SEC, all changed projects in one call, at most DAILY_CALLS calls a day. Until a caption
comes (a restart, an empty budget) a project shows its newest task title, so a busy project never vanishes.
GET /api/team returns {"now", "projects": [{key, name, what, headline, aim, kind, working, waiting, helpers, busy,
stage, since}], "resting": [...]}.
"""
import os
import re
import threading
import time
import traceback

from settings import CONFIG, PROJECT_NAMES
from titles import ask_haiku

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_SEC = 30
MIN_GAP_SEC = 300
ACTIVE_SEC = 6 * 3600       # a project whose helpers waited on him longer than this is resting
DAILY_CALLS = int(os.environ.get('STUDIO_DAILY_CALLS', '240'))
KINDS = ('update', 'fix', 'players', 'numbers', 'money', 'content', 'customers', 'trends', 'hq')
LOOSE = re.compile(r'^\(|^(scratch|r|tmp|review|grade|\d+)$')  # folders that say nothing about the project

PROMPT = '''You write the captions for a big screen in a small studio, where AI helpers work on the owner's projects.
The people reading it are his friends, YouTubers who know nothing about software. For each project below you get what
its helpers are on right now (the job, why, and their last words). Write:
- "headline": what the project's team is doing right now, 4-9 words, the big picture a friend gets at once, like
  "Getting the winter update ready", "Fixing a bug a player found on Discord", "Studying the numbers to earn more",
  "Hunting viral TikTok trends", "Making 10 TikToks to bring new players", "Pricing the Excel coaching",
  "Making the team's own tools faster". Never use words like code, page, screen, branch, deploy, session, agent, task,
  hub, server, script, API, tailnet, dashboard, PR. Say what it is FOR (players, money, fans, customers), not how.
- "aim": what this work should change for the project, 2-5 words, a result a friend cares about, like "More players",
  "The first sale", "Fewer crashes", "Players come back", "A bigger audience", "A paying client", "A faster team".
- "kind": one of {kinds} (update = making the next version, fix = repairing something broken, players = getting more
  players or users, numbers = learning from stats, money = prices, offers, earning more, content = videos and posts,
  customers = finding clients, trends = researching what works elsewhere, hq = improving the team's own tools).
If the words are Czech, still answer in English. Reply with JSON only: {{"1": {{"headline": "...", "aim": "...", "kind": "..."}}, ...}}

{items}'''

CLASSIFY = '''Which project is each piece of work for? Projects: {projects}. Answer "other" if none fits.
Reply with JSON only: {{"1": "project-key", ...}}

{items}'''


def load_projects():
    return {k: v for k, v in CONFIG['projects'].items() if not k.startswith('_')}


def goal_of(body):
    m = re.search(r'## Goal\s*\n(.+?)(\n##|\Z)', body or '', re.S)
    return re.sub(r'\s+', ' ', m.group(1) if m else (body or '')[:200]).strip()[:240]


def tidy(text):
    t = re.sub(r'\s+', ' ', str(text or '')).strip().strip('"\'').rstrip('.').strip()
    return t[:80] if len(t) >= 4 else None


class Studio:
    def __init__(self, store, narrator=None, model=ask_haiku, background=True, progress=None):
        self.store = store
        self.narrator = narrator
        self.progress = progress  # progress.Progress: a running task's own steps and time left
        self.model = model
        self.projects = load_projects()
        self.hq_folders = set(self.projects['hq'].get('folders', []))
        self.lock = threading.Lock()
        self.picked = {}     # session id -> project key Haiku chose for a session in a loose folder
        self.said = {}       # project -> {'headline', 'kind', 'sig', 'at'}
        self.state = {'now': time.time(), 'projects': [], 'resting': []}
        self.calls = []
        if background:
            threading.Thread(target=self.loop, daemon=True).start()

    # ------------------------------------------------------------ grouping

    def tasks_of(self, ids):
        if not ids:
            return {}
        try:
            with self.store.connect() as con:
                rows = con.execute('SELECT l.session_id, t.id, t.project, t.title, t.body, t.status, t.step FROM task_sessions l '
                                   f'JOIN tasks t ON t.id = l.task_id WHERE l.session_id IN ({",".join("?" * len(ids))}) '
                                   'ORDER BY l.linked_at', ids).fetchall()
        except Exception:  # no tasks tables
            return {}
        return {r['session_id']: {'id': r['id'], 'project': r['project'], 'title': r['title'], 'body': r['body'],
                                  'status': r['status'], 'step': r['step'], 'goal': goal_of(r['body'])} for r in rows}

    def key_of(self, folder):
        if not folder:
            return None
        if folder in self.projects or folder in self.hq_folders:
            return folder
        return None if LOOSE.search(folder) else folder

    def gather(self, machines):
        """Live sessions worth showing, each with its project key (None: not known yet)."""
        out = []
        for m in machines:
            for x in m.get('sessions') or ():
                if x.get('state') == 'done' or (x.get('state') != 'working' and (x.get('idle_s') or 0) > ACTIVE_SEC):
                    continue
                if (x.get('doing') or '').startswith('✅'):
                    continue  # finished, just left open
                out.append(x)
        tasks = self.tasks_of([x['id'] for x in out])
        rows = []
        for x in out:
            t = tasks.get(x['id']) or {}
            key = self.key_of(t.get('project')) or self.key_of(x.get('project')) or self.picked.get(x['id'])
            rows.append({'id': x['id'], 'key': key, 'task': t.get('title') or '', 'task_row': t if t.get('status') == 'doing' else None, 'working': x.get('state') == 'working' and (x.get('idle_s') or 0) < 600,
                         'job': t.get('title') or x.get('title') or '', 'goal': t.get('goal') or '',
                         'words': (x.get('now') or x.get('doing') or x.get('prompt') or '')[:160], 'last': x.get('last') or 0})
        return rows

    def classify(self, rows):
        loose = [r for r in rows if r['key'] is None and r['id'] not in self.picked][:20]
        if not loose or not self.budget_left():
            return
        self.calls.append(time.time())
        names = ', '.join(f'{k} ({v["name"]}: {v["what"]})' for k, v in self.projects.items())
        items = '\n'.join(f'{i + 1}. {r["job"][:100]}: {r["goal"][:160]} {r["words"][:120]}'.replace('\n', ' ')
                          for i, r in enumerate(loose))
        got = self.model(CLASSIFY.format(projects=names, items=items))
        if not isinstance(got, dict) or not got:
            return  # the call failed: ask again next time, never file them all under "other" for good
        for i, r in enumerate(loose):
            k = got.get(str(i + 1))
            self.picked[r['id']] = k if k in self.projects else 'other'
        for r in rows:
            if r['key'] is None:
                r['key'] = self.picked.get(r['id'])

    # ------------------------------------------------------------ captions

    def budget_left(self):
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 86400]
        return len(self.calls) < DAILY_CALLS

    def caption(self, groups):
        now = time.time()
        due = []
        for key, rs in groups.items():
            sig = '\n'.join(sorted({f'{r["job"]}|{r["working"]}' for r in rs}))  # jobs, not their changing last words: those burnt the day's budget by noon
            old = self.said.get(key)
            if old and (old['sig'] == sig or now - old['at'] < MIN_GAP_SEC):
                continue
            due.append((key, sig, rs))
        if not due or not self.budget_left():
            return
        self.calls.append(now)
        items = []
        for i, (key, _, rs) in enumerate(due):
            p = self.card_of(key)
            lines = [f'   - {"working" if r["working"] else "waiting for the owner"}: job "{r["job"][:100]}"; why: {r["goal"][:200]}; '
                     f'last words: {r["words"][:140]}'.replace('\n', ' ') for r in sorted(rs, key=lambda r: -r['last'])[:6]]
            items.append(f'{i + 1}. project: {p["name"]} ({p["what"]})\n' + '\n'.join(lines))
        got = self.model(PROMPT.format(kinds=', '.join(KINDS), items='\n'.join(items))) or {}
        for i, (key, sig, _) in enumerate(due):
            one = got.get(str(i + 1))
            if isinstance(one, dict) and tidy(one.get('headline')):
                self.said[key] = {'headline': tidy(one['headline']), 'aim': tidy(one.get('aim')), 'kind': one.get('kind') if one.get('kind') in KINDS else 'update',
                                  'sig': sig, 'at': time.time()}

    # ------------------------------------------------------------ the view

    def card_of(self, key):
        if key in self.projects and key != 'hq':
            return self.projects[key]
        name = PROJECT_NAMES.get(key) or key.replace('-', ' ').capitalize()
        return {'name': name, 'what': self.projects['hq']['what'] if key in self.hq_folders else '', 'stage': None}

    def build(self, groups):
        out = []
        for key, rs in groups.items():
            said = self.said.get(key) or {}
            busy = sum(r['working'] for r in rs)
            newest = max(rs, key=lambda r: r['last'])
            # no caption yet: the newest task title (plain words), else a plain line; never a folder or session name
            headline = said.get('headline') or tidy(max((r for r in rs if r['task']), key=lambda r: r['last'], default=newest)['task']) or ('On the next step' if busy else 'Waiting for your next word')
            p = self.card_of(key)
            hq = key == 'hq' or key in self.hq_folders
            out.append({'key': key, 'name': p['name'], 'what': p.get('what', ''), 'headline': headline,
                        'aim': said.get('aim'), 'kind': 'hq' if hq else said.get('kind', 'update'), 'working': bool(busy),
                        'waiting': not busy, 'helpers': len(rs), 'busy': busy, 'stage': p.get('stage'),
                        'since': newest['last'], 'jobs': self.jobs_of(rs)})
        out.sort(key=lambda p: (p['waiting'], p['kind'] == 'hq', -p['since']))
        busy = {p['key'] for p in out}
        resting = [{'key': k, 'name': v['name'], 'what': v['what'], 'stage': v.get('stage')}
                   for k, v in self.projects.items() if k not in busy and k != 'hq']
        return {'now': time.time(), 'projects': out, 'resting': resting,
                'guesses': self.progress.accuracy() if self.progress else None}

    def jobs_of(self, rs):
        """Each running task's own steps and time left, newest first, at most two; [] without written steps."""
        if not self.progress:
            return []
        out, seen = [], set()
        for r in sorted(rs, key=lambda r: -r['last']):
            t = r['task_row']
            if not t or t['id'] in seen:
                continue
            seen.add(t['id'])
            try:
                job = self.progress.of(t)
            except Exception:  # a guess that fails never blanks the card
                traceback.print_exc()
                job = None
            if job:
                out.append(job)
        return out[:2]

    def run_once(self):
        live = self.store.live_now()
        if self.narrator:
            self.narrator.annotate(live)  # each working session's plain sentence, the best words we have
        rows = self.gather(live['machines'])
        self.classify(rows)
        groups = {}
        for r in rows:
            if r['key'] and r['key'] != 'other':
                groups.setdefault(r['key'], []).append(r)
        self.caption(groups)
        view = self.build(groups)
        with self.lock:
            self.state = view
        return view

    def view(self):
        with self.lock:
            return self.state

    def loop(self):
        while True:
            try:
                self.run_once()
            except Exception:
                traceback.print_exc()
            time.sleep(SAMPLE_SEC)
