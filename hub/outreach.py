"""Outreach (http://beacon/outreach): every lead we researched or scraped, and every research report, in one place.

The data stays where it is; this reads it on request (cached until a source file changes):
  leads     the hand-kept ai-brain/knowledge/reference/outreach/leads.json, plus whatever scraped lists the brain's own
            knowledge/reference/outreach/sources.py reads (each list's format lives there, next to the lists)
  research  ai-brain/knowledge/reference/outreach/artifacts.json (claude.ai artifacts, which the hub cannot list
            itself) + research-files.json + every run folder in knowledge/reference/research/ and market-research/,
            found live, so a new research run shows up by itself

  OUTREACH_BRAIN     ai-brain checkout (default: settings `paths.brain`, a checkout kept synced)
  OUTREACH_LOGOS     folder caching firms' logos (default: data/logos next to the hub)
  OUTREACH_PROJECTS  folder holding the other checkouts a research file may live in (default: settings `paths.projects`)

A lead group's open decision cards (same project id) ride along as `waiting`, read fresh on every request.
"""
import csv
import html
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request

import settings

REF = 'knowledge/reference/'
OUT = REF + 'outreach/'
SOURCES = OUT + 'sources.py'  # the deployment's own list readers, kept in the brain next to its lists
DOC_TYPES = {'.md', '.html', '.txt', '.csv', '.tsv'}

SINGLE = 'single'  # the shared group of every one-firm group (see Outreach.fold)
PROJECTS = {'business': 'Business', 'hq': 'Team tools', 'other': 'Other', SINGLE: 'Single firms'}  # the brain's sources.py LABELS come first


def _read(path):
    with open(path, encoding='utf-8-sig') as f:
        return f.read()


def _tsv(path):
    lines = [x for x in _read(path).splitlines() if x and not x.startswith('#')]
    return list(csv.DictReader(io.StringIO('\n'.join(lines)), delimiter='\t'))


def _csv(path):
    return list(csv.DictReader(io.StringIO(_read(path))))


def _clip(s, n=170):
    s = re.sub(r'\s+', ' ', (s or '').strip())
    return s if len(s) <= n else s[:n - 1].rsplit(' ', 1)[0] + '…'


MARK = 'Outreach lead:'  # the last lines of a move's task tie it back to its card and say which move it was
MOVE = 'Beacon move:'  # the task agent runs a tapped move first, even while paused (client/task-agent TAPPED)
RECENT = 600  # seconds a finished move keeps its button hidden: the lead's files may take a sync to show the result


def _mark(lead):
    return ' '.join(f"{lead.get('home') or lead.get('project')} / {lead.get('name')}".split())  # a folded lead keeps its own group's mark


def _lead(**kw):
    base = {'name': '', 'project': 'other', 'town': '', 'segment': '', 'know': '', 'status': 'not contacted',
            'next': '', 'touched': '', 'web': '', 'source': {'label': '', 'url': '', 'path': ''},
            # the detail a tap opens: chance of a deal and why, the person to talk to, and sections of facts
            # [{title, rows: [{label?, text, url?, path?, note?}]}] (a row's path opens in the viewer here)
            'chance': '', 'chance_why': '', 'person': '', 'detail': []}
    base.update(kw)
    return base


class Outreach:
    def __init__(self, brain=None, projects=None, questions=None, machines=None, tasks=None, live=None):
        self.brain = os.path.realpath(os.path.expanduser(brain or os.environ.get('OUTREACH_BRAIN') or settings.p('brain')))
        self.projects = os.path.realpath(os.path.expanduser(projects or os.environ.get('OUTREACH_PROJECTS') or settings.p('projects')))
        self.lock = threading.Lock()
        self.cache = (None, None)
        self.dates = {}
        self._sources = (None, None)
        self.questions = questions  # the hub's Questions, for the open cards a lead group waits on
        self.machines = machines  # the hub's Machines, for the jobs that keep a lead group fresh
        self.tasks = tasks  # the hub's Tasks: a lead's next move becomes a task for the task agent
        self.live = live  # () -> the live sessions view, to show the agent working a lead's task
        self.move_lock = threading.Lock()  # two taps (two tabs) never file the same move twice

    # ------------------------------------------------------------ sources

    def p(self, rel):
        return os.path.join(self.brain, rel)

    def repo(self, rel):
        """A path in another checkout under the projects folder: repo('<repo>/<path>')."""
        return os.path.join(self.projects, rel)

    def git_date(self, rel):
        """The day a file in ai-brain last changed (its mtime on a synced checkout is only the sync time)."""
        if rel not in self.dates:
            try:
                out = subprocess.run(['git', '-C', self.brain, 'log', '-1', '--format=%cs', '--', rel],
                                     capture_output=True, text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                out = ''
            self.dates[rel] = out
        return self.dates[rel]

    def watched(self):
        """Every path whose change rebuilds the view."""
        files = [OUT + 'leads.json', OUT + 'artifacts.json', OUT + 'research-files.json', SOURCES,
                 REF + 'research', REF + 'market-research']
        mod = self.sources()
        files += list(getattr(mod, 'WATCHED', None) or [])
        if callable(getattr(mod, 'watched', None)):  # paths in other checkouts (absolute), from o.repo(...)
            try:
                files += list(mod.watched(self) or [])
            except Exception as e:
                print(f'outreach: watched(): {e}', flush=True)
        if os.path.isdir(self.p(REF + 'research')):
            files += [REF + 'research/' + d for d in os.listdir(self.p(REF + 'research'))]
        return files

    def signature(self):
        sig = []
        for rel in self.watched():
            try:
                st = os.stat(self.p(rel))
                sig.append((rel, st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((rel, 0, 0))
        return tuple(sig)

    def sources(self):
        """The brain's own list readers (knowledge/reference/outreach/sources.py), or None when it has none.
        Loaded fresh whenever the file changes; see that file's docstring for what it provides."""
        path = self.p(SOURCES)
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        if self._sources[0] != key:
            try:
                spec = importlib.util.spec_from_file_location('outreach_sources', path)
                mod = importlib.util.module_from_spec(spec)
                was, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no cache file in the brain's checkout
                try:
                    spec.loader.exec_module(mod)
                finally:
                    sys.dont_write_bytecode = was
            except Exception as e:  # a broken sources.py hides its lists, never the page
                print(f'outreach: {path}: {e}', flush=True)
                mod = None
            self._sources = (key, mod)
        return self._sources[1]

    # what a sources.py reader builds leads with
    read, tsv, csv, clip, lead = staticmethod(_read), staticmethod(_tsv), staticmethod(_csv), staticmethod(_clip), staticmethod(_lead)

    def labels(self):
        mod = self.sources()
        return {**(getattr(mod, 'LABELS', None) or {}), **{k: v for k, v in PROJECTS.items() if k not in (getattr(mod, 'LABELS', None) or {})}}

    def manual(self):
        try:
            rows = json.loads(_read(self.p(OUT + 'leads.json'))).get('leads') or []
        except (OSError, ValueError):
            return []
        return [_lead(**{k: v for k, v in r.items() if k in _lead()}) for r in rows if r.get('name')]

    def leads(self):
        out, seen = [], set()
        self.listed = set()  # groups a list reader fills: a list, even while it holds one row
        # the same business twice (a hand-kept lead also in a scraped list): same name in the same town
        key = lambda x: (re.sub(r'[^a-z0-9]', '', x['name'].lower()), (x['town'].split() or [''])[0].lower())  # noqa: E731
        mod = self.sources()
        for part in (self.manual, *(mod.readers(self) if mod else [])):  # hand-kept first: it wins a duplicate
            try:
                rows = part()
            except Exception as e:  # a broken list reader hides only its own list, never the page
                print(f'outreach: a list reader failed: {e!r}', flush=True)
                rows = []
            for r in rows:
                if key(r) in seen:
                    continue
                seen.add(key(r))
                out.append(r)
                if part != self.manual:
                    self.listed.add(r['project'])
        return out

    def found_research(self):
        """Research runs the research skill saves (one folder each), and market-research notes: found live."""
        out = []
        base = self.p(REF + 'research')
        for d in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            full = os.path.join(base, d)
            if not os.path.isdir(full):
                continue
            for cand in ('page.html', 'report.md', 'prompt.txt', 'topic-check.md'):
                if os.path.isfile(os.path.join(full, cand)):
                    break
            else:
                continue
            title = ''
            for f in ('report.md', 'topic-check.md', 'prompt.txt'):
                if os.path.isfile(os.path.join(full, f)):
                    title = self.title_of(os.path.join(full, f))
                    if title:
                        break
            m = re.match(r'(\d{4}-\d{2}-\d{2})-(.+)', d)
            out.append({'title': title or (m.group(2) if m else d).replace('-', ' ').capitalize(),
                        'project': 'business', 'date': m.group(1) if m else '', 'kind': 'research',
                        'done': os.path.isfile(os.path.join(full, 'report.md')),
                        'doc': f'ai-brain/{REF}research/{d}/{cand}'})
        base = self.p(REF + 'market-research')
        for f in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            if f.endswith('.md'):
                m = re.search(r'(\d{4}-\d{2}-\d{2})', f)
                out.append({'title': self.title_of(os.path.join(base, f)) or f[:-3], 'project': 'business',
                            'date': m.group(1) if m else self.git_date(REF + 'market-research/' + f), 'kind': 'research',
                            'doc': f'ai-brain/{REF}market-research/{f}'})
        return out

    @staticmethod
    def title_of(path):
        try:
            with open(path, encoding='utf-8') as f:
                for i, line in enumerate(f):
                    m = re.match(r'#\s+(.+)', line) or re.search(r'<title>([^<]+)</title>', line)
                    if m:
                        return _clip(m.group(1), 120)
                    if path.endswith('.txt') and line.strip():
                        return _clip(line, 120)
                    if i > 60:
                        break
        except OSError:
            pass
        return ''

    def research(self):
        def load(rel, key):
            try:
                return json.loads(_read(self.p(rel))).get(key) or []
            except (OSError, ValueError):
                return []
        arts = [dict(a, kind=a.get('kind') or 'other', where='artifact') for a in load(OUT + 'artifacts.json', 'artifacts')]
        files = [dict(f, where='file') for f in load(OUT + 'research-files.json', 'files')]
        known = {f.get('doc') for f in files}
        for f in self.found_research():
            if f['doc'] not in known and not any(k and k.rsplit('/', 1)[0] == f['doc'].rsplit('/', 1)[0] for k in known):
                files.append(dict(f, where='file'))
        rows = arts + files
        for r in rows:
            r['link'] = r.get('url') or ('/outreach/doc?p=' + urllib.parse.quote(r['doc']) if r.get('doc') else '')
        rows.sort(key=lambda r: r.get('date') or '', reverse=True)
        return rows

    # ------------------------------------------------------------ view

    def view(self):
        with self.lock:
            sig = self.signature()
            if self.cache[0] == sig:
                view = self.cache[1]
            else:
                view = self.build()
                self.cache = (sig, view)
        agents = self.agents()
        leads = [dict(x, agent=agents[_mark(x)]) if _mark(x) in agents else x for x in view['leads']] if agents else view['leads']
        return dict(view, leads=leads, waiting=self.waiting({p['id'] for p in view['projects'] if p['leads']}, view['single']), feeds=self.feeds())

    # ------------------------------------------------------------ next moves

    def agents(self):
        """The agent task on each lead (open, or done in the last 2 days), keyed by the lead's mark, with the
        session working it now: what a card shows instead of its move buttons."""
        if not self.tasks:
            return {}
        try:
            rows = self.tasks.list(status='todo,doing,done', q=MARK, limit=300)
            sessions = {}
            if self.live:
                for m in self.live().get('machines') or []:
                    for x in m.get('sessions') or []:
                        sessions[x.get('id')] = {'state': x.get('state'), 'machine': m.get('machine'), 'doing': x.get('doing')}
        except Exception as e:  # a lead card never breaks on the task store
            print(f'outreach: agents: {e}', flush=True)
            return {}
        out = {}
        for t in rows:  # newest first
            body = t.get('body') or ''
            keys = re.findall('^' + re.escape(MARK) + r' (.+)$', body, re.M)
            if not keys:
                continue
            key, moved = keys[-1].strip(), (re.findall('^' + re.escape(MOVE) + r' (\S+)$', body, re.M) or [''])[-1]
            age = time.time() - (t.get('updated_at') or 0)
            if t['status'] == 'done' and age > 2 * 86400:
                continue
            if key in out and not (out[key]['status'] == 'done' and t['status'] != 'done'):
                continue  # the newest task wins, an open one over a finished one
            note = (t.get('last_note') or {}).get('text') or ''
            live = next((sessions[x] for x in reversed(t.get('sessions') or []) if x in sessions), None)
            out[key] = {'task': t['id'], 'title': t['title'], 'status': t['status'], 'handed': 'claude' in (t.get('tags') or []),
                        'waiting': bool(t.get('waiting_on_him')) or note.startswith('⏳'), 'note': note[:300], 'session': live,
                        'move': moved, 'fresh': t['status'] == 'done' and age < RECENT}
        return out

    def move(self, project, name, move, note='', author=None):
        """Hand a lead's next move to the task agent: a task built from the move the list's reader offers for
        that lead (never from the page), refused while another agent task on the lead is open."""
        if not self.tasks:
            raise ValueError('no task store')
        with self.move_lock:
            lead = next((x for x in self.view()['leads'] if x['project'] == project and x['name'] == name), None)
            if not lead:
                raise LookupError('no such lead')
            mv = next((m for m in lead.get('moves') or [] if m.get('id') == move), None)
            if not mv:
                raise ValueError(f'move: {lead["name"]} has no move {move!r} now')
            cur = lead.get('agent') or {}
            if cur and cur['status'] != 'done':
                raise ValueError(f'task {cur["task"]} is already on this lead')
            if cur.get('fresh') and cur.get('move') == move:
                raise ValueError(f'task {cur["task"]} just did this; its result shows within minutes')
            body = mv['brief'].rstrip()
            note = str(note or '').strip()[:2000]
            if note:
                body += f"\n\n## His note (from the Outreach card)\n{note}"
            body += f"\n\n{MARK} {_mark(lead)}\n{MOVE} {move}\n"
            fields = {'title': mv.get('task') or f"{mv['label']}: {lead['name']}", 'body': body, 'project': mv.get('project') or project,
                      'tags': ['claude'], 'status': 'todo', 'priority': 2}
            return self.tasks.create(fields, author) if author else self.tasks.create(fields)

    def build(self):
        """Every lead, research row and group count, from the sources as they are now."""
        self.dates = {}  # a source changed: its last-touched day may have too
        leads, research = self.leads(), self.research()
        for x in leads:  # a viewer link only for a file this box has (else the GitHub url, if any, carries it)
            if x['source'].get('path') and self.resolve(x['source']['path']):
                x['source']['link'] = '/outreach/doc?p=' + urllib.parse.quote(x['source']['path'])
            for sec in x.get('detail') or []:
                for row in sec.get('rows') or []:
                    if row.get('path') and not row.get('url') and self.resolve(row['path']):
                        row['url'] = '/outreach/doc?p=' + urllib.parse.quote(row['path'])
        labels = self.labels()
        single = self.fold(leads, labels, self.listed)
        counts = {}
        for x in leads:
            c = counts.setdefault(x['project'], {'leads': 0, 'research': 0, 'open': 0, 'touched': ''})
            c['leads'] += 1
            c['open'] += x['status'] not in ('dropped', 'rejected', 'client')
            c['touched'] = max(c['touched'], x['touched'] or '')
        for r in research:
            c = counts.setdefault(r.get('project') or 'other', {'leads': 0, 'research': 0, 'open': 0, 'touched': ''})
            c['research'] += 1
            c['touched'] = max(c['touched'], r.get('date') or '')
        order = list(labels) + [SINGLE]
        projects = [{'id': k, 'name': labels.get(k, k), **v} for k, v in
                    sorted(counts.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else 99, kv[0]))]
        return {'projects': projects, 'leads': leads, 'research': research, 'single': single}

    @staticmethod
    def fold(leads, labels, listed=()):
        """A hand-kept group holding one firm is that firm, not a category: two or more such groups share one
        'Single firms' group, each lead keeping its own group's name in `group` and id in `home`. Returns the folded group ids."""
        size = {}
        for x in leads:
            size[x['project']] = size.get(x['project'], 0) + 1
        single = sorted(k for k, v in size.items() if v == 1 and k not in listed)
        if len(single) < 2:
            return []
        for x in leads:
            if x['project'] in single:
                x['group'], x['home'] = labels.get(x['project'], x['project']), x['project']
                x['project'] = SINGLE
        return single

    def feeds(self):
        """The jobs that keep each lead group fresh (sources.py FEEDS: {group: [{job, what}]}), each with its live
        state from the machines view, so a list shows what updates it and when, not only /machines."""
        want = getattr(self.sources(), 'FEEDS', None) or {}
        if not want:
            return []
        jobs = {}
        try:
            for m in (self.machines.view().get('machines') if self.machines else None) or []:
                for j in m.get('jobs') or []:
                    jobs.setdefault(j.get('name'), (m.get('machine'), j))
        except Exception as e:  # the page never breaks on the machines view
            print(f'outreach: feeds: {e}', flush=True)
        out = []
        for project, rows in want.items():
            for f in rows:
                machine, j = jobs.get(f.get('job'), ('', {}))
                out.append({'project': project, 'job': f.get('job', ''), 'what': f.get('what', ''), 'machine': machine or '',
                            'status': j.get('status') or ('unknown' if not j else ''), 'last_run': j.get('last_run'),
                            'next_run': j.get('next_run'), 'code': j.get('code')})
        return out

    def waiting(self, ids, single=()):
        """Open decision cards filed under a lead group's project: what that group waits on him for (a folded
        one-firm group's cards wait under 'Single firms')."""
        if not self.questions or not ids:
            return []
        try:
            rows = self.questions.list(status='open', limit=500)
        except Exception as e:  # cards never hide the page
            print(f'outreach: waiting: {e}', flush=True)
            return []
        return [{'id': q['id'], 'project': SINGLE if q['project'] in single else q['project'], 'question': q['question']}
                for q in rows if q.get('project') in ids or q.get('project') in single]

    # ------------------------------------------------------------ documents

    def resolve(self, ref):
        """'<repo>/<path>' -> a real file under ai-brain or a project checkout, else None."""
        ref = (ref or '').lstrip('/')
        repo, _, rel = ref.partition('/')
        if not repo or not rel or repo.startswith('.'):
            return None
        root = self.brain if repo == 'ai-brain' else os.path.join(self.projects, repo)
        root = os.path.realpath(root)
        full = os.path.realpath(os.path.join(root, rel))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return None
        if os.path.splitext(full)[1].lower() not in DOC_TYPES or '/.git/' in full:
            return None
        if repo == 'ai-brain' and not os.path.relpath(full, root).startswith('knowledge' + os.sep):  # after resolving: no ../ out of it
            return None
        return full

    def doc(self, ref):
        """(content type, body, foreign) for a research file, Markdown rendered as a page; None when not allowed."""
        full = self.resolve(ref)
        if not full:
            return None
        ext = os.path.splitext(full)[1].lower()
        raw = _read(full)
        if ext == '.html':
            return 'text/html; charset=utf-8', raw.encode(), True
        title = self.title_of(full) or os.path.basename(full)
        if ext == '.md':
            body = render_md(raw, os.path.dirname(ref))
        elif ext in ('.csv', '.tsv'):
            body = render_table(raw, '\t' if ext == '.tsv' else ',')
        else:
            body = '<pre>' + html.escape(raw) + '</pre>'
        return 'text/html; charset=utf-8', DOC_PAGE.format(title=html.escape(title), body=body).encode(), False


# ------------------------------------------------------------ logos: a firm's icon by its website, fetched once

LOGO_SRC = 'https://www.google.com/s2/favicons?domain={}&sz=128'  # 404 when the site has no icon
LOGO_DIR = os.environ.get('OUTREACH_LOGOS') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'logos')
LOGO_DAYS = 30


def logo(domain, fetch=None, folder=None):
    """(content type, bytes) of a domain's icon, cached on disk for a month (a miss too); None when it has none."""
    domain = (domain or '').lower().strip().removeprefix('www.')
    if not re.fullmatch(r'[a-z0-9-]+(\.[a-z0-9-]+)+', domain) or len(domain) > 120:
        return None
    folder = folder or LOGO_DIR
    os.makedirs(folder, exist_ok=True)
    img, miss = os.path.join(folder, domain + '.img'), os.path.join(folder, domain + '.none')
    for path in (img, miss):
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < LOGO_DAYS * 86400:
            if path == miss:
                return None
            with open(path, 'rb') as f:
                data = f.read()
            return ('image/jpeg' if data[:2] == b'\xff\xd8' else 'image/png'), data
    try:
        data = (fetch or _fetch_logo)(domain)
    except Exception:  # offline or refused: try again next time, show the initials meanwhile
        return None
    with open(img if data else miss, 'wb') as f:
        f.write(data or b'')
    return logo(domain, fetch, folder) if data else None


def _fetch_logo(domain):
    """The site's own large icon (apple-touch-icon, 180 px) when it has one, else the search engine's favicon."""
    head = {'User-Agent': 'Mozilla/5.0 (Beacon outreach logos)'}
    for url in (f'https://{domain}/apple-touch-icon.png', f'https://www.{domain}/apple-touch-icon.png'):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=head), timeout=6) as r:
                data = r.read(400_000)
            if r.headers.get_content_type().startswith('image/') and len(data) > 1500 and data[:4] in (b'\x89PNG', b'\xff\xd8\xff\xe0', b'\xff\xd8\xff\xe1', b'\xff\xd8\xff\xdb'):
                return data
        except Exception:
            pass
    try:
        with urllib.request.urlopen(urllib.request.Request(LOGO_SRC.format(domain), headers=head), timeout=8) as r:
            return r.read(200_000)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return b''
        raise


# ------------------------------------------------------------ a small Markdown renderer (no dependency)

def _inline(s, base):
    s = html.escape(s, quote=False)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)

    def link(m):
        text, href = m.group(1), m.group(2).strip()
        if not re.match(r'[a-z]+:|#|/', href):  # a file next to this one: open it through the same viewer
            target = os.path.normpath(os.path.join(base, urllib.parse.unquote(href.split('#')[0])))
            href = '/outreach/doc?p=' + urllib.parse.quote(target)
        return f'<a href="{html.escape(href)}">{text}</a>'
    s = re.sub(r'\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)', link, s)
    s = re.sub(r'(?<!["=])(https?://[^\s<)]+)', r'<a href="\1">\1</a>', s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<![\w*])\*([^*\n]+)\*(?![\w*])', r'<em>\1</em>', s)
    s = re.sub(r'\[\[([^\]]+)\]\]', r'<span class="wiki">\1</span>', s)
    return s


def render_md(text, base=''):
    lines = text.split('\n')
    if lines and lines[0].strip() == '---':  # frontmatter
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                lines = lines[i + 1:]
                break
    out, i, para = [], 0, []

    def flush():
        if para:
            out.append('<p>' + _inline(' '.join(para), base) + '</p>')
            para.clear()
    while i < len(lines):
        line = lines[i]
        st = line.strip()
        if st.startswith('```'):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith('```'):
                j += 1
            out.append('<pre>' + html.escape('\n'.join(lines[i + 1:j])) + '</pre>')
            i = j + 1
            continue
        m = re.match(r'(#{1,6})\s+(.*)', st)
        if m:
            flush()
            n = len(m.group(1))
            out.append(f'<h{n}>{_inline(m.group(2), base)}</h{n}>')
        elif st.startswith('|') and i + 1 < len(lines) and re.match(r'\s*\|?\s*:?-{2,}', lines[i + 1]):
            flush()
            cells = lambda r: [c.strip() for c in r.strip().strip('|').split('|')]  # noqa: E731
            rows = ['<tr>' + ''.join(f'<th>{_inline(c, base)}</th>' for c in cells(line)) + '</tr>']
            i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append('<tr>' + ''.join(f'<td>{_inline(c, base)}</td>' for c in cells(lines[i])) + '</tr>')
                i += 1
            out.append('<div class="tw"><table>' + ''.join(rows) + '</table></div>')
            continue
        elif re.match(r'\s*([-*+]|\d+[.)])\s+', line):
            flush()
            tag = 'ol' if re.match(r'\s*\d', line) else 'ul'
            items = []
            while i < len(lines) and (re.match(r'\s*([-*+]|\d+[.)])\s+', lines[i]) or
                                      (lines[i].startswith('  ') and lines[i].strip() and items)):
                if re.match(r'\s*([-*+]|\d+[.)])\s+', lines[i]):
                    items.append(re.sub(r'\s*([-*+]|\d+[.)])\s+', '', lines[i], count=1))
                else:
                    items[-1] += ' ' + lines[i].strip()
                i += 1
            out.append(f'<{tag}>' + ''.join(f'<li>{_inline(x, base)}</li>' for x in items) + f'</{tag}>')
            continue
        elif st.startswith('>'):
            flush()
            out.append('<blockquote>' + _inline(st.lstrip('> '), base) + '</blockquote>')
        elif re.match(r'^(-{3,}|\*{3,})$', st):
            flush()
            out.append('<hr>')
        elif not st:
            flush()
        else:
            para.append(st)
        i += 1
    flush()
    return '\n'.join(out)


def render_table(text, delim):
    lines = [x for x in text.splitlines() if x and not x.startswith('#')]
    rows = list(csv.reader(io.StringIO('\n'.join(lines)), delimiter=delim))
    if not rows:
        return '<p>Empty.</p>'
    head = '<tr>' + ''.join(f'<th>{html.escape(c)}</th>' for c in rows[0]) + '</tr>'
    body = ''.join('<tr>' + ''.join(f'<td>{html.escape(c)}</td>' for c in r) + '</tr>' for r in rows[1:])
    return f'<p class="meta">{len(rows) - 1} rows</p><div class="tw"><table>{head}{body}</table></div>'


DOC_PAGE = '''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Beacon · {title}</title><link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="stylesheet" href="/brand.css"><link rel="stylesheet" href="/hub.css"><link rel="stylesheet" href="/outreach.css">
</head><body class="doc-page"><nav class="doc-back"><a href="/outreach#research">← Research</a></nav>
<article class="doc">{body}</article></body></html>'''
