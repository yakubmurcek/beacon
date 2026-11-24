#!/usr/bin/env python3
"""Agent usage hub: collectors POST events here; the dashboard reads the JSON API.

Binds to loopback only. On its server a dedicated tailscale node ("usage") proxies to it,
so only tailnet devices can reach it. Writes need the bearer token; reads don't.

  USAGE_TOKEN   bearer token collectors send (required for /api/ingest)
  USAGE_DB      SQLite path (default: data/usage.db next to the repo root)
  USAGE_PORT    default 4829
  USAGE_TZ      default Europe/Prague
  TASKS_OWNER   tailnet login allowed to write tasks (the Tailscale-User-Login header that
                `tailscale serve` adds); empty turns header writes off
  TASKS_TOKEN   bearer token agents and scripts write tasks with
"""
import gzip
import hmac
import http.client
import json
import mimetypes
import re
import os
import sqlite3
import subprocess
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from store import Store, parse_filter  # noqa: E402
from tasks import Tasks, agent_problem  # noqa: E402
from questions import Questions  # noqa: E402
from feedback import Feedback  # noqa: E402
from rules import Rules  # noqa: E402
from titles import Titles, ask_haiku  # noqa: E402
from narrate import Narrator  # noqa: E402
from studio import Studio  # noqa: E402
from progress import Progress, steps_of, current_index  # noqa: E402
from machines import Machines, telegram as machines_telegram  # noqa: E402
from names import Names  # noqa: E402
from outreach import Outreach, logo as outreach_logo  # noqa: E402
from knowledge import Knowledge  # noqa: E402
from rank import Rank  # noqa: E402
from outcomes import Outcomes  # noqa: E402
from buildlog import BuildLog  # noqa: E402
from aijobs import AIJobs  # noqa: E402
from labs import Labs  # noqa: E402
from talk import Talk, CapReached, VoiceError  # noqa: E402
from endings import Endings  # noqa: E402
from cardrules import CardRules  # noqa: E402
from beacon_mcp import BeaconMCP  # noqa: E402
import apps  # noqa: E402
from swr import SWR  # noqa: E402
from settings import CONFIG, HUB, OWNER_ID, browser as browser_settings, path as settings_path  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')


_ASSET_REF = re.compile(r'((?:href|src)=")([\w-]+\.(?:css|js))(")')
_STATIC = {}  # (path, etag, gzip) -> body: read and compressed once per version of the file


def asset_version(name):
    """A short version for a web/ file, from its size and change time: changes whenever the file does."""
    try:
        st = os.stat(os.path.join(WEB, name))
    except OSError:
        return ''
    return f'{(st.st_mtime_ns ^ st.st_size) & 0xffffffff:08x}'


BRIEF_BODY, BRIEF_NOTE = 320, 240
PR_URL = re.compile(r'https://github\.com/[\w.-]+/[\w.-]+/pull/\d+')
DONE_TEST = re.compile(r'^#{1,4}\s*done[- ]test', re.I | re.M)


def next_step(t):
    """The step the task stands on, from its whole body (the brief cuts it): {'label', 'mine'}, or None when
    the task has no written steps or every step is done. `mine` = an owner (👤) step, so Today can list it."""
    steps = steps_of(t.get('body') or '')
    i = current_index(steps, t.get('step'))
    if i is None or steps[i]['done']:
        return None
    return {'label': steps[i]['label'], 'mine': steps[i]['owner']}


def brief(rows):
    """A list for a page that shows each task as a row (Tasks home): bodies and the last note cut short,
    whether the body has a Done-test kept as a flag. The whole task comes from /api/tasks/<id> when opened."""
    out = []
    for t in rows:
        t = dict(t, has_done_test=bool(DONE_TEST.search(t.get('body') or '')), next_step=next_step(t))
        if len(t.get('body') or '') > BRIEF_BODY:
            t['body'] = t['body'][:BRIEF_BODY]
        note = t.get('last_note')
        if note and len(note.get('text') or '') > BRIEF_NOTE:
            pr = PR_URL.search(note['text'][BRIEF_NOTE:])  # the list links a PR named in the note: keep it
            t['last_note'] = dict(note, text=note['text'][:BRIEF_NOTE] + (' … ' + pr.group(0) if pr else ''))
        out.append(t)
    return out


def assets_changed():
    """When any CSS or JS file in web/ last changed."""
    with os.scandir(WEB) as it:
        return max((e.stat().st_mtime_ns for e in it if e.name.endswith(('.css', '.js'))), default=0)


def static_body(full, etag, ctype, gz):
    """The bytes to send for a web/ file: a page gets its CSS and JS links versioned, text is gzipped when asked."""
    key = (full, etag, gz)
    body = _STATIC.get(key)
    if body is None:
        with open(full, 'rb') as f:
            body = f.read()
        if ctype == 'text/html':
            body = _ASSET_REF.sub(lambda m: f'{m.group(1)}{m.group(2)}?v={asset_version(m.group(2))}{m.group(3)}',
                                  body.decode('utf-8')).encode('utf-8')
        if gz and (ctype.startswith('text/') or ctype in ('application/javascript', 'image/svg+xml')) and len(body) > 1400:
            body = gzip.compress(body, 6)
        if len(_STATIC) > 400:
            _STATIC.clear()
        _STATIC[key] = body
    return body
TOKEN = os.environ.get('USAGE_TOKEN', '')
MAX_BODY = 50 * 1024 * 1024
TASKS_OWNER = os.environ.get('TASKS_OWNER', '')
TASKS_TOKEN = os.environ.get('TASKS_TOKEN', '')
# Claude's and ChatGPT's servers can't join the tailnet: the hub server's Caddy lets only their IP ranges reach
# /mcp/<key> (the server's setup). Unset = that door doesn't exist.
MCP_KEY = os.environ.get('BEACON_MCP_KEY', '')
TASK_BODY_MAX = 1024 * 1024
CACHE = SWR()  # the slow reads (usage days, meta, power): see hub/swr.py
# Today's numbers are served at most a minute old (older ones wait for a rebuild); a past day hardly changes.
FRESH = lambda today: dict(fresh=15, stale=60) if today else dict(fresh=300, stale=3600)  # noqa: E731
TASK_PATH = re.compile(r'^/api/tasks/(\d+)(?:/(comments|sessions))?$')
QUESTION_PATH = re.compile(r'^/api/questions/(\d+)(?:/(answer|reopen|withdraw|applied|claim|release|waiting|handoff|see|flag))?$')
# What a box needs to run client/tasks without a checkout (a Windows box's launcher fetches these daily).
CLI_FILES = {
    '/cli/tasks': lambda: os.path.join(ROOT, 'client', 'tasks'),
    '/cli/settings.py': lambda: os.path.join(ROOT, 'hub', 'settings.py'),
    '/cli/config.example.json': lambda: os.path.join(ROOT, 'config.example.json'),
    '/cli/config.json': settings_path,  # this deployment's names; no secrets live in it
}
ENDING_PATH = re.compile(r'^/api/endings/(\d+)/(clear|unclear|act)$')
QUESTION_BODY_MAX = 24 * 1024 * 1024  # an ask may carry up to 4 screenshots (base64)

class Handler(BaseHTTPRequestHandler):
    store = None
    tasks = None
    questions = None
    machines = None
    names = None
    knowledge = None
    rules = None
    endings = None
    server_version = 'agent-usage'
    head = False
    timeout = 60  # a client that stalls mid-request gives its thread back instead of holding it

    def handle_one_request(self):
        # A browser that navigates away mid-response closes the socket: nothing to report.
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def log_message(self, fmt, *args):
        if not self.path.startswith('/api/ingest'):
            return
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def send_json(self, code, obj):
        body = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode()  # UTF-8 as is: Czech and emoji cost 2-4 bytes, not 6-12
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        if len(body) > 1400 and 'gzip' in self.headers.get('Accept-Encoding', ''):
            body = gzip.compress(body, 5)
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if not self.head:
            self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        base = apps.backend_for(url.path)
        if base:  # budget and Lighthouse, passed through from the box they run on
            return apps.proxy(self, base)
        if url.path == '/api/power':  # the power meter takes 0.5 s: its last answer at once, a fresh one behind it
            path = '/api/data' + ('?' + url.query if url.query else '')
            try:
                return self.send_json(200, CACHE.get(('power', path), lambda: apps.fetch_json(apps.POWER_URL, path), fresh=20, stale=60))
            except (OSError, ValueError, http.client.HTTPException):
                return self.send_json(502, {'error': 'the power meter is not answering'})
        # The build log shows Claude's replies (an ai-corpus source, gated like Knowledge) and screenshots that can
        # show his desktop: only he (his tailnet login) or an agent with the token, any other device gets a 401.
        if (url.path.startswith('/buildlog/img/') or url.path.startswith('/api/buildlog')) and not self.task_writer():
            return self.send_json(401, {'error': 'unauthorized'})
        if url.path.startswith('/buildlog/img/') and getattr(self, 'buildlog', None):
            return self.buildlog.image(self, url.path[len('/buildlog/img/'):], url.query)
        try:
            if url.path.startswith('/api/buildlog') and getattr(self, 'buildlog', None):
                return self.get_buildlog(url.path, q)
            if url.path == '/api/outcomes' and getattr(self, 'outcomes', None):  # the results strip on top of Team
                return self.send_json(200, self.outcomes.view())
            if url.path == '/api/team':  # the Team page: what each project's AI team does, in plain words
                return self.send_json(200, self.studio.view() if getattr(self, 'studio', None) else {'projects': [], 'resting': []})
            if url.path == '/api/live':
                live = self.store.live_now()
                if getattr(self, 'titles', None):  # the session's own name or task title, never "projects-e4"
                    self.titles.annotate([x for m in live['machines'] for x in m['sessions']])
                if getattr(self, 'narrator', None):
                    self.narrator.annotate(live)  # `now`: what a working agent does, in one plain sentence
                if self.machines:
                    live['alarms'] = self.machines.health  # the home server's hub-watch checks the alarm path is alive
                return self.send_json(200, live)
            if url.path == '/api/rank' and getattr(self, 'rank', None):  # AI Rank: http://beacon/rank and the Home tile
                return self.send_json(200, self.rank.get())
            if url.path == '/api/ai' and getattr(self, 'aijobs', None):  # AI jobs: http://beacon/ai
                return self.send_json(200, self.aijobs.view())
            if url.path.startswith('/api/endings') and self.endings:  # the feed's stuck and done cards (task 1857)
                if url.path == '/api/endings/missed':
                    return self.send_json(200, {'missed': self.endings.missed(float(q.get('hours') or 24), int(q.get('limit') or 60))})
                if url.path == '/api/endings':
                    return self.send_json(200, {'endings': self.endings.feed(q.get('status') == 'all', int(q.get('limit') or 200))})
            if url.path == '/api/meta':  # 0.2 s to build: served from the last build, rebuilt behind it
                return self.send_json(200, CACHE.get('meta', self.store.meta, fresh=10, stale=60))
            if url.path == '/api/machines' and self.machines:
                return self.send_json(200, self.machines.view())
            if url.path == '/api/freshness' and self.machines:  # what a collector reports and which copies to update
                return self.send_json(200, self.machines.freshness_for(q.get('machine') or ''))
            if url.path == '/api/names' and self.names:
                return self.send_json(200, self.names.view())
            if url.path == '/api/outreach' and getattr(self, 'outreach', None):  # every lead and research report
                return self.send_json(200, self.outreach.view())
            if url.path == '/outreach/doc' and getattr(self, 'outreach', None):  # one research file, read-only
                return self.send_doc(q.get('p', ''))
            if url.path == '/outreach/logo':  # a firm's icon by its website, cached
                got = outreach_logo(q.get('d', ''))
                if not got:
                    return self.send_json(404, {'error': 'no logo'})
                self.send_response(200)
                self.send_header('Content-Type', got[0])
                self.send_header('Cache-Control', 'public, max-age=604800')
                self.send_header('Content-Length', str(len(got[1])))
                self.end_headers()
                return self.wfile.write(got[1])
            if url.path == '/api/day':  # 0.7 s to build: the last build at once, a fresh one behind it
                date, filt = q.get('date') or self.store.today(), parse_filter(q)
                return self.send_json(200, CACHE.get(('day', date, filt), lambda: self.titled(self.store.day(date, filt)),
                                                     **FRESH(date >= self.store.today())))
            if url.path == '/api/range':
                a, b, filt = q['from'], q['to'], parse_filter(q)
                return self.send_json(200, CACHE.get(('range', a, b, filt), lambda: self.titled(self.store.range(a, b, filt)),
                                                     **FRESH(b >= self.store.today())))
            if url.path == '/api/search':
                return self.send_json(200, self.store.search(q['q'][:200], q['from'], q['to']))
            if url.path == '/api/session':
                s = self.store.session(q.get('machine'), q['session'])
                return self.send_json(200 if s else 404, s or {'error': 'not found'})
            if url.path.startswith('/api/knowledge/'):
                return self.get_knowledge(url.path, q)
            if url.path == '/mcp' or url.path.startswith('/mcp/'):  # no server-to-client stream: every answer comes back on its POST
                return self.send_json(405, {'error': 'POST only'})
            if url.path == '/api/labs/views':  # Labs: his saved answers (his own questions: owner only, like Knowledge)
                if not self.task_writer():
                    return self.send_json(401, {'error': 'unauthorized'})
                if not getattr(self, 'labs', None):
                    return self.send_json(200, {'views': []})
                self.labs.warm()  # the numbers get ready while he speaks
                return self.send_json(200, {'views': self.labs.views()})
            if url.path.startswith('/api/tasks'):
                return self.get_tasks(url.path, q)
            if url.path.startswith('/api/questions/file/'):  # a screenshot for a decision: can show anything, so his login only
                if not self.task_writer():
                    return self.send_json(401, {'error': 'unauthorized'})
                return self.send_file(self.questions.file(url.path[len('/api/questions/file/'):]))
            if url.path.startswith('/api/questions'):
                return self.get_questions(url.path, q)
            if url.path == '/api/rules':
                return self.send_json(200, {'rules': self.rules.list(), 'weeks': self.rules.weekly(),
                                            'decided': self.rules.decided(), 'last_sweep': self.rules.last_sweep()}
                                      if self.rules else {'rules': [], 'weeks': [], 'decided': [], 'last_sweep': None})
            if url.path == '/api/gate/approval':
                x = self.questions.approval(q.get('session'), q.get('category'))
                return self.send_json(200, {'id': x['id'], 'answer': x['answer'], 'question': x['question']} if x else {})
            if url.path == '/api/gate/log':
                return self.send_json(200, self.questions.gate_report(float(q['since']) if q.get('since') else None,
                                                                      int(q.get('limit') or 200)))
            if url.path.startswith('/api/'):
                return self.send_json(404, {'error': 'not found'})
        except KeyError as e:
            if e.args and e.args[0] in ('from', 'to', 'q', 'machine', 'session', 'project'):
                return self.send_json(400, {'error': f'bad request: missing {e.args[0]}'})
            traceback.print_exc()  # a KeyError from inside the store is a bug, not a bad request
            return self.send_json(500, {'error': 'server error'})
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})
        except (BrokenPipeError, ConnectionResetError):
            raise  # the client left; answering it with a 500 would only fail again
        except Exception:
            traceback.print_exc()
            return self.send_json(500, {'error': 'server error'})
        self.serve_static(url.path, url.query)

    # ------------------------------------------------------------ knowledge

    def get_knowledge(self, path, q):
        """/knowledge: his mail, messages, chats, calls and notes. Only he (his tailnet login) or an
        agent with the token reads them; any other tailnet device gets a 401, unlike the rest of the hub."""
        if not self.task_writer():
            return self.send_json(401, {'error': 'unauthorized'})
        k = self.knowledge
        if not k:
            return self.send_json(404, {'error': 'not found'})
        what = path[len('/api/knowledge/'):]
        try:
            if what == 'overview':
                return self.send_json(200, k.overview())
            if what == 'search':
                return self.send_json(200, k.search(q['q'], q.get('source')))
            if what == 'notes':
                return self.send_json(200, {'notes': k.notes()})
            if what == 'calls':
                return self.send_json(200, {'calls': k.calls()})
            if what == 'graph':
                return self.send_json(200, k.graph())
            if what in ('thread', 'call', 'note', 'gbrain'):
                x = (k.thread(q['id'], q.get('at')) if what == 'thread' else k.call(q['id']) if what == 'call'
                     else k.note(q['path']) if what == 'note' else k.gbrain_page(q['slug']))
                return self.send_json(200 if x else 404, x or {'error': 'not found'})
        except KeyError as e:
            if e.args and e.args[0] in ('q', 'id', 'path', 'slug'):
                return self.send_json(400, {'error': f'bad request: missing {e.args[0]}'})
            raise
        except (LookupError, OSError, sqlite3.Error, subprocess.SubprocessError) as e:  # that source isn't on this box, or is down
            return self.send_json(503, {'error': str(e)})
        return self.send_json(404, {'error': 'not found'})

    # ------------------------------------------------------------ labs

    def post_mcp(self, keyed=False):
        """Beacon inside Claude and ChatGPT (hub/beacon_mcp.py): MCP over HTTP, one JSON answer per POST.
        His login, the token or the connector key only, like Knowledge: it reads his numbers."""
        if not keyed and not self.task_writer():
            return self.send_json(401, {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32001, 'message': 'unauthorized'}})
        mcp = getattr(self, 'mcp', None)
        length = self.headers.get('Content-Length') or ''
        length = int(length) if length.isdigit() else 0
        if not mcp or not 0 < length <= 200000:
            return self.send_json(404 if not mcp else 413, {'error': 'not found' if not mcp else 'body size'})
        try:
            msg = json.loads(self.rfile.read(length))
        except ValueError:
            return self.send_json(400, {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}})
        out = [r for r in (mcp.handle(m) for m in msg) if r] if isinstance(msg, list) else mcp.handle(msg)
        if not out:  # only notifications
            self.send_response(202)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        return self.send_json(200, out)

    def post_labs(self, path):
        """Labs spends his model budget and reads his own data: his login or the token only."""
        if not self.task_writer():
            return self.send_json(401, {'error': 'unauthorized'})
        labs = getattr(self, 'labs', None)
        length = self.headers.get('Content-Length') or ''
        length = int(length) if length.isdigit() else 0
        if not labs or not 0 < length <= 20000:
            return self.send_json(404 if not labs else 413, {'error': 'not found' if not labs else 'body size'})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})
        if path == '/api/labs/forget':
            return self.send_json(200, labs.forget(str(body.get('key') or '')))
        if path != '/api/labs/ask':
            return self.send_json(404, {'error': 'not found'})
        # One JSON event per line, written as it happens (no length, no gzip), so cards appear one by one.
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        self.close_connection = True
        try:
            for ev in labs.ask(str(body.get('q') or ''), body.get('history') if isinstance(body.get('history'), list) else None,
                               str(body['key']) if body.get('key') else None):
                self.wfile.write((json.dumps(ev, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # he asked something else, or left

    def post_talk(self, path):
        """Labs talk mode (hub/talk.py): hears, speaks and acts for him, on his money: his login or the token only."""
        who = self.task_writer()
        if not who:
            return self.send_json(401, {'error': 'unauthorized'})
        if who == 'owner':  # another site's page must not answer his decisions or spend his voice minutes
            site = self.headers.get('Sec-Fetch-Site')
            if (site and site not in ('same-origin', 'none')) if path == '/api/talk/hear' else self.cross_site():
                return self.send_json(403, {'error': 'forbidden: cross-site write'})
        talk = getattr(self, 'talk', None)
        length = self.headers.get('Content-Length') or ''
        length = int(length) if length.isdigit() else 0
        if not talk or not 0 < length <= 4 * 1024 * 1024:
            return self.send_json(404 if not talk else 413, {'error': 'not found' if not talk else 'body size'})
        raw = self.rfile.read(length)
        device = (self.headers.get('X-Talk-Device') or '')[:20]
        try:
            if path == '/api/talk/hear':  # the body is the recorded turn itself
                secs = self.headers.get('X-Talk-Seconds') or '0'
                kind = 'wake' if self.headers.get('X-Talk-Kind') == 'wake' else 'hear'
                text = talk.hear(raw, self.headers.get('Content-Type') or 'audio/webm',
                                 float(secs) if secs.replace('.', '', 1).isdigit() else 0, kind, device)
                return self.send_json(200, {'text': text})
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError('expected a JSON object')
            if path == '/api/talk/say':
                data, ctype = talk.say(body.get('text'), device)
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            if path == '/api/talk/brief':
                return self.send_json(200, talk.brief(device))
            if path == '/api/talk/act':
                return self.send_json(200, talk.act(body, device))
            if path == '/api/talk/warm':  # a talk page opened: the own voice loads while he taps
                return self.send_json(200, {'ok': True, 'warming': talk.warm()})
            if path == '/api/talk/played':  # did the page's speaker play a line (a silent talk diagnoses itself)
                how = 'play-ok' if body.get('ok') else 'play-fail'
                why = ''.join(ch for ch in str(body.get('why') or '') if ch.isalnum() or ch in ' -_/.')[:40]
                talk.log(f'{how} {why}'.strip(), 0, device)
                return self.send_json(200, {'ok': True})
            if path == '/api/talk/start':  # one talk began (the Done-test counts talks per day)
                talk.log('session', 0, device)
                return self.send_json(200, {'ok': True, **talk.stats(1)})
            return self.send_json(404, {'error': 'not found'})
        except CapReached as e:
            return self.send_json(429, {'error': str(e)})
        except VoiceError as e:
            return self.send_json(502, {'error': f'voice: {e}'})
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})

    # ------------------------------------------------------------ build log

    def get_buildlog(self, path, q):
        if path == '/api/buildlog':
            return self.send_json(200, self.buildlog.view())
        if path == '/api/buildlog/game':
            return self.send_json(200, self.buildlog.game(q['project']))
        if path == '/api/buildlog/session':
            s = self.buildlog.session(q.get('machine'), q['session'])
            return self.send_json(200 if s else 404, s or {'error': 'not found'})
        return self.send_json(404, {'error': 'not found'})

    # ------------------------------------------------------------ tasks

    def get_tasks(self, path, q):
        if path == '/api/tasks':
            rows = self.tasks.list(q.get('status') or 'open', q.get('project'), q.get('q'), q.get('due'),
                                   q.get('stale') in ('1', 'true'), int(q.get('limit') or 500), q.get('session'))
            return self.send_json(200, {'tasks': brief(rows) if q.get('brief') in ('1', 'true') else rows})
        if path == '/api/tasks/projects':
            return self.send_json(200, {'projects': self.tasks.projects()})
        if path == '/api/tasks/auth':  # the vault's probe for TASKS_TOKEN, and a CLI self-check
            who = self.task_writer()
            return self.send_json(200 if who else 401, {'writer': who} if who else {'error': 'unauthorized'})
        m = TASK_PATH.match(path)
        t = self.tasks.get(int(m.group(1))) if m and not m.group(2) else None
        if not t:
            return self.send_json(404, {'error': 'not found'})
        # Each linked session with what it is doing now (None: not open on any machine).
        live = {x['id']: x for mach in self.store.live_now()['machines'] for x in mach['sessions']}
        for s in t['sessions']:
            x = live.get(s['session_id']) or {}
            s.update(state=x.get('state'), machine=x.get('machine'), title=x.get('title'), doing=x.get('doing'),
                     steps=x.get('steps') or [])
        return self.send_json(200, t)

    def task_writer(self):
        """Who may write: the owner's tailnet login (set by `tailscale serve`) or the token."""
        auth = self.headers.get('Authorization', '')
        if TASKS_TOKEN and hmac.compare_digest(auth, 'Bearer ' + TASKS_TOKEN):
            return 'agent'
        login = self.headers.get('Tailscale-User-Login', '')
        if TASKS_OWNER and login and hmac.compare_digest(login, TASKS_OWNER):
            return 'owner'
        return None

    def cross_site(self):
        """A write another site's page could fire with his login attached: not JSON (a form or a
        no-cors fetch needs no preflight), or a browser saying it comes from elsewhere."""
        if (self.headers.get('Content-Type') or '').split(';')[0].strip().lower() != 'application/json':
            return True
        site = self.headers.get('Sec-Fetch-Site')
        if site:  # every current browser sends it, and a page can't forge it
            return site not in ('same-origin', 'none')
        origin = self.headers.get('Origin')
        if origin and origin != 'null':
            name = lambda h: (h or '').split(':')[0].split('.')[0].lower()  # beacon == beacon.<tailnet>.ts.net
            return name(urllib.parse.urlparse(origin).netloc) != name(self.headers.get('Host'))
        return False

    def send_file(self, got):
        if not got:
            return self.send_json(404, {'error': 'not found'})
        body, ctype = got
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'private, max-age=31536000, immutable')  # named by its hash
        self.end_headers()
        if not self.head:
            self.wfile.write(body)

    def writer_body(self, limit=TASK_BODY_MAX):
        """(who, JSON body) of an allowed write, or (None, None) once the refusal is sent."""
        who = self.task_writer()
        if not who:
            return None, self.send_json(401, {'error': 'unauthorized'})
        if who == 'owner' and self.cross_site():
            return None, self.send_json(403, {'error': 'forbidden: cross-site write'})
        length = self.headers.get('Content-Length') or ''
        length = int(length) if length.isdigit() else 0
        if length > limit:
            return None, self.send_json(413, {'error': 'body size'})
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            return None, self.send_json(400, {'error': 'bad request: body is not JSON'})
        if not isinstance(body, dict):
            return None, self.send_json(400, {'error': 'bad request: expected a JSON object'})
        return who, body

    def post_outreach_move(self):
        """A tap on a lead's next move (Write the draft, Drop it…): the hub files the task for the agent."""
        who, body = self.writer_body()
        if not who:
            return
        if who != 'owner':
            return self.send_json(403, {'error': 'forbidden: only he hands a lead to the agent'})
        try:
            t = self.outreach.move(str(body.get('project') or ''), str(body.get('name') or ''), str(body.get('move') or ''),
                                   body.get('note') or '', OWNER_ID)
        except LookupError as e:
            return self.send_json(404, {'error': str(e)})
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})
        return self.send_json(201, {'task': t['id'], 'title': t['title']})

    def post_tasks(self, path):
        who, body = self.writer_body()
        if not who:
            return
        author = str(body.get('author') or (OWNER_ID if who == 'owner' else 'agent'))[:80]
        try:
            if who != 'owner' and (path == '/api/tasks' or TASK_PATH.match(path) and not TASK_PATH.match(path).group(2)):
                why = agent_problem(body, path == '/api/tasks')
                if why:
                    return self.send_json(400, {'error': f'refused: {why}'})
            if path == '/api/tasks':
                return self.send_json(201, self.tasks.create(body, author))
            m = TASK_PATH.match(path)
            if not m:
                return self.send_json(404, {'error': 'not found'})
            tid, sub = int(m.group(1)), m.group(2)
            if sub == 'comments':
                out = self.tasks.comment(tid, body.get('text'), author)
            elif sub == 'sessions':
                out = self.tasks.link(tid, body.get('session_id'))
            else:
                out = self.tasks.update(tid, body)
            if out is None:
                return self.send_json(404, {'error': 'not found'})
            return self.send_json(201 if sub else 200, out)
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})
        except Exception:
            traceback.print_exc()
            return self.send_json(500, {'error': 'server error'})

    # ------------------------------------------------------------ rules (his only: an agent never turns a rule on)

    def post_rules(self, path):
        who, body = self.writer_body()
        if not who:
            return
        if path == '/api/rules/sweep':  # the evening review on the home server: only proposes, his yes still turns a rule on
            return self.send_json(200, self.rules.sweep())
        if who != 'owner':
            return self.send_json(403, {'error': 'forbidden: rules are his to change'})
        try:
            m = re.match(r'^/api/rules/uses/(\d+)/undo$', path)
            if m:
                out = self.rules.undo_use(int(m.group(1)))
            else:
                m = re.match(r'^/api/rules/(\d+)$', path)
                if not m:
                    return self.send_json(404, {'error': 'not found'})
                out = self.rules.update(int(m.group(1)), body)
            return self.send_json(200, out) if out else self.send_json(404, {'error': 'not found'})
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})

    def post_feedback(self):
        """A page's feedback button: his marked-up screenshot and note become a task an agent starts on (hub/feedback.py)."""
        who, body = self.writer_body(QUESTION_BODY_MAX)
        if not who:
            return
        try:
            got = self.feedback.file(body, OWNER_ID if who == 'owner' else 'agent')
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})
        except Exception:  # noqa: BLE001 - logged; the page keeps his note and says it wasn't sent
            traceback.print_exc()
            return self.send_json(500, {'error': 'the hub failed to file it'})
        return self.send_json(201, {'task': got['task']['id'], 'question': got['question']['id'],
                                    'project': got['task']['project']})

    # ------------------------------------------------------------ questions

    def get_questions(self, path, q):
        if path == '/api/questions':
            task = q.get('task')
            rows = self.questions.list(
                q.get('status') or 'open', q.get('project'), q.get('kind'), q.get('q'),
                float(q['since']) if q.get('since') else None,
                True if task in ('true', 'any') else int(task) if task else None,  # task=1 is task #1
                q.get('pending') in ('1', 'true'), int(q.get('limit') or 500), q.get('session'))
            return self.send_json(200, {'questions': self.rules.annotate(rows) if self.rules else rows})
        if path == '/api/questions/rules':  # his rules about cards, today's 👎 lessons against the cap, what was held back
            return self.send_json(200, self.cardrules.summary() if getattr(self, 'cardrules', None) else {'rules': []})
        if path == '/api/questions/bad':  # the one log of bad cards: his 👎 flags and the lint's catches
            return self.send_json(200, {'questions': self.questions.bad(float(q['since']) if q.get('since') else None)})
        if path == '/api/questions/kinds':
            return self.send_json(200, {'kinds': self.questions.kinds(float(q['since']) if q.get('since') else None)})
        m = QUESTION_PATH.match(path)
        x = self.questions.get(int(m.group(1))) if m and not m.group(2) else None
        if x and self.rules:
            self.rules.annotate([x])  # which rule answered it, for its own page
        return self.send_json(200, x) if x else self.send_json(404, {'error': 'not found'})

    def post_endings(self, path):
        who, body = self.writer_body(64 * 1024)
        if not who:
            return
        try:
            if path == '/api/endings':
                return self.send_json(201, self.endings.post(body))
            if path == '/api/endings/clear':  # the done pile's "Clear all": the cards he saw
                if 'ids' in body:
                    return self.send_json(200, self.endings.clear(ids=body.get('ids')))
                return self.send_json(200, self.endings.clear(state=body.get('state')))
            if path == '/api/endings/unclear':  # its undo
                return self.send_json(200, self.endings.unclear_many(body.get('ids')))
            m = ENDING_PATH.match(path)
            if not m:
                return self.send_json(404, {'error': 'not found'})
            eid, act = int(m.group(1)), m.group(2)
            if act == 'clear':
                out = self.endings.clear(eid)
            elif act == 'unclear':
                out = self.endings.unclear(eid)
            else:  # a tap on his phone is his answer (page); an agent's token logs it as cli
                out = self.endings.act(eid, body.get('label'), 'page' if who == 'owner' else 'cli', body.get('words'))
            return self.send_json(200, out) if out else self.send_json(404, {'error': 'not found'})
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})
        except Exception:
            traceback.print_exc()
            return self.send_json(500, {'error': 'server error'})

    def post_names(self):
        who, body = self.writer_body()
        if not who:
            return
        try:
            return self.send_json(200, self.names.save(body))
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})

    def post_questions(self, path):
        who, body = self.writer_body(QUESTION_BODY_MAX)
        if not who:
            return
        try:
            if path == '/api/questions':
                author = str(body.get('author') or (OWNER_ID if who == 'owner' else 'agent'))[:80]
                return self.send_json(201, self.questions.create(body, author, hands=who == 'owner'))
            m = QUESTION_PATH.match(path)
            if not m or not m.group(2):
                return self.send_json(404, {'error': 'not found'})
            qid, act = int(m.group(1)), m.group(2)
            if act == 'answer':
                out = self.questions.answer(qid, body, 'page' if who == 'owner' else 'cli', hands=who == 'owner')
            elif act == 'reopen':
                out = self.questions.reopen(qid)
            elif act == 'withdraw':
                out = self.questions.withdraw(qid, body.get('note'))
            elif act == 'claim':
                won, out = self.questions.claim(qid, body.get('by'))
                out = out and dict(out, won=won)
            elif act == 'release':
                done, out = self.questions.release(qid, body.get('by'))
                out = out and dict(out, released=done)
            elif act == 'waiting':
                out = self.questions.waiting(qid)
            elif act == 'handoff':
                out = self.questions.handoff(qid, body.get('reason'))
            elif act == 'see':
                out = self.questions.add_see(qid, body)
            elif act == 'flag':
                if who != 'owner':  # his judgement of a card, never an agent's
                    return self.send_json(403, {'error': 'only he flags a card'})
                out = self.questions.flag(qid, body.get('note'))
            else:
                out = self.questions.applied(qid)
            return self.send_json(200, out) if out else self.send_json(404, {'error': 'not found'})
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})
        except Exception:
            traceback.print_exc()
            return self.send_json(500, {'error': 'server error'})

    def send_doc(self, ref):
        got = self.outreach.doc(ref)
        if not got:
            return self.send_json(404, {'error': 'not found'})
        ctype, body, foreign = got
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        if foreign:
            # a research page (its own HTML and scripts): sandboxed, so it never acts on the hub with his rights
            self.send_header('Content-Security-Policy', 'sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox')
        if len(body) > 1400 and 'gzip' in self.headers.get('Accept-Encoding', ''):
            body = gzip.compress(body, 6)
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        if not self.head:
            self.wfile.write(body)

    def do_HEAD(self):
        # Uptime checks and link previews ask with HEAD: same headers as GET, no body.
        self.head = True
        self.do_GET()

    def pass_through(self):
        base = apps.backend_for(urllib.parse.urlparse(self.path).path)
        if base:
            return apps.proxy(self, base)
        self.send_json(405, {'error': 'method not allowed'})

    do_PUT = do_PATCH = do_DELETE = pass_through

    def redirect(self, to, code=301):
        self.send_response(code)
        self.send_header('Location', to)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def titled(self, data):
        """A friendly name on each session (its task's title, else a short model-written one)."""
        if getattr(self, 'titles', None):
            try:
                self.titles.annotate(data.get('sessions'))
            except Exception:  # names are a nicety: never fail the page over them
                traceback.print_exc()
        return data

    def serve_static(self, path, query=''):
        # http://beacon is the hub's one name (renamed from inside, 2026-11-22): tasks at /, decisions at /decisions,
        # usage at /usage, machines at /machines, team (who is doing what) at /team; Home at /power, /budget, /lighthouse.
        host = (self.headers.get('Host') or '').split('.')[0].split(':')[0].lower()
        page = path.rstrip('/') or '/'
        if page == '/questions':
            return self.redirect('/decisions')
        if page == '/outcomes':  # the results live on top of Team (2026-11-22: no new tab)
            return self.redirect('/team', 302)
        if page == '/talk':  # the short link to talk: straight to the secure name, as an iPhone mic needs https
            return self.redirect((HUB.get('https') or '').rstrip('/') + '/labs?talk', 302)
        if page == '/system':  # System = Usage, Machines, Home, Labs, each on its own page with one sub-row
            return self.redirect('/usage', 302)
        if page in CLI_FILES:  # the agents' CLI and the settings it reads: boxes without a GitHub login refresh from here
            got = CLI_FILES[page]()
            if got is None:
                return self.send_json(404, {'error': 'not found'})
            with open(got, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if got.endswith('.json') else 'text/x-python; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            if not self.head:
                self.wfile.write(body)
            return
        if page == '/config.js':  # the deployment's names (owner, machines, projects), read before any page script
            body = ('window.BEACON = ' + json.dumps(browser_settings(), ensure_ascii=False) + ';\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            if not self.head:
                self.wfile.write(body)
            return
        if path != page and page != '/':  # /decisions/ would load its files from /decisions/...
            return self.redirect(page + ('?' + query if query else ''), 302)
        tasks_host = host == 'beacon'
        if page == '/decisions':
            rel = 'questions.html'
        elif page == '/':
            rel = 'tasks.html' if tasks_host else 'index.html'
        elif page == '/new' and tasks_host:
            rel = 'tasks.html'
        elif page == '/tasks':  # every page links the other two by path, on whatever name it was opened
            rel = 'tasks.html'
        elif page == '/usage':
            rel = 'index.html'
        elif page == '/machines':
            rel = 'machines.html'
        elif page == '/team':
            rel = 'team.html'
        elif page == '/knowledge':
            rel = 'knowledge.html'
        elif page == '/rank':
            rel = 'rank.html'
        elif page == '/ai':  # every job that runs a model by itself, its model, effort and cost
            rel = 'ai.html'
        elif page == '/power':
            rel = 'power.html'
        elif page == '/outreach':  # every lead and research report (Home)
            rel = 'outreach.html'
        elif page == '/buildlog':
            rel = 'buildlog.html'
        elif page == '/labs':  # Labs: ask in words, the answer drawn as live cards
            rel = 'labs.html'
        elif page == '/home':  # Home groups his other tools: Power, Money (budget), Lighthouse
            return self.redirect('/power', 302)
        else:
            rel = urllib.parse.unquote(path).lstrip('/')
        full = os.path.realpath(os.path.join(WEB, rel))
        if not full.startswith(os.path.realpath(WEB) + os.sep) or not os.path.isfile(full):
            self.send_error(404)
            return
        st = os.stat(full)
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        if full.endswith('.html'):  # its links carry the versions of its CSS and JS: a change to any of them is a new page
            etag = etag[:-1] + f'-{assets_changed():x}"'
        # A page is revalidated on every load (edits go live at once) and names its own CSS and JS with a version
        # (?v=), so those are cached for good and a page switch never waits on a round trip per file.
        versioned = 'v=' in (query or '')
        cache = 'public, max-age=31536000, immutable' if versioned or rel.startswith('fonts/') else 'no-cache'
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', cache)
            self.end_headers()
            return
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        gz = 'gzip' in self.headers.get('Accept-Encoding', '')
        body = static_body(full, etag, ctype, gz)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        if ctype.startswith('text/') or ctype in ('application/javascript', 'image/svg+xml'):
            if body.startswith(b'\x1f\x8b'):
                self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('ETag', etag)
        self.send_header('Cache-Control', cache)
        self.end_headers()
        if not self.head:
            self.wfile.write(body)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        base = apps.backend_for(path)
        if base:
            return apps.proxy(self, base)
        if path.startswith('/api/tasks'):
            return self.post_tasks(path)
        if path == '/api/outreach/move':
            return self.post_outreach_move()
        if path.startswith('/api/questions'):
            return self.post_questions(path)
        if path == '/api/feedback' and getattr(self, 'feedback', None):
            return self.post_feedback()
        if path.startswith('/api/rules/'):
            return self.post_rules(path)
        if path.startswith('/api/endings') and self.endings:
            return self.post_endings(path)
        if path.startswith('/api/labs/'):
            return self.post_labs(path)
        if path.startswith('/api/talk/'):
            return self.post_talk(path)
        if path == '/mcp':
            return self.post_mcp()
        if path.startswith('/mcp/'):  # the connector door: the key in the link is the login
            if not (MCP_KEY and len(MCP_KEY) >= 32 and hmac.compare_digest(path[5:], MCP_KEY)):
                return self.send_json(404, {'error': 'not found'})
            return self.post_mcp(keyed=True)
        if path == '/api/gate/log':  # the hook's shadow log: open to the tailnet (a hook has no token at hand), append-only, capped
            length = self.headers.get('Content-Length') or ''
            length = int(length) if length.isdigit() else 0
            if not 0 < length <= 8000:
                return self.send_json(413, {'error': 'body size'})
            try:
                return self.send_json(201, self.questions.log_gate(json.loads(self.rfile.read(length))))
            except ValueError as e:
                return self.send_json(400, {'error': f'bad request: {e}'})
        if path == '/api/names' and self.names:
            return self.post_names()
        if path == '/api/rank/ingest' and getattr(self, 'rank', None):  # the ai-rank collector, with the collector's token
            return self.post_rank()
        if path != '/api/ingest':
            return self.send_json(404, {'error': 'not found'})
        auth = self.headers.get('Authorization', '')
        if not TOKEN or not hmac.compare_digest(auth, 'Bearer ' + TOKEN):
            return self.send_json(401, {'error': 'unauthorized'})
        length = self.headers.get('Content-Length') or ''
        length = int(length) if length.isdigit() else 0
        if length <= 0 or length > MAX_BODY:
            return self.send_json(413, {'error': 'body size'})
        try:
            body = json.loads(self.rfile.read(length))
            machine = str(body['machine'])[:64]
            events = body.get('events') or []
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            return self.send_json(400, {'error': f'bad request: {e}'})
        try:  # past parsing, any failure is the hub's (logged); the collector resends the batch
            n = self.store.ingest(machine, events, body.get('version'), body.get('counts'), body.get('live'))
            if self.machines:  # set in main(); test servers without it still ingest
                self.machines.save(machine, body.get('host'))
            if getattr(self, 'aijobs', None):
                self.aijobs.save(machine, body.get('host'))
            if self.endings and isinstance(body.get('live'), dict):  # turns that ended, for the endings backstop
                self.endings.saw(machine, self.store.clean_live(body['live'].get('sessions') or []))
            if body.get('names') and getattr(self, 'titles', None):
                self.titles.save(machine, body['names'])
        except Exception:
            traceback.print_exc()
            return self.send_json(500, {'error': 'server error'})
        self.send_json(200, {'ok': True, 'stored': n})


    def post_rank(self):
        auth = self.headers.get('Authorization', '')
        if not TOKEN or not hmac.compare_digest(auth, 'Bearer ' + TOKEN):
            return self.send_json(401, {'error': 'unauthorized'})
        length = self.headers.get('Content-Length') or ''
        length = int(length) if length.isdigit() else 0
        if not 0 < length <= 4 * 1024 * 1024:
            return self.send_json(413, {'error': 'body size'})
        try:
            body = json.loads(self.rfile.read(length))
            return self.send_json(200, self.rank.ingest(body))
        except (ValueError, TypeError, AttributeError) as e:
            return self.send_json(400, {'error': f'bad request: {e}'})
        except Exception:
            traceback.print_exc()
            return self.send_json(500, {'error': 'server error'})


def main():
    db = os.environ.get('USAGE_DB') or os.path.join(ROOT, 'data', 'usage.db')
    os.makedirs(os.path.dirname(db), exist_ok=True)
    Handler.store = Store(db, os.environ.get('USAGE_TZ', 'Europe/Prague'))
    Handler.tasks = Tasks(Handler.store)
    Handler.questions = Questions(Handler.store, Handler.tasks)
    Handler.feedback = Feedback(Handler.tasks, Handler.questions)
    Handler.rules = Rules(Handler.store, Handler.questions)
    Handler.titles = Titles(Handler.store)
    Handler.narrator = Narrator(Handler.store)
    Handler.progress = Progress(Handler.store)
    Handler.studio = Studio(Handler.store, Handler.narrator, progress=Handler.progress)
    Handler.machines = Machines(Handler.store)
    Handler.names = Names(Handler.store)
    Handler.outreach = Outreach(questions=Handler.questions, machines=Handler.machines, tasks=Handler.tasks, live=Handler.store.live_now)
    Handler.knowledge = Knowledge()
    Handler.rank = Rank(Handler.store)
    Handler.outcomes = Outcomes(Handler.store)
    Handler.buildlog = BuildLog(Handler.store, Handler.titles)
    Handler.aijobs = AIJobs(Handler.store, Handler.titles)
    Handler.labs = Labs(Handler.store, Handler.tasks, Handler.questions, Handler.machines, Handler.rank, Handler.titles, Handler.narrator)
    Handler.mcp = BeaconMCP(Handler.labs)
    # His rules about cards (task 1903): the file outside the repo seeds them, his 👎 adds to them.
    Handler.cardrules = CardRules(Handler.store, Handler.questions.get, ask_haiku,
                                  CONFIG['paths'].get('card_rules', '~/.config/beacon/card-rules.md'))
    Handler.questions.cardrules = Handler.labs.cardrules = Handler.cardrules
    Handler.endings = Endings(Handler.store, Handler.questions, Handler.titles, ask_haiku, Handler.cardrules)
    Handler.labs.aijobs = Handler.aijobs  # its ai_jobs sources
    Handler.talk = Talk(Handler.store, Handler.labs, Handler.questions, Handler.tasks)
    Handler.aijobs.extra.append(Handler.talk.aijob)  # the voice's own cost on /ai
    if os.environ.get('HUB_ALARMS') == '1':  # the live hub tells him when talk loses its own voice
        Handler.talk.send = lambda text: machines_telegram(text, 'talk-voice')
    Handler.machines.watch()  # alarms only where HUB_ALARMS=1 (the live hub)
    Handler.questions.harvesting = True  # screenshots a question links to (a PR body) land on its card
    threading.Thread(target=Handler.questions.harvest_open, daemon=True).start()
    port = int(os.environ.get('USAGE_PORT', '4829'))
    def warm():
        # After a deploy the pages he opens most come back first: usage history and meta, AI jobs, then Labs' 60 days.
        Handler.store.warm()
        CACHE.get('meta', Handler.store.meta, fresh=10)
        Handler.aijobs.view()
        Handler.labs.warm(wait=True)
    threading.Thread(target=warm, daemon=True).start()
    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    httpd.daemon_threads = True
    print(f'agent usage hub on http://127.0.0.1:{port}/ (db {db})', flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    main()
