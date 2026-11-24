"""Knowledge: everything the agents know, readable by him at /knowledge (2026-11-22).

Read-only, and the data stays where it lives; all of it sits on the hub's own server:

  corpus   ai-corpus's SQLite (mail, iMessage, Claude chats, Hermes, call transcripts), opened
           read-only. Search goes through ai-corpus's own lib/corpus.py (its FTS query rules and
           contact names); the calls list and full threads are plain read-only queries, because
           its REST facade can't group calls and caps a thread at its newest 1,000 rows.
  brain    the ai-brain checkout that ai-brain-sync keeps pulled (markdown notes).
  gbrain   the gbrain CLI (`gbrain search --json`, `gbrain get`), keyword search, no model calls.

Every lane answers on its own: a missing or slow source shows as unreachable, never as a 500.

  CORPUS_DIR   ai-corpus checkout (default: settings `paths.corpus`; corpus.db and lib/corpus.py in it)
  BRAIN_DIR    ai-brain checkout (default: settings `paths.brain`)
  GBRAIN_BIN   gbrain CLI (default: settings `paths.gbrain`)
"""
import contextlib
import html
import importlib.util
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import settings

log = logging.getLogger('beacon.knowledge')

CORPUS_DIR = os.environ.get('CORPUS_DIR', settings.p('corpus'))
BRAIN_DIR = os.environ.get('BRAIN_DIR', settings.p('brain'))
GBRAIN_BIN = os.environ.get('GBRAIN_BIN', settings.p('gbrain'))
ME = ('me', 'user', *(e.lower() for e in settings.OWNER.get('emails', [])))  # senders that are the owner himself

SOURCES = ('icloud-mail', 'imessage', 'claude-sessions', 'transcripts', 'hermes')
NOISE = ('session-harness',)  # tool, hook and script output inside a Claude chat: not words anyone said
THREAD_WINDOW = (150, 250)  # rows before and from the hit when a thread is long
ITEM_MAX = 8000  # characters of one message shown; longer ones are cut with a note
SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/@<>+=-]{0,300}$')
NOTE_SKIP = ('knowledge/archive/',)  # retired notes, deliberately unlisted (ai-brain index.md)


LINK = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]|\[[^\]\n]+\]\(([^)\s]+)\)')
NOT_A_NAME = {'mix', 'mixed', 'spolupracovnik', 'spoluhrac', 'treti', 'majitel', 'kamarad', 'speaker', 'unknown', 'nenahrano'}


def resolve_note(target, src, by_path, by_name):
    """A link's note path, the same way the page's noteHref resolves it (relative, then by bare name); None if none."""
    t = urllib.parse.unquote(target)
    t = re.sub(r'#.*$', '', t)
    t = re.sub(r'\.md$', '', t)
    if not t:
        return None
    if t.startswith('/'):
        tries = [t[1:]]
    else:
        parts = src.split('/')[:-1]
        for p in t.split('/'):
            if p == '..':
                parts = parts[:-1]
            elif p != '.':
                parts.append(p)
        tries = ['/'.join(parts), t]
    for p in tries:
        for cand in (p + '.md', p + '/README.md'):
            if cand in by_path:
                return cand
    return by_name.get(t.split('/')[-1].lower())


SESSION_CAP = 1800  # Claude chats in the graph: the newest real ones (a ball of ~2,000 dots still draws on a phone)
PROJECT_CAP = 250  # chats per project, so one busy project (clean-the-snow: 800+) is a cluster, not the whole ball
NOTE_PATH = re.compile(r'knowledge/[\w./-]+?\.md')


def graph_project(cwd):
    """The project a Claude chat ran in: the folder under Projects (any machine), not a subfolder of it."""
    parts = [p for p in re.split(r'[\\/]+', cwd or '') if p]
    low = [p.lower() for p in parts]
    for i, p in enumerate(low[:-1]):  # Projects, projects, Godot Projects, Unreal Projects
        if p.endswith('projects'):
            return _project('/' + parts[i + 1])
    if low[-2:] == ['lib', 'ai-brain']:
        return 'ai-brain'
    if not parts or (':' in parts[0] and len(parts) <= 3) or (low[0] in ('users', 'home') and len(parts) <= 2):
        return None  # a home folder or a drive: no project
    return _project('/'.join(parts))


def speaker_name(label):
    """A call speaker label -> (key, name) when it is a person's name; None for "Mixed (labels swap)…", roles, blanks.
    Alex / Alex Novak are one person, KommTo / Komto too (key = first name, accents off, doubled letters once)."""
    label = re.sub(r'\([^)]*\)?|\[[^\]]*\]?', '', label or '').strip()
    words = label.split()
    if not 1 <= len(words) <= 3 or not all(w[:1].isupper() for w in words):
        return None
    first = ''.join(c for c in unicodedata.normalize('NFKD', words[0].lower()) if not unicodedata.combining(c))
    first = re.sub(r'[^a-z]', '', first)
    if not first or first in NOT_A_NAME:
        return None
    return re.sub(r'(.)\1', r'\1', first), words[0]


def epoch(ts):
    """ISO time (any offset, trailing Z) -> unix seconds, None when it isn't one."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def terms(q):
    return [t for t in re.findall(r'[\w@.+-]+', q.lower().replace('*', '')) if t not in ('or', 'and')]


def snippet(text, words, before=110, after=230):
    """The part of text around the first search word, on one line."""
    flat = re.sub(r'\s+', ' ', text or '').strip()
    low = flat.lower()
    at = min([i for i in (low.find(w) for w in words) if i >= 0] or [0])
    start = max(0, at - before)
    if start:
        sp = flat.find(' ', start)
        start = sp + 1 if 0 <= sp < at else start
    end = min(len(flat), at + after)
    return ('…' if start else '') + flat[start:end] + ('…' if end < len(flat) else '')


class Knowledge:
    def __init__(self, corpus_dir=CORPUS_DIR, brain_dir=BRAIN_DIR, gbrain_bin=GBRAIN_BIN):
        self.db = os.path.join(corpus_dir, 'corpus.db')
        self.corpus_lib = os.path.join(corpus_dir, 'lib', 'corpus.py')
        self.brain = os.path.realpath(brain_dir)
        self.gbrain_bin = gbrain_bin
        self._lib = None
        self._notes = (0, None)  # (built at, [note]) refreshed every minute
        self._texts = {}  # path -> (mtime, text)
        self._lock = threading.Lock()

    # ------------------------------------------------------------ corpus

    @contextlib.contextmanager
    def connect(self):
        """A read-only connection, closed after use (sqlite3's own `with` only commits)."""
        if not os.path.isfile(self.db):
            raise LookupError('corpus not on this machine')
        con = sqlite3.connect(f'file:{self.db}?mode=ro', uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    def lib(self):
        """ai-corpus's own search code (stdlib only), loaded by path so its rules are the agents' rules."""
        if self._lib is None:
            spec = importlib.util.spec_from_file_location('ai_corpus_lib', self.corpus_lib)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._lib = mod
        return self._lib

    def contacts(self, con):
        try:
            return dict(con.execute('SELECT key, name FROM contacts'))
        except sqlite3.Error:
            return {}

    def who(self, row, cmap=None):
        """A sender in plain words: his own rows say his name, an agent's say Claude or Hermes."""
        s = (row['sender'] or '').strip()
        if 'sender_name' in row.keys() and row['sender_name']:
            return row['sender_name']
        if s.lower() in ME:
            return settings.OWNER_NAME
        if s == 'assistant':
            return 'Hermes' if row['source'] == 'hermes' else 'Claude'
        if cmap:
            for key in self.lib().contact_keys(s):
                if key in cmap:
                    return cmap[key]
        return s or 'unknown'

    @staticmethod
    def meta(row):
        try:
            return json.loads(row['meta_json'] or '{}') or {}
        except ValueError:
            return {}

    def overview(self):
        """Each source with how many items it holds and when the newest arrived (counted every 5 min:
        a count over the whole corpus takes two seconds)."""
        built, out = getattr(self, '_overview', (0, None))
        if out is None or time.time() - built > 300 or out['errors']:
            out = self._count()
            self._overview = (time.time(), out)
        return out

    def _count(self):
        out = {'sources': [], 'calls': 0, 'notes': 0, 'errors': {}}
        try:
            with self.connect() as con:
                for r in con.execute('SELECT source, count(*) n, max(ts) newest FROM items GROUP BY source'):
                    out['sources'].append({'source': r['source'], 'items': r['n'], 'newest': epoch(r['newest'])})
                out['calls'] = con.execute("SELECT count(DISTINCT thread_id) FROM items WHERE source='transcripts'").fetchone()[0]
        except (LookupError, sqlite3.Error) as e:
            out['errors']['corpus'] = str(e)
        try:
            out['notes'] = len(self.notes())
        except OSError as e:
            out['errors']['brain'] = str(e)
        return out

    def corpus_search(self, q, source=None, limit=40):
        lib = self.lib()
        words = terms(q)
        with self.connect() as con:
            rows = lib.search(con, q, source or None, None, None, None, min(1000, limit * 2))
        out = []
        for r in rows:
            if r.get('kind') in NOISE:
                continue
            m = self.meta_dict(r)
            text = r.get('text') or ''
            item = {'id': r['id'], 'source': r['source'], 'kind': r['kind'], 'thread': r['thread_id'],
                    'who': self.who(r), 'ts': epoch(r['ts']), 'snippet': snippet(text, words),
                    'partial': r.get('match') == 'partial'}
            if r['source'] == 'icloud-mail':  # mail keeps its HTML entities (m&#xe1;): plain letters here
                text = html.unescape(text)
                item['snippet'] = snippet(text, words)
                item['title'] = text.split('\n', 1)[0][:160]
                item['to'] = _recipients(r.get('recipients'))
            elif r['source'] == 'transcripts':
                item['title'] = m.get('title')
            elif r['source'] == 'claude-sessions':
                item['project'] = _project(m.get('cwd'))
            out.append(item)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def meta_dict(r):
        try:
            return json.loads(r.get('meta_json') or '{}') or {}
        except ValueError:
            return {}

    def thread(self, thread_id, at=None):
        """A conversation in time order. A long one opens around the hit (`at`, an item id),
        else at its newest rows; harness noise in Claude chats is left out."""
        with self.connect() as con:
            cmap = self.contacts(con)
            noise = ' AND kind NOT IN (%s)' % ','.join('?' * len(NOISE))
            base = 'SELECT * FROM items WHERE thread_id=?' + noise
            args = [thread_id, *NOISE]
            total = con.execute('SELECT count(*) FROM items WHERE thread_id=?' + noise, args).fetchone()[0]
            if not total:
                return None
            anchor = con.execute('SELECT ts FROM items WHERE id=? AND thread_id=?', (at, thread_id)).fetchone() if at else None
            if anchor:
                before = con.execute(base + ' AND ts < ? ORDER BY ts DESC LIMIT ?', args + [anchor['ts'], THREAD_WINDOW[0]]).fetchall()
                after = con.execute(base + ' AND ts >= ? ORDER BY ts LIMIT ?', args + [anchor['ts'], THREAD_WINDOW[1]]).fetchall()
                rows = before[::-1] + after
            else:
                rows = con.execute(base + ' ORDER BY ts DESC LIMIT ?', args + [sum(THREAD_WINDOW)]).fetchall()[::-1]
            first = con.execute('SELECT * FROM items WHERE thread_id=? ORDER BY ts LIMIT 1', (thread_id,)).fetchone()
        source = first['source']
        items = []
        for r in rows:
            text = r['text'] or ''
            items.append({'id': r['id'], 'who': self.who(r, cmap), 'me': (r['sender'] or '').lower() in ME,
                          'ts': epoch(r['ts']), 'kind': r['kind'], 'text': text[:ITEM_MAX], 'cut': len(text) > ITEM_MAX,
                          'to': _recipients(r['recipients']) if source == 'icloud-mail' else None})
        m = self.meta(first)
        if source == 'icloud-mail':
            title = (first['text'] or '').split('\n', 1)[0][:200]
        elif source == 'claude-sessions':
            starter = next((i for i in items if i['me']), None)
            title = snippet(starter['text'], [], 0, 120) if starter else 'Claude chat'
        elif source == 'imessage':
            other = next((i['who'] for i in items if not i['me']), None)
            title = f'Messages with {other}' if other else 'Messages'
        elif source == 'transcripts':
            title = m.get('title') or 'Call'
        else:
            title = 'Hermes chat'
        return {'id': thread_id, 'source': source, 'title': title, 'total': total, 'shown': len(items),
                'start': epoch(first['ts']), 'project': _project(m.get('cwd')), 'host': m.get('host'), 'items': items}

    def calls(self):
        """Every recorded call: when, how long, who and how much each one talked, its digest (cached a minute)."""
        built, out = getattr(self, '_calls', (0, None))
        if out is None or time.time() - built > 60:
            out = self._list_calls()
            self._calls = (time.time(), out)
        return out

    def _list_calls(self):
        with self.connect() as con:
            heads = con.execute("SELECT thread_id, min(ts) start, max(ts) last, count(*) n FROM items "
                                "WHERE source='transcripts' GROUP BY thread_id ORDER BY start DESC").fetchall()
            out = []
            for c in heads:
                talk = con.execute("SELECT sender, sum(coalesce(json_extract(meta_json,'$.duration_s'),0)) s, count(*) n "
                                   "FROM items WHERE thread_id=? GROUP BY sender ORDER BY s DESC", (c['thread_id'],)).fetchall()
                head = con.execute("SELECT meta_json FROM items WHERE thread_id=? AND meta_json LIKE '%\"digest\"%' LIMIT 1",
                                   (c['thread_id'],)).fetchone() or con.execute(
                    'SELECT meta_json FROM items WHERE thread_id=? ORDER BY ts LIMIT 1', (c['thread_id'],)).fetchone()
                m = self.meta(head)
                d = m.get('digest') or {}
                start, last = epoch(c['start']), epoch(c['last'])
                out.append({'id': c['thread_id'], 'title': m.get('title') or c['thread_id'], 'start': start,
                            'minutes': max(1, round(((last or 0) - (start or 0)) / 60)), 'turns': c['n'],
                            'speakers': [{'who': t['sender'] or 'unknown', 'seconds': round(t['s']), 'turns': t['n']} for t in talk],
                            'summary': d.get('summary'), 'decisions': len(d.get('decisions') or []),
                            'actions': len(d.get('action_items') or [])})
        return out

    def call(self, thread_id):
        """One call by speaker: consecutive lines of one person become one turn."""
        with self.connect() as con:
            rows = con.execute("SELECT * FROM items WHERE thread_id=? AND source='transcripts' ORDER BY ts", (thread_id,)).fetchall()
        if not rows:
            return None
        digest, title, turns = {}, None, []
        for r in rows:
            m = self.meta(r)
            digest = digest or m.get('digest') or {}
            title = title or m.get('title')
            who = r['sender'] or 'unknown'
            if turns and turns[-1]['who'] == who:
                turns[-1]['text'] += ' ' + (r['text'] or '')
            else:
                turns.append({'who': who, 'at': m.get('offset_s') or 0, 'text': r['text'] or ''})
        start, last = epoch(rows[0]['ts']), epoch(rows[-1]['ts'])
        return {'id': thread_id, 'title': title or thread_id, 'start': start,
                'minutes': max(1, round(((last or 0) - (start or 0)) / 60)),
                'speakers': list(dict.fromkeys(t['who'] for t in turns)),
                'summary': digest.get('summary'), 'decisions': digest.get('decisions') or [],
                'actions': digest.get('action_items') or [], 'questions': digest.get('open_questions') or [],
                'turns': turns}

    # ------------------------------------------------------------ ai-brain

    def notes(self):
        """Every live ai-brain note as a card: title, one line, section, last change (git), newest first."""
        built, notes = self._notes
        if notes is not None and time.time() - built < 60:
            return notes
        if not self._lock.acquire(blocking=notes is None):
            return notes  # another request is rebuilding: the last list is fine meanwhile
        try:
            notes = self._build_notes()
            self._notes = (time.time(), notes)
            return notes
        finally:
            self._lock.release()

    def _build_notes(self):
        if not os.path.isdir(os.path.join(self.brain, 'knowledge')):
            raise OSError('ai-brain not on this machine')
        changed = self._git_dates()
        blurbs = self._index_blurbs()
        out = []
        for d, dirs, files in os.walk(os.path.join(self.brain, 'knowledge')):
            dirs[:] = [x for x in dirs if not x.startswith('.')]
            for f in files:
                if not f.endswith('.md'):
                    continue
                rel = os.path.relpath(os.path.join(d, f), self.brain)
                if rel.startswith(NOTE_SKIP):
                    continue
                text = self.text(rel)
                fm, body = _frontmatter(text)
                parts = rel.split('/')
                section = 'reference' if parts[1] == 'reference' else 'feedback' if parts[1] == 'feedback' else 'core'
                topic = parts[2] if section == 'reference' and len(parts) > 3 else None
                stem = f[:-3]
                title = fm.get('title') or _heading(body) or stem.replace('-', ' ')
                if stem.lower() == 'readme' and len(parts) > 2 and not fm.get('title'):
                    title = _heading(body) or parts[-2].replace('-', ' ')
                by_name = blurbs.get(stem) if stem.lower() != 'readme' else None  # every folder has a README
                blurb = fm.get('description') or blurbs.get(rel) or by_name or _first_para(body)
                out.append({'path': rel, 'name': stem, 'title': title.strip(), 'blurb': (blurb or '')[:240],
                            'section': section, 'topic': topic, 'changed': changed.get(rel) or os.path.getmtime(os.path.join(d, f))})
        out.sort(key=lambda n: -n['changed'])
        return out

    def _git_dates(self):
        """path -> unix time of the last commit that touched it (file mtimes are pull times)."""
        try:
            log = subprocess.run(['git', '-C', self.brain, 'log', '--format=\x01%ct', '--name-only', '--', 'knowledge'],
                                 capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        out, ts = {}, None
        for line in log.splitlines():
            if line.startswith('\x01'):
                ts = int(line[1:] or 0)
            elif line and ts and line not in out:
                out[line] = ts
        return out

    def _index_blurbs(self):
        """The one line index.md gives each note ([[name]] — text, or [Title](path) — text)."""
        out = {}
        try:
            idx = self.text('index.md')
        except OSError:
            return out
        for line in idx.splitlines():
            m = re.match(r'\s*[-*]\s+(?:\[\[([^\]|]+)(?:\|[^\]]*)?\]\]|\[[^\]]+\]\(([^)]+)\))\s*[—–-]+\s*(.+)', line)
            if not m:
                continue
            key = (m.group(1) or m.group(2) or '').strip()
            key = key[:-3] if key.endswith('.md') else key
            desc = re.sub(r'\[\[([^\]|]+)\|?([^\]]*)\]\]', lambda x: x.group(2) or x.group(1), m.group(3)).strip()
            if '/' in key:
                out[key + '.md'] = desc
            out.setdefault(key.split('/')[-1], desc)
        return out

    def text(self, rel):
        full = os.path.realpath(os.path.join(self.brain, rel))
        if not full.startswith(self.brain + os.sep) or not full.endswith('.md') or not os.path.isfile(full):
            raise OSError('no such note')
        mt = os.path.getmtime(full)
        hit = self._texts.get(full)
        if hit and hit[0] == mt:
            return hit[1]
        with open(full, encoding='utf-8', errors='replace') as f:
            text = f.read()
        self._texts[full] = (mt, text)
        return text

    def note(self, rel):
        rel = os.path.normpath(rel or '').lstrip('/')
        try:
            text = self.text(rel)
        except OSError:
            return None
        card = next((n for n in self.notes() if n['path'] == rel), None)
        _, body = _frontmatter(text)
        body = re.sub(r'\A\s*#\s+.*\n', '', body, count=1)  # the page shows the title already
        return {'path': rel, 'title': card['title'] if card else _heading(body) or rel, 'changed': card and card['changed'],
                'section': card and card['section'], 'text': body}

    # ------------------------------------------------------------ the graph

    def graph(self):
        """The brain as an Obsidian-style graph (cached a minute): every note a node, every [[wikilink]] or relative
        .md link an edge (resolved the way the page resolves links, so a tap opens the same note), a wikilink to no
        note a faint dangling node. Calls join as a second layer through the people who spoke in them: hard links
        from the transcripts only, nothing guessed from a name showing up in text."""
        built, g = getattr(self, '_graph', (0, None))
        if g is not None and time.time() - built < 600:
            return g
        if g is not None:  # stale: the last one now, a fresh one built behind it (it reads the whole chat corpus)
            if not getattr(self, '_graph_building', False):
                self._graph_building = True
                threading.Thread(target=self._rebuild_graph, daemon=True).start()
            return g
        g = self._build_graph()
        self._graph = (time.time(), g)
        return g

    def _rebuild_graph(self):
        try:
            self._graph = (time.time(), self._build_graph())
        except Exception:  # the old graph stays; the next request tries again
            log.exception('rebuilding the knowledge graph failed')
        finally:
            self._graph_building = False

    def _graph_sessions(self, nodes, links, by_path):
        """Claude chats as dots round their project (the clusters an Obsidian vault gets from folders), and a line
        from a chat to each brain note it names by path (it read or wrote that note): real links, no guessing."""
        try:
            with self.connect() as con:
                heads = con.execute(
                    "SELECT thread_id, min(ts) start, count(*) n, json_extract(meta_json, '$.cwd') cwd, "
                    "substr(text, 1, 90) first FROM items WHERE source='claude-sessions' AND kind='session' "
                    "GROUP BY thread_id HAVING n >= 6 ORDER BY start DESC").fetchall()
                named = {}
                for tid, text in con.execute("SELECT thread_id, text FROM items WHERE source='claude-sessions' "
                                             "AND kind IN ('session', 'session-agent') AND instr(text, 'knowledge/') > 0"):
                    for p in NOTE_PATH.findall(text or ''):
                        if p in by_path:
                            named.setdefault(tid, set()).add(p)
        except (sqlite3.Error, OSError):
            return  # no corpus on this box: the notes alone still draw
        kept, per = 0, {}
        for tid, start, n, cwd, first in heads:
            proj = graph_project(cwd)
            if tid not in named and (not proj or kept >= SESSION_CAP or per.get(proj, 0) >= PROJECT_CAP):
                continue
            kept += 1
            per[proj] = per.get(proj, 0) + 1
            sid = 'chat:' + tid
            label = re.sub(r'\s+', ' ', first or '').strip()
            nodes[sid] = {'id': sid, 'label': (label[:80] or 'Claude chat'), 'group': 'chat', 'changed': epoch(start), 'size': n}
            if proj:
                pid = 'project:' + proj.lower()
                nodes.setdefault(pid, {'id': pid, 'label': proj, 'group': 'project'})
                links.add((sid, pid))
            for p in named.get(tid, ()):
                links.add((sid, p))

    def _build_graph(self):
        notes = self.notes()
        by_path = {n['path']: n for n in notes}
        by_name = {}
        for n in notes:  # newest first, like the page's list.find
            by_name.setdefault(n['name'].lower(), n['path'])
        for n in notes:  # [[trend-lag]] names a folder: its README
            if n['name'].lower() == 'readme' and n['path'].count('/') > 1:
                by_name.setdefault(n['path'].split('/')[-2].lower(), n['path'])
        nodes = {}
        for n in notes:
            group = 'decisions' if n['path'] == 'knowledge/decisions.md' else n['section']
            nodes[n['path']] = {'id': n['path'], 'label': n['title'], 'group': group, 'changed': n['changed']}
        links = set()
        for src in list(nodes):
            try:
                text = self.text(src)
            except OSError:
                continue
            text = re.sub(r'```.*?```', '', text, flags=re.S)
            text = re.sub(r'`[^`\n]*`', '', text)
            for m in LINK.finditer(text):
                target = m.group(1) or m.group(2)
                if m.group(2) and re.match(r'^[a-z][a-z0-9+.-]*:', target, re.I):
                    continue  # http(s), mailto: the web, not the brain
                hit = resolve_note(target, src, by_path, by_name)
                if not hit and m.group(1):
                    name = target.split('|')[0].split('#')[0].split('/')[-1].strip()
                    if not name:
                        continue
                    hit = 'missing:' + name.lower()
                    nodes.setdefault(hit, {'id': hit, 'label': name, 'group': 'missing'})
                if hit and hit != src:
                    links.add((src, hit))
        for c in self.calls():
            cid = 'call:' + c['id']
            nodes[cid] = {'id': cid, 'label': c['title'], 'group': 'call', 'changed': c.get('start')}
            for s in c['speakers']:
                who = speaker_name(s['who'])
                if not who:
                    continue
                pid = 'person:' + who[0]
                nodes.setdefault(pid, {'id': pid, 'label': who[1], 'group': 'person'})
                links.add((cid, pid))
        self._graph_sessions(nodes, links, by_path)
        deg = {}
        for a, b in links:
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
        for k, v in nodes.items():
            v['deg'] = deg.get(k, 0)
        return {'nodes': list(nodes.values()), 'links': [{'source': a, 'target': b} for a, b in sorted(links)]}

    def brain_search(self, q, limit=12):
        words = terms(q)
        if not words:
            return []
        hits = []
        for n in self.notes():
            text = self.text(n['path'])
            low = text.lower()
            if not all(w in low for w in words):
                continue
            title = n['title'].lower()
            score = sum(low.count(w) for w in words) + 8 * sum(w in title for w in words)
            plain = re.sub(r'\*\*|`|\[\[|\]\]|^#+\s*|\|', ' ', _frontmatter(text)[1], flags=re.M)  # markdown marks out
            hits.append((score, n['changed'], {**n, 'snippet': snippet(plain, words)}))
        hits.sort(key=lambda x: (-x[0], -x[1]))
        return [x[2] for x in hits[:limit]]

    # ------------------------------------------------------------ gbrain

    def gbrain(self, args, timeout=8):
        if not os.path.isfile(self.gbrain_bin):
            raise LookupError('gbrain not on this machine')
        env = dict(os.environ, PATH=os.path.dirname(self.gbrain_bin) + ':' + os.environ.get('PATH', '/usr/bin:/bin'))
        env.pop('OPENAI_API_KEY', None)  # keyword search only: a search box never spends money
        r = subprocess.run([self.gbrain_bin, *args], capture_output=True, text=True, timeout=timeout, env=env, cwd='/')
        if r.returncode:
            raise LookupError((r.stderr or 'gbrain failed').strip().splitlines()[-1][:200])
        return r.stdout

    def gbrain_search(self, q, limit=8):
        q = q.lstrip('-').strip() or '*'  # never an option to the CLI
        out = self.gbrain(['search', q, '--json', '--limit', str(limit), '--snippet-chars', '400'])
        rows = json.loads(out[out.find('['):] or '[]')
        words = terms(q)
        res = []
        for r in rows:
            text = re.sub(r'… \[truncated.*?\]$', '…', r.get('chunk_text') or '', flags=re.S)
            text = re.sub(r'^#.*\n', '', text)  # the chunk repeats the title as a heading
            res.append({'slug': r.get('slug'), 'title': r.get('title'), 'type': r.get('type'),
                        'thread': r.get('thread_id'), 'date': r.get('effective_date'), 'snippet': snippet(text, words)})
        return res

    def gbrain_page(self, slug):
        if not slug or not SLUG.match(slug) or slug.startswith('-'):
            return None
        text = self.gbrain(['get', slug])
        fm, body = _frontmatter(text)
        return {'slug': slug, 'title': fm.get('title') or _heading(body) or slug, 'type': fm.get('type'),
                'date': fm.get('date'), 'thread': fm.get('thread_id'), 'text': body}

    # ------------------------------------------------------------ search, all lanes at once

    def search(self, q, source=None):
        q = (q or '').strip()[:200]
        if not q:
            raise ValueError('empty search')
        corpus = source in (None, '', 'corpus') or source in SOURCES
        lanes = {'corpus': (lambda: self.corpus_search(q, source if source in SOURCES else None)) if corpus else None,
                 'brain': (lambda: self.brain_search(q)) if source in (None, '', 'brain') else None,
                 'gbrain': (lambda: self.gbrain_search(q)) if source in (None, '', 'gbrain') else None}
        out = {'q': q}
        ex = ThreadPoolExecutor(3)  # no `with`: leaving it would wait for the slowest lane after all
        futs = {k: ex.submit(f) for k, f in lanes.items() if f}
        deadline = time.time() + 12
        try:
            for k, f in futs.items():
                try:
                    out[k] = {'items': f.result(timeout=max(0.1, deadline - time.time()))}
                except sqlite3.OperationalError:
                    out[k] = {'items': [], 'error': 'Those search words could not be read. Try plain words.'}
                except LookupError as e:  # that source is not on this box: expected, nothing to log
                    out[k] = {'items': [], 'error': f'{k} not reachable ({type(e).__name__})'}
                except Exception as e:  # one slow or broken source never takes the others down
                    log.warning('knowledge search: %s: %s: %s', k, type(e).__name__, e)
                    out[k] = {'items': [], 'error': f'{k} not reachable ({type(e).__name__})'}
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return out


def _recipients(raw):
    try:
        return ', '.join(json.loads(raw)) if raw else None
    except (ValueError, TypeError):
        return raw


def _project(cwd):
    if not cwd:
        return None
    name = cwd.rstrip('/').split('/')[-1]
    if name.lower() in ('projects', 'root', 'home', 'tmp', '', os.path.basename(os.path.expanduser('~')).lower()) or re.search(r'scratch|^tmp|[0-9a-f]{8}-[0-9a-f]{4}', name):
        return None  # a scratch or temp folder says nothing about the project
    return name


def _frontmatter(text):
    if not text.startswith('---\n'):
        return {}, text
    end = text.find('\n---', 4)
    if end < 0:
        return {}, text
    fm = {}
    key = None
    for line in text[4:end].splitlines():
        m = re.match(r'^([A-Za-z_][\w-]*):\s*(.*)$', line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            fm[key] = '' if val in ('>-', '|', '>') else val.strip('\'"')
        elif key and line.startswith('  ') and not line.strip().startswith('- '):
            fm[key] = (fm[key] + ' ' + line.strip()).strip()
    return fm, text[end + 4:].lstrip('\n')


def _heading(body):
    m = re.search(r'^#\s+(.+)$', body, re.M)
    return re.sub(r'[*`]', '', m.group(1)).strip() if m else None


def _first_para(body):
    for para in re.split(r'\n\s*\n', body):
        p = para.strip()
        if p and not p.startswith(('#', '|', '```', '>', '---', '<!--', '[http')):
            p = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', p)  # [text](link) -> text
            p = re.sub(r'https?://\S+', '', p)
            p = re.sub(r'\s+', ' ', re.sub(r'[*`_]|\[\[|\]\]', '', p)).strip()
            if len(p) > 20:
                return p[:240]
    return None
