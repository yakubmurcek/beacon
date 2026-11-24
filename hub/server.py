#!/usr/bin/env python3
"""Agent usage hub: collectors POST events here; the dashboard reads the JSON API.

Binds to loopback only. On its server a dedicated tailscale node ("usage") proxies to it,
so only tailnet devices can reach it. Writes need the bearer token; reads don't.

  USAGE_TOKEN   bearer token collectors send (required for /api/ingest)
  USAGE_DB      SQLite path (default: data/usage.db next to the repo root)
  USAGE_PORT    default 4829
  USAGE_TZ      overrides the settings' `timezone`
  TASKS_OWNER   the tailnet login allowed to write from a browser, checked with tailscaled's whois
                (hub/whois.py); empty turns browser writes off
  TASKS_TOKEN   bearer token agents and scripts write tasks with
"""
import gzip
import hmac
import json
import logging
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import threading
import unicodedata
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import apps
from aijobs import AIJobs
from beacon_mcp import BeaconMCP
from buildlog import BuildLog
from cardrules import CardRules
from endings import Endings
from feedback import Feedback
from knowledge import Knowledge
from labs import Labs
from machines import Machines
from machines import telegram as machines_telegram
from names import Names
from narrate import Narrator
from outcomes import Outcomes
from outreach import Outreach
from outreach import logo as outreach_logo
from progress import Progress, current_index, steps_of
from questions import Questions
from rank import Rank
from rules import Rules
from settings import CONFIG, HUB, OWNER_ID, TIMEZONE
from settings import browser as browser_settings
from settings import path as settings_path
from store import Store, parse_filter
from studio import Studio
from swr import SWR
from talk import CapReached, Talk, VoiceError
from tasks import Tasks, agent_problem
from titles import Titles, ask_haiku
from whois import Whois, peer_address

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
log = logging.getLogger('beacon.server')


_ASSET_REF = re.compile(r'((?:href|src)=")([\w-]+\.(?:css|js))(")')
_STATIC = {}  # (path, etag, gzip) -> body: read and compressed once per version of the file


def asset_version(name):
    """A short version for a web/ file, from its size and change time: changes whenever the file does."""
    try:
        st = os.stat(os.path.join(WEB, name))
    except OSError:
        return ''
    return f'{(st.st_mtime_ns ^ st.st_size) & 0xffffffff:08x}'


# page path -> its file in web/
PAGE_FILES = {'/decisions': 'questions.html', '/tasks': 'tasks.html', '/usage': 'index.html', '/machines': 'machines.html',
              '/team': 'team.html', '/knowledge': 'knowledge.html', '/rank': 'rank.html',
              '/ai': 'ai.html',  # every job that runs a model by itself, its model, effort and cost
              '/power': 'power.html', '/outreach': 'outreach.html',  # every lead and research report
              '/buildlog': 'buildlog.html', '/labs': 'labs.html',  # Labs: ask in words, the answer drawn as live cards
              '/inbox': 'inbox.html'}  # every notice, pushed to the phone or held back (hub/notify.py)

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
# The certificate authority behind that name, offered at /ca.crt so his devices can trust it once ('' = none).
CA_CERT = os.environ.get('HUB_CA_CERT', '')
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
# Old and short addresses: where each now lives.
MOVED = {'/questions': ('/decisions', 301), '/outcomes': ('/team', 302),  # the results sit on top of Team
         '/system': ('/usage', 302), '/home': ('/power', 302),
         # an iPhone mic needs https: straight to the secure name
         '/talk': (lambda: (HUB.get('https') or '').rstrip('/') + '/labs?talk', 302)}
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
    whois = Whois()  # the caller's tailnet identity, asked of tailscaled (tests put a fake here)
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
        if url.path == '/api/power':
            return self.get_power(url.query)
        if self.buildlog_refused(url):
            return None
        try:
            if self.route_get(url.path, q) is not NOT_MINE:
                return None
            if url.path.startswith('/api/'):
                return self.send_json(404, {'error': 'not found'})
        except (BrokenPipeError, ConnectionResetError):
            raise  # the client left; answering it with a 500 would only fail again
        except Exception as e:  # the last net under every route: the page gets a 4xx or 500, the log the traceback
            return self.get_failed(url.path, e)
        return self.serve_static(url.path, url.query)

    def get_failed(self, path, e):
        """A GET route raised: a missing query parameter or a bad value is the caller's 400, anything else a 500."""
        if isinstance(e, KeyError) and e.args and e.args[0] in ('from', 'to', 'q', 'machine', 'session', 'project'):
            return self.send_json(400, {'error': f'bad request: missing {e.args[0]}'})
        if isinstance(e, ValueError):
            return self.send_json(400, {'error': f'bad request: {e}'})
        log.exception('GET %s', path, exc_info=e)  # a KeyError from inside the store is a bug, not a bad request
        return self.send_json(500, {'error': 'server error'})

    def buildlog_refused(self, url):
        """The build log shows Claude's replies (an ai-corpus source, gated like Knowledge) and screenshots that can
        show his desktop: only he or an agent with the token. Sends the 401, or the screenshot; True when it did."""
        img = url.path.startswith('/buildlog/img/')
        if (img or url.path.startswith('/api/buildlog')) and not self.task_writer():
            self.send_json(401, {'error': 'unauthorized'})
            return True
        if img and getattr(self, 'buildlog', None):
            self.buildlog.image(self, url.path[len('/buildlog/img/'):], url.query)
            return True
        return False

    def route_get(self, path, q):
        """Answer a GET from the route tables; NOT_MINE when no route (or its service on this box) takes it."""
        route = GET_ROUTES.get(path)
        if route is None:
            route = next((r for prefix, r in GET_PREFIXES if path.startswith(prefix)), None)
        if route is None:
            return NOT_MINE
        service, handler = route
        if service and not getattr(self, service, None):
            return NOT_MINE
        handler(self, path, q)
        return None

    def get_power(self, query):
        """The power meter takes 0.5 s: its last answer at once, a fresh one behind it."""
        path = '/api/data' + ('?' + query if query else '')
        try:
            return self.send_json(200, CACHE.get(('power', path), lambda: apps.fetch_json(apps.POWER_URL, path), fresh=20, stale=60))
        except apps.NET_ERRORS:
            return self.send_json(502, {'error': 'the power meter is not answering'})

    def get_live(self, path, q):
        live = self.store.live_now()
        if getattr(self, 'titles', None):  # the session's own name or task title, never "projects-e4"
            self.titles.annotate([x for m in live['machines'] for x in m['sessions']])
        if getattr(self, 'narrator', None):
            self.narrator.annotate(live)  # `now`: what a working agent does, in one plain sentence
        if self.machines:
            live['alarms'] = self.machines.health  # the home server's hub-watch checks the alarm path is alive
        return self.send_json(200, live)

    def get_outreach_logo(self, path, q):
        """A firm's icon by its website, cached."""
        got = outreach_logo(q.get('d', ''))
        if not got:
            return self.send_json(404, {'error': 'no logo'})
        self.send_response(200)
        self.send_header('Content-Type', got[0])
        self.send_header('Cache-Control', 'public, max-age=604800')
        self.send_header('Content-Length', str(len(got[1])))
        self.end_headers()
        return self.wfile.write(got[1])

    def get_day(self, path, q):
        """0.7 s to build: the last build at once, a fresh one behind it."""
        date, filt = q.get('date') or self.store.today(), parse_filter(q)
        return self.send_json(200, CACHE.get(('day', date, filt), lambda: self.titled(self.store.day(date, filt)),
                                             **FRESH(date >= self.store.today())))

    def get_range(self, path, q):
        a, b, filt = q['from'], q['to'], parse_filter(q)
        return self.send_json(200, CACHE.get(('range', a, b, filt), lambda: self.titled(self.store.range(a, b, filt)),
                                             **FRESH(b >= self.store.today())))

    def get_session(self, path, q):
        s = self.store.session(q.get('machine'), q['session'])
        return self.send_json(200 if s else 404, s or {'error': 'not found'})

    def get_labs_views(self, path, q):
        """Labs: his saved answers (his own questions: owner only, like Knowledge)."""
        if not self.task_writer():
            return self.send_json(401, {'error': 'unauthorized'})
        if not getattr(self, 'labs', None):
            return self.send_json(200, {'views': []})
        self.labs.warm()  # the numbers get ready while he speaks
        return self.send_json(200, {'views': self.labs.views()})

    def get_question_file(self, path, q):
        """A screenshot for a decision: can show anything, so his login only."""
        if not self.task_writer():
            return self.send_json(401, {'error': 'unauthorized'})
        return self.send_file(self.questions.file(path[len('/api/questions/file/'):]))

    def get_rules(self, path, q):
        if not self.rules:
            return self.send_json(200, {'rules': [], 'weeks': [], 'decided': [], 'last_sweep': None})
        return self.send_json(200, {'rules': self.rules.list(), 'weeks': self.rules.weekly(),
                                    'decided': self.rules.decided(), 'last_sweep': self.rules.last_sweep()})

    def get_gate_approval(self, path, q):
        x = self.questions.approval(q.get('session'), q.get('category'))
        return self.send_json(200, {'id': x['id'], 'answer': x['answer'], 'question': x['question']} if x else {})

    def get_gate_log(self, path, q):
        return self.send_json(200, self.questions.gate_report(float(q['since']) if q.get('since') else None,
                                                              int(q.get('limit') or 200)))

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
        view = KNOWLEDGE_VIEWS.get(what)
        if not view:
            return self.send_json(404, {'error': 'not found'})
        try:
            x = view(k, q)
        except KeyError as e:
            if e.args and e.args[0] in ('q', 'id', 'path', 'slug'):
                return self.send_json(400, {'error': f'bad request: missing {e.args[0]}'})
            raise
        except (LookupError, OSError, sqlite3.Error, subprocess.SubprocessError) as e:  # that source isn't on this box, or is down
            return self.send_json(503, {'error': str(e)})
        if what in KNOWLEDGE_LOOKUPS and not x:
            return self.send_json(404, {'error': 'not found'})
        return self.send_json(200, x)

    # ------------------------------------------------------------ labs

    def mcp_allowed(self, path):
        """True for the connector key in the link, or his login or the token on the tailnet; else the refusal is sent."""
        if path.startswith('/mcp/'):
            if MCP_KEY and len(MCP_KEY) >= 32 and hmac.compare_digest(path[5:], MCP_KEY):
                return True
            return self.send_json(404, {'error': 'not found'})
        who = self.task_writer()
        if not who:
            return self.send_json(401, {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32001, 'message': 'unauthorized'}})
        if who == 'owner' and self.cross_site():
            return self.send_json(403, {'error': 'forbidden: cross-site write'})
        return True

    def post_mcp(self, path):
        """Beacon inside Claude and ChatGPT (hub/beacon_mcp.py): MCP over HTTP, one JSON answer per POST.
        His login or the token on the tailnet; from Claude's and ChatGPT's servers, the connector key in the link
        is the login. Like Knowledge, it reads his numbers."""
        if not self.mcp_allowed(path):
            return None
        mcp = getattr(self, 'mcp', None)
        if not mcp:
            return self.send_json(404, {'error': 'not found'})
        raw = self.read_body(200000, required=True)
        if raw is None:
            return None
        try:
            msg = json.loads(raw)
        except ValueError:
            return self.send_json(400, {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}})
        out = [r for r in (mcp.handle(m) for m in msg) if r] if isinstance(msg, list) else mcp.handle(msg)
        if not out:  # only notifications
            self.send_response(202)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None
        return self.send_json(200, out)

    def post_labs(self, path):
        """Labs spends his model budget and reads his own data: his login or the token only."""
        if not self.writer():
            return None
        labs = getattr(self, 'labs', None)
        if not labs:
            return self.send_json(404, {'error': 'not found'})
        body = self.read_json(20000, required=True)
        if body is None:
            return None
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

    def talk_writer(self, path):
        """His login or the token, or None once refused. Another site's page must not answer his decisions or spend
        his voice minutes; a recorded turn is sent as audio, so only the browser's own site header can say so."""
        who = self.task_writer()
        if not who:
            return self.send_json(401, {'error': 'unauthorized'})
        if who == 'owner':
            site = self.headers.get('Sec-Fetch-Site')
            if (site and site not in ('same-origin', 'none')) if path == '/api/talk/hear' else self.cross_site():
                return self.send_json(403, {'error': 'forbidden: cross-site write'})
        return who

    def post_talk(self, path):
        """Labs talk mode (hub/talk.py): hears, speaks and acts for him, on his money: his login or the token only."""
        if not self.talk_writer(path):
            return None
        talk = getattr(self, 'talk', None)
        if not talk:
            return self.send_json(404, {'error': 'not found'})
        raw = self.read_body(4 * 1024 * 1024, required=True)
        if raw is None:
            return None
        device = (self.headers.get('X-Talk-Device') or '')[:20]
        try:
            if path == '/api/talk/hear':  # the body is the recorded turn itself
                return self.talk_hear(talk, raw, device)
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError('expected a JSON object')
            action = TALK_ACTIONS.get(path)
            if not action:
                return self.send_json(404, {'error': 'not found'})
            return action(self, talk, body, device)
        except CapReached as e:
            return self.send_json(429, {'error': str(e)})
        except VoiceError as e:
            return self.send_json(502, {'error': f'voice: {e}'})
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})

    def talk_hear(self, talk, raw, device):
        secs = self.headers.get('X-Talk-Seconds') or '0'
        kind = 'wake' if self.headers.get('X-Talk-Kind') == 'wake' else 'hear'
        text = talk.hear(raw, self.headers.get('Content-Type') or 'audio/webm',
                         float(secs) if secs.replace('.', '', 1).isdigit() else 0, kind, device)
        return self.send_json(200, {'text': text})

    def talk_say(self, talk, body, device):
        data, ctype = talk.say(body.get('text'), device)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def talk_played(self, talk, body, device):
        """Did the page's speaker play a line: a silent talk diagnoses itself."""
        how = 'play-ok' if body.get('ok') else 'play-fail'
        why = ''.join(ch for ch in str(body.get('why') or '') if ch.isalnum() or ch in ' -_/.')[:40]
        talk.log(f'{how} {why}'.strip(), 0, device)
        return self.send_json(200, {'ok': True})

    def talk_start(self, talk, body, device):
        """One talk began (the talks per day are counted)."""
        talk.log('session', 0, device)
        return self.send_json(200, {'ok': True, **talk.stats(1)})

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

    def get_task_list(self, path, q):
        rows = self.tasks.list(q.get('status') or 'open', q.get('project'), q.get('q'), q.get('due'),
                               q.get('stale') in ('1', 'true'), int(q.get('limit') or 500), q.get('session'))
        return self.send_json(200, {'tasks': brief(rows) if q.get('brief') in ('1', 'true') else rows})

    def get_task_auth(self, path, q):
        """The vault's probe for TASKS_TOKEN, and a CLI self-check."""
        who = self.task_writer()
        return self.send_json(200 if who else 401, {'writer': who} if who else {'error': 'unauthorized'})

    def get_task(self, path, q):
        """One task, each linked session with what it is doing now (None: not open on any machine)."""
        m = TASK_PATH.match(path)
        t = self.tasks.get(int(m.group(1))) if m and not m.group(2) else None
        if not t:
            return self.send_json(404, {'error': 'not found'})
        live = {x['id']: x for mach in self.store.live_now()['machines'] for x in mach['sessions']}
        for s in t['sessions']:
            x = live.get(s['session_id']) or {}
            s.update(state=x.get('state'), machine=x.get('machine'), title=x.get('title'), doing=x.get('doing'),
                     steps=x.get('steps') or [])
        return self.send_json(200, t)

    def caller(self):
        """The calling device's tailnet identity (whois of the address `tailscale serve` forwarded), or None."""
        ip = peer_address(self.client_address, self.headers)
        return self.whois(ip) if ip else None

    def task_writer(self):
        """Who may write: an agent with the token, or the owner's own device, as tailscaled names it."""
        auth = self.headers.get('Authorization', '')
        if TASKS_TOKEN and hmac.compare_digest(auth, 'Bearer ' + TASKS_TOKEN):
            return 'agent'
        caller = self.caller() if TASKS_OWNER else None
        if caller and hmac.compare_digest(caller.login, TASKS_OWNER):
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

    def send_ca(self):
        """The certificate authority of the hub's secure short name, for his devices to install once."""
        try:
            with open(CA_CERT, 'rb') as f:
                body = f.read()
        except OSError:
            self.send_json(404, {'error': 'not found'})
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-x509-ca-cert')
        self.send_header('Content-Disposition', 'attachment; filename="beacon-ca.crt"')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if not self.head:
            self.wfile.write(body)

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

    def read_body(self, limit, required=False):
        """The raw request body, or None once a 413 is sent: over `limit`, or empty where a body is `required`."""
        length = self.headers.get('Content-Length') or ''
        length = int(length) if length.isdigit() else 0
        if length > limit or (required and length <= 0):
            self.send_json(413, {'error': 'body size'})
            return None
        return self.rfile.read(length)

    def read_json(self, limit, required=False, kinds=(dict,)):
        """The request's JSON (an object unless `kinds` allows more), or None once the refusal is sent.
        An empty body reads as {} where none is `required`."""
        raw = self.read_body(limit, required)
        if raw is None:
            return None
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            return self.send_json(400, {'error': 'bad request: body is not JSON'})
        if not isinstance(body, kinds):
            return self.send_json(400, {'error': 'bad request: expected a JSON object'})
        return body

    def writer(self, cross_site_ok=False):
        """Who writes ('owner' or 'agent'), or None once the 401 or 403 is sent. His login also needs the write
        to come from Beacon's own pages: another site's page could fire it with his login attached."""
        who = self.task_writer()
        if not who:
            return self.send_json(401, {'error': 'unauthorized'})
        if who == 'owner' and not cross_site_ok and self.cross_site():
            return self.send_json(403, {'error': 'forbidden: cross-site write'})
        return who

    def writer_body(self, limit=TASK_BODY_MAX):
        """(who, JSON object) of an allowed write, or (None, None) once the refusal is sent."""
        who = self.writer()
        body = self.read_json(limit) if who else None
        return (who, body) if body is not None else (None, None)

    def post_outreach_move(self, path):
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

    def post_notify(self, path):
        """A notice from any sender (agents and boxes with the task token), or his read marks on the inbox."""
        who, body = self.writer_body(64 * 1024)
        if not who:
            return
        try:
            if path == '/api/notify/read':
                return self.send_json(200, self.notify.read(body.get('ids'), bool(body.get('all'))))
            if path in ('/api/notify/subscribe', '/api/notify/test'):
                if who != 'owner' or not self.webpush.ready():
                    return self.send_json(403 if who != 'owner' else 503,
                                          {'error': 'only his own phone' if who != 'owner' else 'web push is not set up'})
                if path == '/api/notify/subscribe':
                    return self.send_json(201, self.webpush.subscribe(body.get('subscription'), body.get('device')))
                ok, _, err = self.webpush.send('urgent' if body.get('urgent') else 'digest', '🔔 Beacon pings work',
                                               'This is what a notice looks like. Tap it to open the inbox.',
                                               self.notify.base + '/inbox')
                return self.send_json(200 if ok else 502, {'ok': ok, 'error': err})
            return self.send_json(201, self.notify.post(body))
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})

    def post_tasks(self, path):
        who, body = self.writer_body()
        if not who:
            return
        author = str(body.get('author') or (OWNER_ID if who == 'owner' else 'agent'))[:80]
        m = TASK_PATH.match(path)
        if path != '/api/tasks' and not m:
            return self.send_json(404, {'error': 'not found'})
        sub = m.group(2) if m else None
        try:
            if who != 'owner' and not sub:  # an agent's task must carry a plan and a done-test (hub/tasks.py)
                why = agent_problem(body, path == '/api/tasks')
                if why:
                    return self.send_json(400, {'error': f'refused: {why}'})
            if path == '/api/tasks':
                return self.send_json(201, self.tasks.create(body, author))
            out = self.task_write(int(m.group(1)), sub, body, author)
            if out is None:
                return self.send_json(404, {'error': 'not found'})
            return self.send_json(201 if sub else 200, out)
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})

    def task_write(self, tid, sub, body, author):
        if sub == 'comments':
            return self.tasks.comment(tid, body.get('text'), author)
        if sub == 'sessions':
            return self.tasks.link(tid, body.get('session_id'))
        return self.tasks.update(tid, body)

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

    def post_feedback(self, path):
        """A page's feedback button: his marked-up screenshot and note become a task an agent starts on (hub/feedback.py)."""
        who, body = self.writer_body(QUESTION_BODY_MAX)
        if not who:
            return
        try:
            got = self.feedback.file(body, OWNER_ID if who == 'owner' else 'agent')
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})
        except Exception:  # noqa: BLE001 - logged; the page keeps his note and says it wasn't sent
            log.exception('post_feedback failed')
            return self.send_json(500, {'error': 'the hub failed to file it'})
        return self.send_json(201, {'task': got['task']['id'], 'question': got['question']['id'],
                                    'project': got['task']['project']})

    # ------------------------------------------------------------ questions

    def get_question_list(self, path, q):
        task = q.get('task')
        rows = self.questions.list(
            q.get('status') or 'open', q.get('project'), q.get('kind'), q.get('q'), since(q),
            True if task in ('true', 'any') else int(task) if task else None,  # task=1 is task #1
            q.get('pending') in ('1', 'true'), int(q.get('limit') or 500), q.get('session'))
        return self.send_json(200, {'questions': self.rules.annotate(rows) if self.rules else rows})

    def get_question(self, path, q):
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

    def post_names(self, path):
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
            if act == 'flag' and who != 'owner':  # his judgement of a card, never an agent's
                return self.send_json(403, {'error': 'only he flags a card'})
            out = QUESTION_ACTIONS[act](self.questions, qid, body, who)
            return self.send_json(200, out) if out else self.send_json(404, {'error': 'not found'})
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})

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
                log.exception('titled failed')
        return data

    def send_bytes(self, body, ctype, cache='no-cache'):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        if not self.head:
            self.wfile.write(body)

    def send_generated(self, page):
        """The agents' CLI and the settings it reads (boxes without a GitHub login refresh from here), and the
        deployment's names every page reads first (/config.js). True when the page was one of them."""
        if page in CLI_FILES:
            got = CLI_FILES[page]()
            if got is None:
                self.send_json(404, {'error': 'not found'})
                return True
            with open(got, 'rb') as f:
                self.send_bytes(f.read(), 'application/json' if got.endswith('.json') else 'text/x-python; charset=utf-8')
            return True
        if page == '/config.js':
            body = ('window.BEACON = ' + json.dumps(browser_settings(), ensure_ascii=False) + ';\n').encode()
            self.send_bytes(body, 'application/javascript; charset=utf-8')
            return True
        return False

    def page_file(self, page, path):
        """The file under web/ a path names. Every page links the others by path, on whatever name it was opened;
        the bare hub name opens on the tasks."""
        tasks_host = (self.headers.get('Host') or '').split('.')[0].split(':')[0].lower() == 'beacon'
        if page in PAGE_FILES:
            return PAGE_FILES[page]
        if page == '/' or (page == '/new' and tasks_host):
            return 'tasks.html' if tasks_host else 'index.html'
        return urllib.parse.unquote(path).lstrip('/')

    def serve_static(self, path, query=''):
        page = path.rstrip('/') or '/'
        if page in MOVED:
            to, code = MOVED[page]
            return self.redirect(to() if callable(to) else to, code)
        if self.send_generated(page):
            return None
        if path != page and page != '/':  # /decisions/ would load its files from /decisions/...
            return self.redirect(page + ('?' + query if query else ''), 302)
        rel = self.page_file(page, path)
        full = os.path.realpath(os.path.join(WEB, rel))
        if not full.startswith(os.path.realpath(WEB) + os.sep) or not os.path.isfile(full):
            self.send_error(404)
            return
        return self.send_web_file(full, rel, query)

    def send_web_file(self, full, rel, query):
        """A file under web/, with its ETag. A page is revalidated on every load (edits go live at once) and names
        its own CSS and JS with a version (?v=), so those are cached for good and a page switch never waits on a
        round trip per file."""
        st = os.stat(full)
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        if full.endswith('.html'):  # its links carry the versions of its CSS and JS: a change to any of them is a new page
            etag = etag[:-1] + f'-{assets_changed():x}"'
        versioned = 'v=' in (query or '')
        cache = 'public, max-age=31536000, immutable' if versioned or rel.startswith('fonts/') else 'no-cache'
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', cache)
            self.end_headers()
            return
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        body = static_body(full, etag, ctype, 'gzip' in self.headers.get('Accept-Encoding', ''))
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
        route = POST_ROUTES.get(path) or next((r for prefix, r in POST_PREFIXES if path.startswith(prefix)), None)
        if route is None or (route[0] and not getattr(self, route[0], None)):
            return self.send_json(404, {'error': 'not found'})
        try:
            return route[1](self, path)
        except (BrokenPipeError, ConnectionResetError):
            raise  # the client left; answering it with a 500 would only fail again
        except Exception:  # the last net under every route: the caller gets a 500, the log the traceback
            log.exception('POST %s', path)
            return self.send_json(500, {'error': 'server error'})

    def collector_token(self):
        """A collector's write (usage events, AI Rank): the collectors' own token."""
        auth = self.headers.get('Authorization', '')
        return bool(TOKEN) and hmac.compare_digest(auth, 'Bearer ' + TOKEN)

    def post_ingest(self, path):
        """A collector's batch: usage events, the box's live sessions, its jobs and its session names."""
        if not self.collector_token():
            return self.send_json(401, {'error': 'unauthorized'})
        body = self.read_json(MAX_BODY, required=True)
        if body is None:
            return None
        if 'machine' not in body:
            return self.send_json(400, {'error': "bad request: 'machine'"})
        machine, events = str(body['machine'])[:64], body.get('events') or []
        n = self.store.ingest(machine, events, body.get('version'), body.get('counts'), body.get('live'))
        if self.machines:  # set in main(); test servers without it still ingest
            self.machines.save(machine, body.get('host'))
        if getattr(self, 'aijobs', None):
            self.aijobs.save(machine, body.get('host'))
        if self.endings and isinstance(body.get('live'), dict):  # turns that ended, for the endings backstop
            self.endings.saw(machine, self.store.clean_live(body['live'].get('sessions') or []))
        if body.get('names') and getattr(self, 'titles', None):
            self.titles.save(machine, body['names'])
        return self.send_json(200, {'ok': True, 'stored': n})

    def post_gate_log(self, path):
        """The approval hook's log, append-only and capped: a writer, or one of his tagged servers (a hook there
        has no token at hand)."""
        if not (self.task_writer() or getattr(self.caller(), 'tagged', False)):
            return self.send_json(401, {'error': 'unauthorized'})
        body = self.read_json(8000, required=True)
        if body is None:
            return None
        try:
            return self.send_json(201, self.questions.log_gate(body))
        except ValueError as e:
            return self.send_json(400, {'error': f'bad request: {e}'})

    def post_rank(self, path):
        if not self.collector_token():
            return self.send_json(401, {'error': 'unauthorized'})
        body = self.read_json(4 * 1024 * 1024, required=True)
        if body is None:
            return None
        try:
            return self.send_json(200, self.rank.ingest(body))
        except (ValueError, TypeError, AttributeError) as e:
            return self.send_json(400, {'error': f'bad request: {e}'})


NOT_MINE = object()  # route_get: no route here, so the static files (or the /api/ 404) answer


def since(q):
    """The `since` query parameter as a timestamp, or None."""
    return float(q['since']) if q.get('since') else None


def _view(service, method):
    """A route that answers with one service method's view, as JSON."""
    return lambda h, path, q: h.send_json(200, getattr(getattr(h, service), method)())


# Knowledge's views by the last part of the path: each reads one source (a missing key is a 400, a source that is
# down a 503). The lookups of one item answer 404 when it isn't there.
KNOWLEDGE_VIEWS = {
    'overview': lambda k, q: k.overview(),
    'search': lambda k, q: k.search(q['q'], q.get('source')),
    'notes': lambda k, q: {'notes': k.notes()},
    'calls': lambda k, q: {'calls': k.calls()},
    'graph': lambda k, q: k.graph(),
    'thread': lambda k, q: k.thread(q['id'], q.get('at')),
    'call': lambda k, q: k.call(q['id']),
    'note': lambda k, q: k.note(q['path']),
    'gbrain': lambda k, q: k.gbrain_page(q['slug']),
}
KNOWLEDGE_LOOKUPS = ('thread', 'call', 'note', 'gbrain')
# Exact paths: (the Handler attribute that must be set on this box, or None; the handler).
GET_ROUTES = {
    '/ca.crt': (None, lambda h, path, q: h.send_ca()),  # the authority behind https://<hub>, for his devices
    '/api/outcomes': ('outcomes', _view('outcomes', 'view')),  # the results strip on top of Team
    '/api/team': (None, lambda h, path, q: h.send_json(  # what each project's AI team does, in plain words
        200, h.studio.view() if getattr(h, 'studio', None) else {'projects': [], 'resting': []})),
    '/api/live': (None, Handler.get_live),
    '/api/rank': ('rank', _view('rank', 'get')),  # AI Rank: http://beacon/rank and the Home tile
    '/api/ai': ('aijobs', _view('aijobs', 'view')),  # AI jobs: http://beacon/ai
    # the feed's stuck and done cards (task 1857)
    '/api/endings': ('endings', lambda h, path, q: h.send_json(
        200, {'endings': h.endings.feed(q.get('status') == 'all', int(q.get('limit') or 200))})),
    '/api/endings/missed': ('endings', lambda h, path, q: h.send_json(
        200, {'missed': h.endings.missed(float(q.get('hours') or 24), int(q.get('limit') or 60))})),
    # 0.2 s to build: served from the last build, rebuilt behind it
    '/api/meta': (None, lambda h, path, q: h.send_json(200, CACHE.get('meta', h.store.meta, fresh=10, stale=60))),
    '/api/machines': ('machines', _view('machines', 'view')),
    # what a collector reports and which copies to update
    '/api/freshness': ('machines', lambda h, path, q: h.send_json(200, h.machines.freshness_for(q.get('machine') or ''))),
    '/api/names': ('names', _view('names', 'view')),
    '/api/notify': ('notify', _view('notify', 'view')),  # the inbox and its send log (task 1967)
    '/api/notify/vapid': ('webpush', lambda h, path, q: h.send_json(  # the key a phone subscribes with
        200, {'key': h.webpush.public_key(), 'devices': h.webpush.count()})),
    '/api/outreach': ('outreach', _view('outreach', 'view')),  # every lead and research report
    '/outreach/doc': ('outreach', lambda h, path, q: h.send_doc(q.get('p', ''))),  # one research file, read-only
    '/outreach/logo': (None, Handler.get_outreach_logo),
    '/api/day': (None, Handler.get_day),
    '/api/range': (None, Handler.get_range),
    '/api/search': (None, lambda h, path, q: h.send_json(200, h.store.search(q['q'][:200], q['from'], q['to']))),
    '/api/session': (None, Handler.get_session),
    # no server-to-client stream: every answer comes back on its POST
    '/mcp': (None, lambda h, path, q: h.send_json(405, {'error': 'POST only'})),
    '/api/labs/views': (None, Handler.get_labs_views),
    '/api/rules': (None, Handler.get_rules),
    '/api/gate/approval': (None, Handler.get_gate_approval),
    '/api/gate/log': (None, Handler.get_gate_log),
    '/api/tasks': (None, Handler.get_task_list),
    '/api/tasks/projects': (None, lambda h, path, q: h.send_json(200, {'projects': h.tasks.projects()})),
    '/api/tasks/auth': (None, Handler.get_task_auth),
    '/api/questions': (None, Handler.get_question_list),
    # his rules about cards, today's 👎 lessons against the cap, what was held back
    '/api/questions/rules': (None, lambda h, path, q: h.send_json(
        200, h.cardrules.summary() if getattr(h, 'cardrules', None) else {'rules': []})),
    # the one log of bad cards: his 👎 flags and the lint's catches
    '/api/questions/bad': (None, lambda h, path, q: h.send_json(200, {'questions': h.questions.bad(since(q))})),
    '/api/questions/kinds': (None, lambda h, path, q: h.send_json(200, {'kinds': h.questions.kinds(since(q))})),
}
# Prefixes, tried in order after the exact paths: the longer prefix first where two overlap.
GET_PREFIXES = (
    ('/api/buildlog', ('buildlog', Handler.get_buildlog)),
    ('/api/knowledge/', (None, Handler.get_knowledge)),
    ('/mcp/', GET_ROUTES['/mcp']),
    ('/api/tasks', (None, Handler.get_task)),
    ('/api/questions/file/', (None, Handler.get_question_file)),
    ('/api/questions', (None, Handler.get_question)),
)


def _claimed(questions, qid, body, who):
    won, out = questions.claim(qid, body.get('by'))
    return out and dict(out, won=won)


def _released(questions, qid, body, who):
    done, out = questions.release(qid, body.get('by'))
    return out and dict(out, released=done)


# What a POST to /api/questions/<id>/<act> does. His tap on the page is his answer ('page'); an agent's token
# passing his words on logs as 'cli'.
QUESTION_ACTIONS = {
    'answer': lambda qs, qid, body, who: qs.answer(qid, body, 'page' if who == 'owner' else 'cli', hands=who == 'owner'),
    'reopen': lambda qs, qid, body, who: qs.reopen(qid),
    'withdraw': lambda qs, qid, body, who: qs.withdraw(qid, body.get('note')),
    'claim': _claimed,
    'release': _released,
    'waiting': lambda qs, qid, body, who: qs.waiting(qid),
    'handoff': lambda qs, qid, body, who: qs.handoff(qid, body.get('reason')),
    'see': lambda qs, qid, body, who: qs.add_see(qid, body),
    'flag': lambda qs, qid, body, who: qs.flag(qid, body.get('note')),
    'applied': lambda qs, qid, body, who: qs.applied(qid),
}

# Labs talk's JSON actions (hearing a recorded turn is the one raw-audio POST).
TALK_ACTIONS = {
    '/api/talk/say': Handler.talk_say,
    '/api/talk/brief': lambda h, talk, body, device: h.send_json(200, talk.brief(device)),
    '/api/talk/act': lambda h, talk, body, device: h.send_json(200, talk.act(body, device)),
    # a talk page opened: the own voice loads while he taps
    '/api/talk/warm': lambda h, talk, body, device: h.send_json(200, {'ok': True, 'warming': talk.warm()}),
    '/api/talk/played': Handler.talk_played,
    '/api/talk/start': Handler.talk_start,
}

# POSTs, like the GETs: exact paths first, then prefixes in order. (service, handler): a route whose service is not
# set on this box answers 404. Every handler checks its own caller: a writer, a collector, or the MCP key.
POST_ROUTES = {
    '/api/outreach/move': (None, Handler.post_outreach_move),
    '/api/feedback': ('feedback', Handler.post_feedback),
    '/mcp': (None, Handler.post_mcp),
    '/api/gate/log': (None, Handler.post_gate_log),
    '/api/names': ('names', Handler.post_names),
    '/api/rank/ingest': ('rank', Handler.post_rank),
    '/api/ingest': (None, Handler.post_ingest),
    '/api/notify': ('notify', Handler.post_notify),
    '/api/notify/read': ('notify', Handler.post_notify),
    '/api/notify/subscribe': ('notify', Handler.post_notify),  # a phone turning pings on
    '/api/notify/test': ('notify', Handler.post_notify),  # "Send a test" on the inbox
}
POST_PREFIXES = (
    ('/api/tasks', (None, Handler.post_tasks)),
    ('/api/questions', (None, Handler.post_questions)),
    ('/api/rules/', ('rules', Handler.post_rules)),
    ('/api/endings', ('endings', Handler.post_endings)),
    ('/api/labs/', (None, Handler.post_labs)),
    ('/api/talk/', (None, Handler.post_talk)),
    ('/mcp/', (None, Handler.post_mcp)),  # the connector door: the key in the link is the login
)

def start_notify(live):
    """Notices (hub/notify.py): every alarm and finished move lands in /inbox; only the live hub pushes to the phone,
    through Beacon's own home-screen app (hub/webpush.py), Telegram while no phone has subscribed."""
    import machines as machines_mod  # after the sys.path line above, like every hub module
    import notify as notify_mod
    import webpush
    base = HUB.get('https') or HUB.get('url') or ''
    Handler.webpush = webpush.WebPush(Handler.store, os.environ.get('VAPID_PRIVATE_KEY'), base)
    wp = Handler.webpush

    def name():
        return 'webpush' if wp.ready() and wp.count() else 'telegram' if notify_mod.legacy_bin() else 'none'
    send = notify_mod.first_of(*([wp.send] if wp.ready() else []), notify_mod.telegram) if live else None
    Handler.notify = notify_mod.Notify(Handler.store, send, TIMEZONE, base, name)
    machines_mod.NOTIFY = lambda text, key, quiet: bool(
        Handler.notify.post(notify_mod.from_alarm(text, key, quiet, '🖥️ Machines')))
    Handler.tasks.on_close = outreach_done(Handler.tasks, Handler.notify, Handler.tasks.on_close)
    if live:  # the live hub tells him when talk loses its own voice
        Handler.talk.send = lambda text: machines_telegram(text, 'talk-voice')
        Handler.notify.watch()


def outreach_done(tasks, notices, then=None):
    """A task a tapped Outreach move filed, closed: one normal notice that opens the lead's card (task 1967).
    Chained after the slot's earlier owner (questions withdraws a closed task's cards)."""
    def closed(tid):
        if then:
            then(tid)
        try:
            t = tasks.get(tid)
            link = outreach_link(t.get('body') if t else '')
            if link:
                done = t['status'] == 'done'
                notices.post({'title': ('✅ ' if done else '🗑️ ') + t['title'], 'link': link, 'source': 'Outreach',
                              'key': f'outreach-move:{tid}', 'level': 'normal' if done else 'inbox', 'alone': done})
        except Exception:  # the task is closed either way; only its notice is lost, and the log says why
            log.exception('the outreach move notice for task %s failed', tid)
    return closed


def outreach_link(body):
    """The lead card a move's task came from (the `Outreach lead: <project> / <name>` line outreach.move writes)."""
    m = re.search(r'^Outreach lead: (.+?) / (.+)$', body or '', re.M)
    if not m:
        return ''
    slug = unicodedata.normalize('NFD', f'{m.group(1).strip()}-{m.group(2).strip()}'.lower())
    slug = re.sub(r'[^a-z0-9]+', '-', ''.join(c for c in slug if not unicodedata.combining(c)))
    return '/outreach#leads?l=' + slug  # the page's leadId()


def main():
    # One line per record on stdout: journald keeps the time, so the format doesn't.
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(levelname)s %(name)s: %(message)s')
    db = os.environ.get('USAGE_DB') or os.path.join(ROOT, 'data', 'usage.db')
    os.makedirs(os.path.dirname(db), exist_ok=True)
    Handler.store = Store(db, TIMEZONE)
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
    start_notify(os.environ.get('HUB_ALARMS') == '1')
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
    log.info('agent usage hub on http://127.0.0.1:%s/ (db %s)', port, db)
    httpd.serve_forever()


if __name__ == '__main__':
    main()
