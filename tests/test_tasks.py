"""Tasks: the store, the API and the ClickUp import mapping."""
import datetime
import json
import os
import re
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'hub'), os.path.join(ROOT, 'scripts')]

from store import Store  # noqa: E402
from tasks import Tasks, split_paste  # noqa: E402
from questions import Questions  # noqa: E402
from settings import OWNER_ID  # noqa: E402

DAY = 86400


class TasksStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, 'u.db'))
        self.tasks = Tasks(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def today(self, days=0):
        return (datetime.datetime.now(self.store.tz).date() + datetime.timedelta(days=days)).isoformat()

    def test_a_title_is_enough(self):
        t = self.tasks.create({'title': 'koupit stebla'})
        self.assertEqual((t['project'], t['status'], t['priority'], t['due'], t['tags'], t['body']),
                         ('personal', 'todo', None, None, [], ''))
        self.assertIsInstance(t['id'], int)

    def test_a_paste_splits_into_title_and_body(self):
        self.assertEqual(split_paste('Fix login\n\nSteps:\n1. run it'), ('Fix login', 'Steps:\n1. run it'))
        long = 'Create a task to look at the new pricing page and compare it with the old one. ' * 8
        title, body = split_paste(long.strip())
        self.assertEqual(title, 'Create a task to look at the new pricing page and compare it with the old one.')
        self.assertTrue(body.startswith('Create a task'))  # nothing dictated is lost
        title, _ = split_paste('x' * 300)
        self.assertLessEqual(len(title), 120)
        t = self.tasks.create({'body': 'Call Dominik\nabout the invoice'})
        self.assertEqual((t['title'], t['body']), ('Call Dominik', 'about the invoice'))

    def test_bad_fields_name_themselves(self):
        for fields, name in (({'title': 'x', 'status': 'open'}, 'status'), ({'title': 'x', 'priority': 7}, 'priority'),
                             ({'title': 'x', 'due': 'tomorrow'}, 'due'), ({'title': ' '}, 'title'),
                             ({'title': 'x', 'tags': 'claude'}, 'tags')):
            with self.assertRaises(ValueError) as e:
                self.tasks.create(fields)
            self.assertTrue(str(e.exception).startswith(name), e.exception)

    def test_a_long_title_edit_keeps_the_body(self):
        t = self.tasks.create({'title': 'Short', 'body': 'the runbook'})
        got = self.tasks.update(t['id'], {'title': 'New title\nwith a second line'})
        self.assertEqual((got['title'], got['body']), ('New title', 'with a second line\n\nthe runbook'))
        got = self.tasks.update(t['id'], {'title': 'Plain rename'})
        self.assertEqual((got['title'], got['body']), ('Plain rename', 'with a second line\n\nthe runbook'))

    def test_done_stamps_closed_and_reopening_clears_it(self):
        t = self.tasks.create({'title': 'x'})
        done = self.tasks.update(t['id'], {'status': 'done'})
        self.assertIsNotNone(done['closed_at'])
        self.assertIsNone(self.tasks.update(t['id'], {'status': 'todo'})['closed_at'])
        self.assertIsNone(self.tasks.update(999, {'status': 'done'}))

    def test_the_list_is_open_tasks_with_newest_activity_first(self):
        a = self.tasks.create({'title': 'a'})
        self.tasks.create({'title': 'b'})
        c = self.tasks.create({'title': 'c'})
        self.tasks.update(c['id'], {'status': 'dropped'})
        self.tasks.update(a['id'], {'priority': 2})
        self.assertEqual([t['title'] for t in self.tasks.list()], ['a', 'b'])
        self.assertEqual([t['title'] for t in self.tasks.list(status='all')][:1], ['a'])
        self.assertEqual([t['title'] for t in self.tasks.list(status='dropped')], ['c'])

    def test_due_today_includes_overdue_in_prague_time(self):
        self.tasks.create({'title': 'late', 'due': self.today(-3)})
        self.tasks.create({'title': 'now', 'due': self.today() + 'T23:30'})
        self.tasks.create({'title': 'later', 'due': self.today(2)})
        self.tasks.create({'title': 'never'})
        self.assertEqual(sorted(t['title'] for t in self.tasks.list(due='today')), ['late', 'now'])

    def test_stale_means_open_and_untouched_for_two_weeks(self):
        old = self.tasks.create({'title': 'old', 'updated_at': time.time() - 20 * DAY})
        self.tasks.create({'title': 'fresh'})
        gone = self.tasks.create({'title': 'gone', 'updated_at': time.time() - 20 * DAY})
        self.tasks.update(gone['id'], {'status': 'done'})
        self.assertEqual([t['title'] for t in self.tasks.list(stale=True)], ['old'])
        self.tasks.update(old['id'], {})  # "keep" touches it
        self.assertEqual(self.tasks.list(stale=True), [])

    def test_search_reads_title_and_body(self):
        self.tasks.create({'title': 'Invoice', 'body': 'send to Dominik'})
        self.tasks.create({'title': 'Other'})
        self.assertEqual([t['title'] for t in self.tasks.list(q='dominik')], ['Invoice'])
        self.assertEqual([t['title'] for t in self.tasks.list(q='INVO')], ['Invoice'])

    def test_one_source_is_one_task(self):
        a = self.tasks.create({'title': 'x', 'source': 'clickup:abc'})
        b = self.tasks.create({'title': 'x again', 'source': 'clickup:abc'})
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(len(self.tasks.list(status='all')), 1)

    def test_comments_and_sessions_come_with_the_task(self):
        t = self.tasks.create({'title': 'x', 'project': 'clean-the-snow'})
        self.tasks.comment(t['id'], '✅ Saved in Studio', author='Claude Code')
        self.tasks.link(t['id'], 'Claude Code:bcab5cd8')
        self.tasks.link(t['id'], 'Claude Code:bcab5cd8')
        got = self.tasks.get(t['id'])
        self.assertEqual([c['text'] for c in got['comments']], ['✅ Saved in Studio'])
        self.assertEqual([s['session_id'] for s in got['sessions']], ['Claude Code:bcab5cd8'])
        self.assertIsNone(self.tasks.comment(999, 'x'))
        with self.assertRaises(ValueError):
            self.tasks.comment(t['id'], '  ')

    def test_closing_every_task_a_session_filed_ends_its_wait(self):
        now = time.time()
        snap = {'scanned_at': now, 'sessions': [
            {'agent': 'Claude Code', 'id': s, 'state': 'waiting', 'last': now - 60, 'project': 'demo'}
            for s in ('asked', 'half', 'old', 'plain')]}
        self.store.ingest('mac', [], 2, {}, snap)
        a = self.tasks.create({'title': 'Save Studio'})
        b1 = self.tasks.create({'title': 'Merge PR'})
        b2 = self.tasks.create({'title': 'Reply to Dominik'})
        c = self.tasks.create({'title': 'ancient'})
        self.tasks.link(a['id'], 'Claude Code:asked')
        self.tasks.link(b1['id'], 'Claude Code:half')
        self.tasks.link(b2['id'], 'Claude Code:half')
        self.tasks.link(c['id'], 'Claude Code:old')
        with self.store.connect() as con:  # filed long before that session's latest reply
            con.execute("UPDATE task_sessions SET linked_at = ? WHERE session_id = 'Claude Code:old'", (now - 86400,))
        for t in (a, b1, c):
            self.tasks.update(t['id'], {'status': 'done'})
        got = {x['id'].split(':')[1]: x['state'] for x in self.store.live_now()['machines'][0]['sessions']}
        self.assertEqual(got, {'asked': 'done', 'half': 'waiting', 'old': 'waiting', 'plain': 'waiting'})
        self.assertNotIn('Claude Code:asked', [x['id'] for x in self.store.live()])  # not "running"

    def test_projects_count_open_tasks(self):
        self.tasks.create({'title': 'a', 'project': 'clean-the-snow'})
        self.tasks.create({'title': 'b', 'project': 'clean-the-snow'})
        d = self.tasks.create({'title': 'c', 'project': 'weddings'})
        self.tasks.update(d['id'], {'status': 'done'})
        self.assertEqual(self.tasks.projects(), [{'project': 'clean-the-snow', 'open': 2}, {'project': 'weddings', 'open': 0}])


class LiveHub:
    """A real hub server on a free port, with a temporary database. tailscaled is faked: OWNER_IP is the
    owner's phone, SERVER_IP one of his tagged servers, any other address is unknown to it."""
    OWNER = 'me@example.com'
    OWNER_IP, SERVER_IP = '100.100.1.1', '100.100.2.2'

    def setUp(self):
        import server
        from http.server import ThreadingHTTPServer
        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        server.Handler.store = Store(os.path.join(self.tmp.name, 'u.db'))
        server.Handler.tasks = Tasks(server.Handler.store)
        server.Handler.questions = Questions(server.Handler.store, server.Handler.tasks)
        self.saved = (server.TASKS_OWNER, server.TASKS_TOKEN, server.Handler.whois)
        server.TASKS_OWNER, server.TASKS_TOKEN = self.OWNER, 'tok'
        from whois import Caller
        devices = {self.OWNER_IP: Caller(self.OWNER, False), self.SERVER_IP: Caller('tagged-devices', True)}
        server.Handler.whois = staticmethod(devices.get)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server.TASKS_OWNER, self.server.TASKS_TOKEN, self.server.Handler.whois = self.saved
        self.tmp.cleanup()

    def call(self, path, body=None, headers=None, raw=None):
        headers = dict(headers or {})
        if body is not None:
            headers.setdefault('Content-Type', 'application/json')
        req = urllib.request.Request(self.base + path, headers=headers,
                                     data=raw if raw is not None else None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get('Content-Type') == 'application/json' else raw)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def me(self):
        """What `tailscale serve` sends for a request from his phone."""
        return {'X-Forwarded-For': self.OWNER_IP}


class TasksServerTest(LiveHub, unittest.TestCase):

    def test_a_brief_list_cuts_bodies_and_keeps_the_done_test_flag(self):
        body = 'x' * 900 + '\n## Done-test\nit works'
        self.assertEqual(self.call('/api/tasks', {'title': 'long one', 'body': body}, self.me())[0], 201)
        _, full = self.call('/api/tasks')
        _, short = self.call('/api/tasks?brief=1')
        self.assertEqual(full['tasks'][0]['body'], body)
        self.assertEqual(len(short['tasks'][0]['body']), 320)
        self.assertTrue(short['tasks'][0]['has_done_test'])

    def test_writes_need_the_owner_or_the_token(self):
        self.assertEqual(self.call('/api/tasks', {'title': 'x'})[0], 401)
        self.assertEqual(self.call('/api/tasks', {'title': 'x'}, {'Tailscale-User-Login': 'guest@x.com'})[0], 401)
        self.assertEqual(self.call('/api/tasks', {'title': 'x'}, {'Authorization': 'Bearer nope'})[0], 401)
        code, t = self.call('/api/tasks', {'title': 'mine'}, self.me())
        self.assertEqual((code, t['title']), (201, 'mine'))
        self.assertEqual(self.call('/api/tasks', {'title': 'agent', 'body': '## Goal\nx\n## Done-test\ny\n## Steps\n1. z'}, {'Authorization': 'Bearer tok'})[0], 201)

    def test_only_his_own_device_signs_as_him(self):
        """A note or task under his name answers its task's open cards: an agent's token never signs with it."""
        tok = {'Authorization': 'Bearer tok'}
        dt = '## Goal\nx\n## Done-test\ny\n## Steps\n1. z'
        _, t = self.call('/api/tasks', {'title': 'mine'}, self.me())
        notes, ask = f"/api/tasks/{t['id']}/comments", {'question': 'Ship it?', 'options': ['Yes']}
        for owner in (OWNER_ID, f' {OWNER_ID.upper()} '):
            self.assertEqual(self.call(notes, {'text': 'merge', 'author': owner}, tok)[0], 403)
            self.assertEqual(self.call('/api/tasks', {'title': 'Ship it', 'body': dt, 'author': owner}, tok)[0], 403)
            self.assertEqual(self.call('/api/questions', dict(ask, author=owner), tok)[0], 403)
        self.assertEqual(self.call(notes, {'text': 'started', 'author': 'Claude Code ab'}, tok)[0], 201)
        self.assertEqual(self.call(notes, {'text': 'ok'}, self.me())[0], 201)
        self.assertEqual([(c['author'], c['text']) for c in self.call(f"/api/tasks/{t['id']}")[1]['comments']],
                         [('Claude Code ab', 'started'), (OWNER_ID, 'ok')])

    def test_another_site_cannot_write_with_his_login(self):
        me = self.me()
        # a no-cors fetch or a form: text/plain body, no preflight, his login attached by tailscale
        self.assertEqual(self.call('/api/tasks', raw=b'{"title":"x"}', headers=dict(me, **{'Content-Type': 'text/plain'}))[0], 403)
        self.assertEqual(self.call('/api/tasks', {'title': 'x'}, dict(me, Origin='https://evil.example'))[0], 403)
        self.assertEqual(self.call('/api/tasks', {'title': 'x'}, dict(me, **{'Sec-Fetch-Site': 'cross-site'}))[0], 403)
        host = self.base.split('//')[1]
        self.assertEqual(self.call('/api/tasks', {'title': 'x'}, dict(me, Origin=f'http://{host}', **{'Sec-Fetch-Site': 'same-origin'}))[0], 201)
        # agents with the token are not browsers
        self.assertEqual(self.call('/api/tasks', raw=b'{"title":"x","body":"## Goal\\nx\\n## Done-test\\ny\\n## Steps\\n1. z"}', headers={'Authorization': 'Bearer tok', 'Content-Type': 'application/json'})[0], 201)

    def test_the_auth_probe_says_who_may_write(self):
        self.assertEqual(self.call('/api/tasks/auth')[0], 401)
        self.assertEqual(self.call('/api/tasks/auth', headers={'Authorization': 'Bearer tok'}), (200, {'writer': 'agent'}))

    def test_no_owner_configured_means_no_header_writes(self):
        self.server.TASKS_OWNER = ''
        self.assertEqual(self.call('/api/tasks', {'title': 'x'}, {'Tailscale-User-Login': ''})[0], 401)

    def test_an_agent_title_is_a_plain_checkable_outcome(self):
        tok = {'Authorization': 'Bearer tok'}
        dt = '## Goal\nx\n## Done-test\n5 downloads.\n## Steps\n1. z'
        for bad in ('Sleep Like CEO: first strangers via a TikTok slideshow series (needs your OK)',
                    'Wedding mail: finish text, send tonight', 'Merge PR #7', 'Decide: keep or park'):
            code, err = self.call('/api/tasks', {'title': bad, 'body': dt}, tok)
            self.assertEqual(code, 400, bad)
        self.assertIn('Done-test', self.call('/api/tasks', {'title': 'Ship it'}, tok)[1]['error'])
        self.assertIn('Steps', self.call('/api/tasks', {'title': 'Ship it', 'body': '## Goal\nx\n## Done-test\ny'}, tok)[1]['error'])
        code, t = self.call('/api/tasks', {'title': 'Sleep Like CEO: 5 downloads from 3 TikTok slideshows', 'body': dt}, tok)
        self.assertEqual(code, 201)
        self.assertEqual(self.call(f"/api/tasks/{t['id']}", {'title': 'Do it tomorrow'}, tok)[0], 400)
        self.assertEqual(self.call(f"/api/tasks/{t['id']}", {'status': 'done'}, tok)[0], 200)
        # his own writes from the page are never checked
        self.assertEqual(self.call('/api/tasks', {'title': 'koupit stebla (zítra)'}, self.me())[0], 201)

    def test_the_task_api_round_trip(self):
        _, t = self.call('/api/tasks', {'title': 'Ship it', 'project': 'weddings', 'priority': 1}, self.me())
        code, err = self.call('/api/tasks', {'title': 'x', 'due': 'soon'}, self.me())
        self.assertEqual(code, 400)
        self.assertIn('due', err['error'])
        self.assertEqual(self.call(f"/api/tasks/{t['id']}", {'status': 'doing'}, self.me())[1]['status'], 'doing')
        self.assertEqual(self.call(f"/api/tasks/{t['id']}/comments", {'text': 'started'}, self.me())[0], 201)
        self.assertEqual(self.call(f"/api/tasks/{t['id']}/sessions", {'session_id': 'Claude Code:abc'}, self.me())[0], 201)
        code, got = self.call(f"/api/tasks/{t['id']}")
        self.assertEqual((code, got['status'], len(got['comments'])), (200, 'doing', 1))
        self.assertEqual(got['sessions'][0]['state'], None)  # not live anywhere
        self.assertEqual(self.call('/api/tasks?project=weddings')[1]['tasks'][0]['id'], t['id'])
        self.assertEqual(self.call('/api/tasks/projects')[1]['projects'], [{'project': 'weddings', 'open': 1}])
        self.assertEqual(self.call('/api/tasks/999')[0], 404)
        self.assertEqual(self.call('/api/tasks/999', {'status': 'done'}, self.me())[0], 404)

    def test_the_beacon_name_serves_the_task_app(self):
        _, page = self.call('/', headers={'Host': 'beacon.example.ts.net'})
        self.assertIn(b'tasks.js', page)
        self.assertIn(b'content="Beacon"', page)
        _, page = self.call('/', headers={'Host': 'usage'})
        self.assertNotIn(b'tasks.js', page)

    def test_every_name_serves_the_pages_as_they_are(self):
        web = os.path.join(os.path.dirname(self.server.__file__), '..', 'web')
        for host, path, rel in (('beacon', '/', 'tasks.html'), ('usage', '/', 'index.html'),
                                ('cloud:4829', '/', 'index.html'), ('beacon', '/new', 'tasks.html'),
                                # each page links the other two by path, on any name
                                ('beacon', '/usage', 'index.html'), ('usage', '/tasks', 'tasks.html'),
                                ('beacon', '/decisions', 'questions.html'), ('beacon', '/team', 'team.html')):
            _, page = self.call(path, headers={'Host': host})
            with open(os.path.join(web, rel), 'rb') as f:  # as they are, apart from the versions on their CSS and JS links
                self.assertEqual(re.sub(rb'\?v=\w+"', b'"', page), re.sub(rb'\?v=\w+"', b'"', f.read()), f'{host}{path}')


class TasksCliTest(LiveHub, unittest.TestCase):
    """The `tasks` command against a real hub: what an agent runs."""

    def run_cli(self, *argv):
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        loader = importlib.machinery.SourceFileLoader('tasks_cli', os.path.join(ROOT, 'client', 'tasks'))
        spec = importlib.util.spec_from_loader('tasks_cli', loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        cli.URL = self.base
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    def setUp(self):
        super().setUp()
        self.env = {k: os.environ.get(k) for k in ('TASKS_TOKEN', 'CLAUDE_CODE_SESSION_ID')}
        os.environ['TASKS_TOKEN'] = 'tok'
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'abc-123'

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_an_agent_files_works_and_closes_a_task(self):
        out = self.run_cli('add', 'Save Studio', '-p', 'clean-the-snow', '--priority', '1', '-t', 'claude', '-b', '## Goal\nx\n## Done-test\nIt works.\n## Steps\n1. y')
        self.assertTrue(out.startswith('#1  Save Studio  [clean-the-snow]  urgent  #claude'), out)
        t = self.server.Handler.tasks.get(1)
        self.assertEqual([s['session_id'] for s in t['sessions']], ['Claude Code:abc-123'])
        self.assertIn('Save Studio', self.run_cli('ls', '--claude'))
        self.run_cli('set', '1', 'due=2026-10-09', 'priority=')
        self.assertEqual((self.server.Handler.tasks.get(1)['due'], self.server.Handler.tasks.get(1)['priority']), ('2026-10-09', None))
        self.run_cli('done', '1', '-m', '✅ saved, version 41')
        t = self.server.Handler.tasks.get(1)
        self.assertEqual((t['status'], t['comments'][0]['text'], t['comments'][0]['author']),
                         ('done', '✅ saved, version 41', 'Claude Code abc-123'))
        self.assertEqual(self.run_cli('ls').strip(), 'nothing open')
        self.assertEqual(self.run_cli('auth').strip(), 'agent')

    def test_a_task_for_this_sessions_own_work_is_filed_doing(self):
        out = self.run_cli('add', 'Fix the sync', '--doing', '-b', '## Goal\nx\n## Done-test\nIt works.\n## Steps\n1. y')
        self.assertIn('doing', out)
        t = self.server.Handler.tasks.get(1)
        self.assertEqual((t['status'], [s['session_id'] for s in t['sessions']]), ('doing', ['Claude Code:abc-123']))

    def test_taking_a_task_links_the_session_doing_it(self):
        self.run_cli('add', 'Fix the sync', '--no-link', '-b', '## Goal\nx\n## Done-test\nIt works.\n## Steps\n1. y')
        self.run_cli('doing', '1')
        t = self.server.Handler.tasks.get(1)
        self.assertEqual((t['status'], [s['session_id'] for s in t['sessions']]), ('doing', ['Claude Code:abc-123']))

    def test_progress_goes_in_the_status_not_the_text(self):
        self.run_cli('add', 'Save Studio', '-p', 'x', '-b', '## Goal\nSaved.\n## Steps\n1. Open Studio, press Save.\n## Done-test\nVersion 41 exists.')
        for bad in ('body=## ✅ Done\nsaved', 'body=- [x] saved', 'title=DONE: Save Studio', 'body=⚠️ Partial: half'):
            with self.assertRaises(SystemExit) as e:
                self.run_cli('set', '1', bad)
            self.assertIn('refused', str(e.exception), bad)
        with self.assertRaises(SystemExit):
            self.run_cli('add', 'y', '-p', 'x', '-b', '✅ Done: already')
        self.run_cli('set', '1', 'status=done', 'body=## ✅ Done\nsaved')  # status in the same call: fine
        self.run_cli('set', '1', 'body=Everything is done once the PR lands.')  # a sentence, not a claim
        self.run_cli('set', '1', 'body=## Done-test\nversion 41 exists')  # the done-test heading the skill asks for
        self.run_cli('set', '1', 'body=## Done when\nversion 41 exists')
        self.assertEqual(self.server.Handler.tasks.get(1)['status'], 'done')

    def test_ls_hides_parked_unless_asked(self):
        self.run_cli('add', 'live one', '-p', 'x', '--due', '2026-01-01', '-b', '## Goal\nx\n## Done-test\nIt works.\n## Steps\n1. y')
        self.run_cli('add', 'set aside', '-p', 'x', '--due', '2026-01-01', '-t', 'parked', '-b', '## Goal\nx\n## Done-test\nIt works.\n## Steps\n1. y')
        self.assertNotIn('set aside', self.run_cli('ls', '--due', 'today'))
        self.assertIn('live one', self.run_cli('ls', '--due', 'today'))
        self.assertIn('set aside', self.run_cli('ls', '--parked'))

    def test_without_a_token_nothing_is_written(self):
        os.environ['TASKS_TOKEN'] = 'wrong'
        with self.assertRaises(SystemExit) as e:
            self.run_cli('add', 'x', '-p', 'personal')
        self.assertIn('not allowed', str(e.exception))


class TasksCliRetryTest(unittest.TestCase):
    """The client against a flaky hub: a restart or a blip is waited out, a dead hub is a clean exit."""

    def cli(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader('tasks_cli', os.path.join(ROOT, 'client', 'tasks'))
        spec = importlib.util.spec_from_loader('tasks_cli', loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        cli.RETRY_WAITS = (0, 0, 0)
        return cli

    def flaky(self, codes):
        """A server answering with the given status codes in turn, then 200 {"ok": n}."""
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        seen = []

        class H(BaseHTTPRequestHandler):
            def answer(self):
                seen.append(self.command)
                code = codes[len(seen) - 1] if len(seen) <= len(codes) else 200
                body = json.dumps({'ok': len(seen)} if code == 200 else {'error': 'busy'}).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            do_GET = do_POST = answer

            def log_message(self, *a):
                pass
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f'http://127.0.0.1:{httpd.server_address[1]}', seen

    def test_a_hub_restart_is_waited_out(self):
        cli = self.cli()
        cli.URL, seen = self.flaky([502, 503])
        self.assertEqual(cli.call('/api/tasks', auth=False), {'ok': 3})
        cli.URL, seen = self.flaky([502])
        self.assertEqual(cli.call('/api/tasks/1/comments', {'text': 'x'}, auth=False), {'ok': 2})  # the hub never saw it

    def test_a_dead_hub_is_a_clean_exit_after_the_retries(self):
        import socket
        cli = self.cli()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cli.URL = f'http://127.0.0.1:{sock.getsockname()[1]}'  # bound, not listening: refused
        with self.assertRaises(SystemExit) as e:
            cli.call('/api/tasks', auth=False)
        self.assertIn('hub unreachable', str(e.exception))

    def test_a_server_error_says_so(self):
        cli = self.cli()
        cli.URL, seen = self.flaky([500])
        with self.assertRaises(SystemExit) as e:
            cli.call('/api/tasks', auth=False)
        self.assertIn('server error 500', str(e.exception))
        self.assertEqual(seen, ['GET'])  # a hub bug is not retried


if __name__ == '__main__':
    unittest.main()
