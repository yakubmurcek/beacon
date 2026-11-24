"""Questions: the store, the API, the `tasks ask/wait/answer` commands and the chat hook."""
import contextlib
import datetime
import importlib.machinery
import importlib.util
import io
import json
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tasks import ROOT, LiveHub  # noqa: E402
from store import Store  # noqa: E402
from tasks import Tasks  # noqa: E402
from questions import Questions, kind_of  # noqa: E402

# These tests pin the gate's enforce behavior (task 1764); the shadow test below switches it off.
os.environ['GATE_MODE'] = 'enforce'


class QuestionsStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, 'u.db'))
        self.tasks = Tasks(self.store)
        self.qs = Questions(self.store, self.tasks)

    def tearDown(self):
        self.tmp.cleanup()

    def test_steps_are_checked(self):
        q = self.qs.create({'question': 'Top up Soniox?', 'options': ['Done'], 'steps': ['Open billing :: https://x.io/b', {'text': 'Pay'}]})
        self.assertEqual(q['steps'], [{'text': 'Open billing', 'url': 'https://x.io/b'}, {'text': 'Pay'}])
        q = self.qs.create({'question': 'Send it?', 'steps': ['Glenfair :: mailto:info@glenfair.cz?subject=Ahoj']})
        self.assertEqual(q['steps'][0]['url'], 'mailto:info@glenfair.cz?subject=Ahoj')
        for bad in (['x'] * 13, [{'text': ''}], [{'text': 'a', 'url': 'javascript:alert(1)'}], 'go'):
            with self.assertRaises(ValueError):
                self.qs.create({'question': 'Q?', 'steps': bad})

    def test_the_first_option_is_the_agents_default(self):
        q = self.qs.create({'question': 'Squash or merge?', 'options': ['Squash', {'label': 'Merge', 'detail': 'keeps history'}],
                            'kind': 'Merge Style', 'project': 'budget'})
        # A merge is a risk word and merge-style no low-risk kind: the hub makes it wait (task 1764).
        self.assertEqual((q['default_answer'], q['blocking'], q['status'], q['kind'], q['project']),
                         ('Squash', True, 'open', 'merge-style', 'budget'))
        self.assertEqual(q['blocking_reason'], 'risk word "merge" (live)')
        self.assertEqual(q['options'][1], {'label': 'Merge', 'detail': 'keeps history'})

    def test_shadow_mode_only_records_what_the_gate_would_do(self):
        with mock.patch.dict(os.environ, {'HOME': '/nonexistent'}):
            os.environ.pop('GATE_MODE', None)
            q = self.qs.create({'question': 'Send the mail?', 'options': ['Yes', 'No'], 'kind': 'naming'})
        self.assertEqual((q['blocking'], q['gate_blocking'], q['blocking_reason']), (False, True, 'risk word "send" (send)'))

    def test_only_a_low_risk_kind_without_risk_words_goes_on_with_its_default(self):
        ok = self.qs.create({'question': 'Call the helper fold or norm?', 'options': ['fold', 'norm'], 'kind': 'naming'})
        self.assertEqual((ok['blocking'], ok['blocking_reason']), (False, 'low-risk kind "naming", no risk word'))
        for fields, why in (
                ({'question': 'Which layout?', 'options': ['A', 'B'], 'kind': 'design'}, 'kind "design" is not on the low-risk list'),
                ({'question': 'Name the mail to Kovo "Hi"?', 'options': ['Hi', 'Hello'], 'kind': 'naming'}, 'risk word "mail" (send)'),
                ({'question': 'Smazat starou složku?', 'options': ['Ano', 'Ne'], 'kind': 'naming'}, 'risk word "smaz" (delete)'),
                ({'question': 'Cena 490 Kč?', 'options': ['Ano'], 'kind': 'tech-choice'}, 'risk word "cena" (pay)'),
                ({'question': 'Name it fold?', 'options': ['fold'], 'kind': 'naming', 'blocking': True}, 'the agent asked it as blocking')):
            q = self.qs.create(fields)
            self.assertEqual((q['blocking'], q['blocking_reason']), (True, why), fields)

    def test_not_now_is_never_an_option(self):
        # 2026-11-22: tapping "Not now" answered the card and woke an agent run only to learn he put it off;
        # the deck's Later button does that without waking anything.
        q = self.qs.create({'question': 'Replug the Pi?', 'options': ['Done', 'Not now', 'Later (Recommended)', 'ne teď'],
                            'default': 'Not now'})
        self.assertEqual((q['options'], q['default_answer']), ([{'label': 'Done'}], 'Done'))
        with self.qs.store.connect() as con:  # a card filed before the change loses it too
            con.execute('UPDATE questions SET options = ? WHERE id = ?', ('[{"label": "Done"}, {"label": "Not now"}]', q['id']))
        self.assertEqual(self.qs.get(q['id'])['options'], [{'label': 'Done'}])

    def test_a_recommended_option_is_the_default_wherever_it_is(self):
        q = self.qs.create({'question': 'Price?', 'options': ['$9', '$19 (Recommended)']})
        self.assertEqual(q['default_answer'], '$19 (Recommended)')

    def test_no_default_means_the_agent_is_blocked(self):
        self.assertTrue(self.qs.create({'question': 'What is the client called?'})['blocking'])

    def test_bad_fields_name_themselves(self):
        for fields, name in (({}, 'question'), ({'question': 'x' * 401}, 'question'),
                             ({'question': 'x', 'options': 'a'}, 'options'), ({'question': 'x', 'task_id': 99}, 'task_id')):
            with self.assertRaises(ValueError) as e:
                self.qs.create(fields)
            self.assertTrue(str(e.exception).startswith(name), e.exception)

    def test_an_answer_is_logged_with_whether_it_was_the_default(self):
        a = self.qs.create({'question': 'Squash?', 'options': ['Squash (Recommended)', 'Merge'], 'kind': 'merge'})
        b = self.qs.create({'question': 'Squash again?', 'options': ['Squash', 'Merge'], 'kind': 'merge'})
        self.qs.answer(a['id'], {'answer': 'squash', 'standing': True})
        self.qs.answer(b['id'], {'answer': 'Merge', 'note': 'keep the commits this time'})
        a, b = self.qs.get(a['id']), self.qs.get(b['id'])
        self.assertEqual((a['matched_default'], a['standing'], a['answered_via']), (True, True, 'page'))
        self.assertEqual((b['matched_default'], b['answer_note']), (False, 'keep the commits this time'))
        self.assertEqual(self.qs.kinds(), [{'kind': 'merge', 'answered': 2, 'took_default': 1, 'standing': 1, 'last': b['answered_at']}])
        with self.assertRaises(ValueError):
            self.qs.answer(a['id'], {'answer': 'again'})

    def test_an_answer_can_be_undone_until_the_agent_took_it(self):
        q = self.qs.create({'question': 'x?', 'options': ['a', 'b']})
        self.qs.answer(q['id'], {'answer': 'b'})
        self.assertEqual(self.qs.reopen(q['id'])['status'], 'open')
        self.qs.answer(q['id'], {'answer': 'a'})
        self.qs.applied(q['id'])
        with self.assertRaises(ValueError):
            self.qs.reopen(q['id'])

    def test_a_question_answered_elsewhere_is_logged_once(self):
        f = {'question': 'Stripe?', 'options': ['Yes', 'No'], 'answer': 'Yes', 'via': 'chat', 'source': 'chat:s:t:0'}
        self.assertEqual(self.qs.create(f)['id'], self.qs.create(f)['id'])
        q = self.qs.list('answered')[0]
        self.assertEqual((q['answered_via'], q['matched_default'], bool(q['applied_at'])), ('chat', True, True))

    def test_a_question_on_a_task_shows_the_tasks_project_not_the_askers_folder(self):
        t = self.tasks.create({'title': 'Post the bench', 'project': 'budget'})
        q = self.qs.create({'question': 'Keep it public?', 'task_id': t['id'], 'project': 'beacon'})
        self.assertEqual(q['project'], 'budget')
        self.tasks.link(t['id'], 'Claude Code:s1')  # no --task: the session's own task decides too
        q = self.qs.create({'question': 'Post today?', 'project': 'beacon', 'session_id': 'Claude Code:s1'})
        self.assertEqual((q['task_id'], q['project']), (t['id'], 'budget'))
        q = self.qs.create({'question': 'Fix the hub?', 'task_id': t['id'], 'project': 'beacon', 'project_asked': True})
        self.assertEqual(q['project'], 'beacon')  # named with -p: kept

    def test_a_sessions_page_lists_only_what_that_session_asked(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'budget'})
        self.tasks.link(t['id'], 'Claude Code:one')
        self.tasks.comment(t['id'], '⏳ Needs you: monthly or yearly?', 'task-agent')
        self.assertEqual([q['question'] for q in self.qs.list('all', session='Claude Code:one')], ['monthly or yearly?'])
        self.assertEqual(self.qs.list('all', session='Claude Code:other'), [])

    def test_an_agents_note_on_a_task_asks_and_his_note_answers(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'budget'})
        self.tasks.link(t['id'], 'Claude Code:abc')
        self.tasks.comment(t['id'], '👉 PR https://github.com/o/r/pull/1', 'task-agent')  # not a question
        self.assertEqual(self.qs.list('all'), [])
        self.tasks.comment(t['id'], '⏳ Needs you: monthly or yearly?', 'task-agent')
        q = self.qs.list('open')[0]
        self.assertEqual((q['question'], q['task_id'], q['project'], q['kind'], q['blocking'], q['session_id']),
                         ('monthly or yearly?', t['id'], 'budget', 'task', True, 'Claude Code:abc'))
        self.tasks.comment(t['id'], '⏳ Needs you: actually, which currency?', 'task-agent')  # asks again
        self.assertEqual([(x['question'], x['status']) for x in self.qs.list('all')],
                         [('actually, which currency?', 'open'), ('monthly or yearly?', 'withdrawn')])
        self.tasks.comment(t['id'], 'CZK', 'owner')
        q = self.qs.list('answered')[0]  # not taken yet: the dispatcher resumes the task with it (task 1776)
        self.assertEqual((q['answer'], q['answered_via'], bool(q['applied_at'])), ('CZK', 'task', False))
        self.assertEqual([c['author'] for c in self.tasks.get(t['id'])['comments']].count('decisions'), 0)

    def test_every_question_lands_on_a_task(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'budget'})
        self.tasks.link(t['id'], 'Claude Code:s1')
        q = self.qs.create({'question': 'Monthly?', 'options': ['Yes'], 'session_id': 'Claude Code:s1'})
        self.assertEqual(q['task_id'], t['id'])  # the asking session's own task
        q = self.qs.create({'question': 'Which bank?', 'options': ['Fio'], 'session_id': 'Claude Code:s2', 'project': 'budget'})
        filed = self.tasks.get(q['task_id'])  # none: a small one holds it
        self.assertEqual((filed['title'], filed['tags'], filed['project'], [s['session_id'] for s in filed['sessions']]),
                         ('Decide: Which bank?', ['decision'], 'budget', ['Claude Code:s2']))
        self.assertTrue(filed['waiting_on_him'] == q['blocking'])
        logged = self.qs.create({'question': 'Dark mode?', 'answer': 'yes', 'session_id': 'Claude Code:s3'})
        self.assertIsNone(logged['task_id'])  # answered in chat already: nothing to resume
        self.qs.withdraw(q['id'], 'moot')
        self.assertEqual(self.tasks.get(filed['id'])['status'], 'dropped')  # nothing left on it
        self.qs.withdraw(self.qs.list('open', task=t['id'])[0]['id'])
        self.assertEqual(self.tasks.get(t['id'])['status'], 'todo')  # a real task stays

    def test_his_answer_becomes_a_task_note_once_taken_and_never_answers_again(self):
        t = self.tasks.create({'title': 'Pick a plan'})
        a = self.qs.create({'question': 'Monthly?', 'options': ['Yes', 'No'], 'task_id': t['id']})
        b = self.qs.create({'question': 'Which bank?', 'options': ['Fio'], 'task_id': t['id']})
        self.qs.answer(a['id'], {'answer': 'No'})
        self.assertEqual(self.tasks.get(t['id'])['comments'], [])  # undo still possible: no note yet
        self.qs.reopen(a['id'])
        self.qs.answer(a['id'], {'answer': 'Yes', 'note': 'cheaper'})
        self.assertEqual(self.qs.claim(a['id'], 'x')[0], True)
        self.assertEqual(self.qs.claim(a['id'], 'y')[0], False)
        notes = self.tasks.get(t['id'])['comments']
        self.assertEqual([(c['author'], c['text']) for c in notes], [('decisions', '✅ He decided ?1 „Monthly?“: Yes\ncheaper')])
        self.assertEqual(self.qs.get(b['id'])['status'], 'open')  # the note answered nothing else

    def test_closing_a_task_withdraws_its_open_questions(self):
        for status in ('done', 'dropped'):
            t = self.tasks.create({'title': f'Plan ({status})'})
            other = self.tasks.create({'title': 'Other'})
            answered = self.qs.create({'question': 'Monthly?', 'options': ['yes'], 'task_id': t['id']})
            self.qs.answer(answered['id'], {'answer': 'yes'})
            self.tasks.comment(t['id'], '⏳ Needs you: which currency?', 'task-agent')
            self.tasks.comment(other['id'], '⏳ Needs you: still here?', 'task-agent')
            self.tasks.update(t['id'], {'status': status})
            got = {q['question']: (q['status'], q['answer_note']) for q in self.qs.list('all') if q['task_id'] in (t['id'], other['id'])}
            self.assertEqual(got, {'which currency?': ('withdrawn', 'task closed'), 'Monthly?': ('answered', None),
                                   'still here?': ('open', None)}, status)
            self.tasks.update(t['id'], {'status': 'todo'})  # reopened: the question stays withdrawn
            self.assertEqual([q['status'] for q in self.qs.list('all', task=t['id']) if q['question'] == 'which currency?'], ['withdrawn'])
            self.tasks.update(other['id'], {'status': 'done'})

    def test_a_question_asked_on_a_closed_task_is_not_opened(self):
        t = self.tasks.create({'title': 'Dropped mid-run'})
        self.tasks.update(t['id'], {'status': 'dropped'})
        self.tasks.comment(t['id'], '⏳ Needs you: monthly or yearly?', 'task-agent')
        self.assertEqual(self.qs.list('all'), [])

    def test_a_withdrawal_never_overwrites_an_answer(self):
        q = self.qs.create({'question': 'x?', 'options': ['a']})
        stale = self.qs.get(q['id'])
        self.qs.answer(q['id'], {'answer': 'a'})
        self.qs.get = lambda qid: stale  # read before the answer landed (another request thread)
        self.qs.withdraw(q['id'], 'task closed')
        del self.qs.get
        self.assertEqual((self.qs.get(q['id'])['status'], self.qs.get(q['id'])['answer']), ('answered', 'a'))

    def test_his_note_then_the_close_keeps_his_answer(self):
        t = self.tasks.create({'title': 'Pick a plan'})
        self.tasks.comment(t['id'], '⏳ Needs you: monthly or yearly?', 'task-agent')
        self.tasks.comment(t['id'], 'yearly', 'owner')
        self.tasks.update(t['id'], {'status': 'done'})
        q = self.qs.list('all')[0]
        self.assertEqual((q['status'], q['answer']), ('answered', 'yearly'))

    def test_on_start_questions_of_tasks_already_closed_are_withdrawn(self):
        self.tasks.on_close = None  # as before task_closed existed
        closed, live = self.tasks.create({'title': 'Closed'}), self.tasks.create({'title': 'Live'})
        self.tasks.comment(closed['id'], '⏳ Needs you: a?', 'task-agent')
        self.tasks.comment(live['id'], '⏳ Needs you: b?', 'task-agent')
        free = self.qs.create({'question': 'No task?'})
        self.tasks.update(closed['id'], {'status': 'dropped'})
        self.assertEqual(len(self.qs.list('open')), 3)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            qs = Questions(self.store, self.tasks)
        self.assertIn('withdrew 1', out.getvalue())
        self.assertEqual(sorted(q['question'] for q in qs.list('open')), ['No task?', 'b?'])
        self.assertEqual(qs.get(free['id'])['status'], 'open')
        self.assertEqual(qs.withdraw_closed(), 0)  # idempotent

    def test_the_log_is_newest_first_and_searchable(self):
        for i in range(3):
            q = self.qs.create({'question': f'q{i}?', 'options': ['yes'], 'kind': 'naming' if i else 'merge'})
            self.qs.answer(q['id'], {'answer': 'yes', 'note': 'Příliš' if i == 2 else None})
        self.assertEqual([x['question'] for x in self.qs.list('answered')], ['q2?', 'q1?', 'q0?'])
        self.assertEqual([x['question'] for x in self.qs.list('answered', kind='naming')], ['q2?', 'q1?'])
        self.assertEqual([x['question'] for x in self.qs.list('answered', q='prilis')], ['q2?'])

    def test_kinds_are_slugs(self):
        self.assertEqual((kind_of('Merge PR'), kind_of(''), kind_of('  Copy / Tone ')), ('merge-pr', 'other', 'copy-tone'))


class QuestionsApiTest(LiveHub, unittest.TestCase):
    AGENT = {'Authorization': 'Bearer tok'}

    def test_agents_ask_he_answers(self):
        self.assertEqual(self.call('/api/questions', {'question': 'x?'})[0], 401)
        code, q = self.call('/api/questions', {'question': 'Ship it?', 'options': ['Yes', 'No']}, self.AGENT)
        self.assertEqual((code, q['default_answer'], q['asked_by']), (201, 'Yes', 'agent'))
        self.assertEqual(self.call(f"/api/questions/{q['id']}/answer", {'answer': 'No'})[0], 401)
        code, q = self.call(f"/api/questions/{q['id']}/answer", {'answer': 'No', 'standing': True}, self.me())
        self.assertEqual((code, q['status'], q['answered_via'], q['standing']), (200, 'answered', 'page', True))
        self.assertEqual(self.call(f"/api/questions/{q['id']}/answer", {'answer': 'Yes'}, self.me())[0], 400)
        code, q = self.call(f"/api/questions/{q['id']}/applied", {}, self.AGENT)
        self.assertTrue(q['applied_at'])
        self.assertEqual(self.call('/api/questions?status=answered')[1]['questions'][0]['id'], q['id'])
        self.assertEqual(self.call('/api/questions/kinds')[1]['kinds'][0]['answered'], 1)
        self.assertEqual(self.call('/api/questions/999')[0], 404)
        self.assertEqual(self.call('/api/questions?since=soon')[0], 400)

    def test_closing_a_task_through_the_api_withdraws_its_question(self):
        _, t = self.call('/api/tasks', {'title': 'Pick a plan', 'body': '## Goal\nx\n## Done-test\npicked\n## Steps\n1. z'}, self.AGENT)
        self.call(f"/api/tasks/{t['id']}/comments", {'text': '⏳ Needs you: monthly or yearly?', 'author': 'task-agent'}, self.AGENT)
        self.assertEqual(len(self.call('/api/questions?status=open')[1]['questions']), 1)
        self.assertEqual(self.call(f"/api/tasks/{t['id']}", {'status': 'done'}, self.AGENT)[0], 200)
        self.assertEqual(self.call('/api/questions?status=open')[1]['questions'], [])
        q = self.call('/api/questions?status=withdrawn')[1]['questions'][0]
        self.assertEqual((q['task_id'], q['answer_note']), (t['id'], 'task closed'))

    def test_an_agent_cannot_claim_his_hands(self):
        # his own hands (the page, a task note) count as approval for risky actions; the token may not say so itself
        _, q = self.call('/api/questions', {'question': 'Push live?', 'options': ['Yes']}, self.AGENT)
        code, out = self.call(f"/api/questions/{q['id']}/answer", {'answer': 'Yes', 'via': 'page'}, self.AGENT)
        self.assertEqual(code, 400, out)
        self.assertEqual(self.call(f"/api/questions/{q['id']}")[1]['status'], 'open')
        code, out = self.call('/api/questions', {'question': 'Deploy?', 'answer': 'Yes', 'via': 'task'}, self.AGENT)
        self.assertEqual(code, 400, out)
        code, q = self.call(f"/api/questions/{q['id']}/answer", {'answer': 'Yes', 'via': 'chat'}, self.AGENT)
        self.assertEqual((code, q['answered_via']), (200, 'chat'))  # the asking session's own chat stays the agent's to log

    def test_another_site_cannot_answer_with_his_login(self):
        _, q = self.call('/api/questions', {'question': 'x?', 'options': ['a']}, self.AGENT)
        code, _ = self.call(f"/api/questions/{q['id']}/answer", {'answer': 'a'}, dict(self.me(), **{'Sec-Fetch-Site': 'cross-site'}))
        self.assertEqual(code, 403)

    def test_the_page_is_served_at_beacon_decisions(self):
        for path, host in (('/decisions', 'beacon'), ('/decisions', '127.0.0.1')):
            code, body = self.call(path, headers={'Host': host})
            self.assertEqual(code, 200)
            self.assertIn('<title>Beacon · Decisions</title>'.encode(), body)
        self.assertIn('<title>Beacon · Tasks</title>'.encode(), self.call('/', headers={'Host': 'beacon'})[1])
        import http.client
        for path, to, code in (('/questions', '/decisions', 301), ('/decisions/', '/decisions', 302)):
            c = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1])
            c.request('GET', path, headers={'Host': 'beacon'})
            r = c.getresponse()
            self.assertEqual((r.status, r.getheader('Location')), (code, to), path)
            c.close()


class QuestionsCliTest(LiveHub, unittest.TestCase):
    """What an agent runs: `tasks ask`, then `tasks wait` in the background."""

    def setUp(self):
        super().setUp()
        self.env = {k: os.environ.get(k) for k in ('TASKS_TOKEN', 'CLAUDE_CODE_SESSION_ID', 'HOME', 'TASKS_HOOKS')}
        os.environ['TASKS_TOKEN'] = 'tok'
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'abc-123'
        os.environ['TASKS_HOOKS'] = '1'  # the hooks run whatever started the tests
        loader = importlib.machinery.SourceFileLoader('tasks_cli', os.path.join(ROOT, 'client', 'tasks'))
        spec = importlib.util.spec_from_loader('tasks_cli', loader)
        self.cli = importlib.util.module_from_spec(spec)
        loader.exec_module(self.cli)
        self.cli.URL = self.base

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def run_cli(self, *argv, stdin=None):
        if argv and argv[0] == 'ask' and '-c' not in argv and '--context' not in argv:
            argv = (*argv, '-c', '- 🧪 what it is about')  # every real ask carries one (the hub refuses it bare)
        out = io.StringIO()
        saved = sys.stdin
        sys.stdin = io.StringIO(stdin) if stdin is not None else saved
        try:
            with contextlib.redirect_stdout(out):
                self.cli.main(list(argv))
        finally:
            sys.stdin = saved
        return out.getvalue()

    def test_ask_then_wait_until_he_answers(self):
        out = self.run_cli('ask', 'Squash or rebase?', '-o', 'Squash :: one commit', '-o', 'Rebase', '-k', 'tech-choice', '-p', 'budget')
        self.assertTrue(out.startswith('?1  Squash or rebase?  [budget]  tech-choice  going on with: Squash\nhttp://beacon/decisions#/q/1'), out)
        self.assertIn("Don't run `tasks wait`", out)
        q = self.server.Handler.questions.get(1)
        self.assertEqual((q['session_id'], q['options'][0]), ('Claude Code:abc-123', {'label': 'Squash', 'detail': 'one commit'}))

        def answer_soon():
            time.sleep(0.3)
            self.server.Handler.questions.answer(1, {'answer': 'Rebase', 'note': 'keep history', 'standing': True})
        threading.Thread(target=answer_soon).start()
        out = self.run_cli('wait', '?1', '--every', '0.1')
        self.assertTrue(out.startswith('?1 answered: Rebase\nnote: keep history\n(not your default'), out)
        self.assertIn('standing rule', out)
        self.assertTrue(self.server.Handler.questions.get(1)['applied_at'])

    # Where an answer is carried out (2026-11-22, ai-brain claude-code-session-size): same work stays with the
    # asking session, other work or a big idle session goes to a fresh one.
    def transcript(self, size, ago):
        cfg = os.path.join(self.tmp.name, 'claude')
        os.makedirs(os.path.join(cfg, 'projects', '-root-projects-x'), exist_ok=True)
        saved = os.environ.get('CLAUDE_CONFIG_DIR')
        os.environ['CLAUDE_CONFIG_DIR'] = cfg
        self.addCleanup(lambda: os.environ.pop('CLAUDE_CONFIG_DIR', None) if saved is None
                        else os.environ.__setitem__('CLAUDE_CONFIG_DIR', saved))
        at = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(time.time() - ago))
        rows = [{'type': 'assistant', 'timestamp': at, 'message': {'usage': {
                    'input_tokens': 10, 'cache_read_input_tokens': size - 10, 'cache_creation_input_tokens': 0}}},
                {'type': 'assistant', 'isSidechain': True, 'timestamp': at, 'message': {'usage': {'input_tokens': 5}}},
                {'type': 'user', 'timestamp': at, 'message': {'content': 'hi'}}]
        with open(os.path.join(cfg, 'projects', '-root-projects-x', 'abc-123.jsonl'), 'w') as f:
            f.write('\n'.join(json.dumps(r) for r in rows) + '\n')

    def ask_and_answer(self, project, task_project):
        t = self.server.Handler.tasks.create({'title': 'Clean up old PRs', 'project': task_project})
        out = self.run_cli('ask', 'Merge the icon PR?', '-o', 'Yes', '-o', 'No', '-k', 'tech-choice', '-p', project,
                           '--task', str(t['id']), '--blocking', '--no-see', 'a tested code change')
        qid = int(out.split()[0].lstrip('?'))
        self.server.Handler.questions.answer(qid, {'answer': 'No'})
        return qid, t, out

    def test_an_answer_for_another_project_goes_to_a_fresh_session(self):
        qid, t, out = self.ask_and_answer('ayrus', 'beacon')
        self.assertIn('runs in a fresh session (it is work in ayrus', out)  # said when asking, so the card carries it
        out = self.run_cli('wait', str(qid), '--every', '0.1')
        self.assertIn('Handed to a fresh session, because it is work in ayrus', out)
        self.assertIn(f'tasks note {t["id"]}', out)
        q = self.server.Handler.questions.get(qid)
        self.assertIsNone(q['applied_at'])  # not taken here: the dispatcher's run takes it
        self.assertTrue(q['handoff_at'])
        self.assertIsNone(q['waited_at'])

    def test_same_work_stays_in_the_asking_session(self):
        self.transcript(120_000, 3 * 3600)  # small: idle or not, its context is worth keeping
        qid, t, out = self.ask_and_answer('beacon', 'beacon')
        self.assertNotIn('fresh session', out)
        out = self.run_cli('wait', str(qid), '--every', '0.1')
        self.assertTrue(out.startswith(f'?{qid} answered: No'), out)
        self.assertTrue(self.server.Handler.questions.get(qid)['applied_at'])

    def test_a_big_session_still_working_keeps_its_answer(self):
        self.transcript(350_000, 120)  # cache warm, context load-bearing: continue
        qid, t, _ = self.ask_and_answer('beacon', 'beacon')
        self.run_cli('wait', str(qid), '--every', '0.1')
        self.assertTrue(self.server.Handler.questions.get(qid)['applied_at'])

    def test_a_big_idle_session_hands_its_answer_on_without_waking(self):
        self.transcript(350_000, 2 * 3600)
        self.assertEqual(self.cli.session_size()[0], 350_000)  # the sidechain row is not this session's size
        qid, t, _ = self.ask_and_answer('beacon', 'beacon')
        with self.assertRaises(SystemExit) as e:
            self.run_cli('wait', str(qid), '--every', '0.1', '--timeout', '1')
        self.assertEqual(e.exception.code, 2)  # exits silently: printing would wake a 350k session to read it
        q = self.server.Handler.questions.get(qid)
        self.assertIsNone(q['applied_at'])
        self.assertIn('350k tokens and sat idle 120 min', q['handoff_reason'])

    def test_a_folder_the_session_drifted_into_is_not_other_work(self):
        # 2026-11-22 review: a beacon task's session asking from linux-setup without -p asks about its own work.
        t = self.server.Handler.tasks.create({'title': 'Power-off fix', 'project': 'beacon'})
        out = self.run_cli('ask', 'Restart the unit now?', '-o', 'Yes', '-o', 'No', '-k', 'tech-choice', '--task',
                           str(t['id']), '--blocking')
        qid = int(out.split()[0].lstrip('?'))
        self.server.Handler.questions.answer(qid, {'answer': 'No'})
        self.run_cli('wait', str(qid), '--every', '0.1')
        self.assertTrue(self.server.Handler.questions.get(qid)['applied_at'])

    def ask_low_risk(self, project, task_project):
        t = self.server.Handler.tasks.create({'title': 'Old PRs', 'project': task_project})
        out = self.run_cli('ask', 'Call it fold or norm?', '-o', 'fold', '-o', 'norm', '-k', 'naming', '-p', project,
                           '--task', str(t['id']))
        qid = int(out.split()[0].lstrip('?'))
        q = self.server.Handler.questions.get(qid)
        self.assertEqual((q['blocking'], q['gate_blocking']), (False, False))
        return qid, t

    def test_his_own_default_on_a_low_risk_card_needs_nothing_anywhere(self):
        qid, t = self.ask_low_risk('ayrus', 'beacon')
        self.server.Handler.questions.answer(qid, {'answer': 'fold'})
        out = self.run_cli('wait', str(qid), '--every', '0.1')
        self.assertIn('(your default)', out)  # it went on with fold already: nothing to hand anyone
        self.assertIsNone(self.server.Handler.questions.get(qid)['handoff_at'])

    def test_another_pick_on_a_low_risk_card_goes_fresh_and_says_who_switches(self):
        qid, t = self.ask_low_risk('ayrus', 'beacon')
        self.server.Handler.questions.answer(qid, {'answer': 'norm'})
        out = self.run_cli('wait', str(qid), '--every', '0.1')
        self.assertIn('Handed to a fresh session, because it is work in ayrus', out)
        self.assertIn("already went on with 'fold'; he picked 'norm', so the fresh session switches it", out)
        self.assertIsNone(self.server.Handler.questions.get(qid)['applied_at'])

    def test_a_card_the_gate_only_records_tells_a_holding_asker_to_say_so(self):
        # 2026-11-22: the 14 incident cards were merges and money with the gate in shadow: told "go on", yet held.
        t = self.server.Handler.tasks.create({'title': 'Old PRs', 'project': 'beacon'})
        with mock.patch.dict(os.environ, {}):
            os.environ.pop('GATE_MODE', None)
            out = self.run_cli('ask', 'Merge the icon PR?', '-o', 'Yes, merge', '-o', 'No', '-k', 'design', '-p', 'ayrus',
                               '--task', str(t['id']), '--no-see', 'test')
        self.assertIn('tasks block 1', out)  # the same card turns blocking, never a second one
        qid = int(out.split()[0].lstrip('?'))
        self.server.Handler.questions.answer(qid, {'answer': 'Yes, merge'})  # the default it was told to go on with
        out = self.run_cli('wait', str(qid), '--every', '0.1')
        self.assertIn('(your default)', out)  # never a second merge from a fresh run
        self.assertIsNone(self.server.Handler.questions.get(qid)['handoff_at'])

    def test_a_big_idle_session_that_must_switch_hears_it(self):
        self.transcript(350_000, 2 * 3600)
        qid, t = self.ask_low_risk('beacon', 'beacon')
        self.server.Handler.questions.answer(qid, {'answer': 'norm'})
        out = self.run_cli('wait', str(qid), '--every', '0.1')
        self.assertIn('Stop work that depends on your default', out)  # not quiet: it would build on fold

    def test_a_hub_that_refuses_handoffs_still_gets_the_answer_carried_out(self):
        # A client updated before its hub (no /handoff route): after a few tries the asker carries it out itself.
        qid, t, _ = self.ask_and_answer('ayrus', 'beacon')
        real = self.cli.call
        def call(path, *args, **kw):
            if path.endswith('/handoff'):
                sys.exit('tasks: no such question')
            return real(path, *args, **kw)
        self.cli.call = call
        self.addCleanup(setattr, self.cli, 'call', real)
        out = self.run_cli('wait', str(qid), '--every', '0.05')
        self.assertTrue(out.startswith(f'?{qid} answered: No'), out)
        self.assertTrue(self.server.Handler.questions.get(qid)['applied_at'])

    def test_an_undo_clears_the_handoff(self):
        qid, t, _ = self.ask_and_answer('ayrus', 'beacon')
        qs = self.server.Handler.questions
        qs.handoff(qid, 'it is work in ayrus')
        q = qs.reopen(qid)
        self.assertEqual((q['status'], q['handoff_at'], q['handoff_reason']), ('open', None, None))

    def test_a_handoff_never_takes_an_answer_someone_took(self):
        qid, t, _ = self.ask_and_answer('ayrus', 'beacon')
        qs = self.server.Handler.questions
        self.assertTrue(qs.claim(qid, 'chat')[0])
        self.assertIsNone(qs.handoff(qid, 'late')['handoff_at'])

    def test_a_hands_ask_must_carry_steps_with_a_link(self):
        # 2026-11-22: the Paid Apps card was ids and a 409 error; he wants "a direct link and clear steps".
        with self.assertRaises(SystemExit) as e:
            self.run_cli('ask', "Sign Apple's Paid Apps agreement?", '-o', 'Done', '-o', 'Not now', '-k', 'setup')
        self.assertIn('--step', str(e.exception.code))
        self.assertIsNone(self.server.Handler.questions.get(1))
        url = 'https://appstoreconnect.apple.com/business'
        self.run_cli('ask', "Sign Apple's Paid Apps agreement?", '-o', 'Done', '-o', 'Not now', '-k', 'setup',
                     '--step', f'Open Business :: {url}', '--step', 'Tap View and Agree on Paid Apps')
        q = self.server.Handler.questions.get(1)
        self.assertEqual(q['steps'], [{'text': 'Open Business', 'url': url}, {'text': 'Tap View and Agree on Paid Apps'}])
        self.assertFalse(q['needs_see'])  # the step's link is what he opens
        # A rewrite replaces the checklist, never doubles it.
        self.run_cli('steps', '1', f'Open Agreements :: {url}', 'Accept', 'Add your bank')
        self.assertEqual([x['text'] for x in self.server.Handler.questions.get(1)['steps']],
                         ['Open Agreements', 'Accept', 'Add your bank'])

    def test_a_look_question_must_carry_what_to_look_at(self):
        # 2026-11-22: "put the leaving card live on the site?" with nothing to see sent him back to Claude.
        with self.assertRaises(SystemExit) as e:
            self.run_cli('ask', 'alex.example: put the leaving card live on the site?', '-o', 'Yes', '-o', 'Not yet')
        self.assertIn('--see', str(e.exception.code))
        self.assertIsNone(self.server.Handler.questions.get(1))
        png = os.path.join(self.tmp.name, 'card.png')
        with open(png, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\nfake')
        self.run_cli('ask', 'Put the leaving card live on the site?', '-o', 'Yes', '-o', 'Not yet',
                     '--see', 'Preview :: http://alex-site/exit', '--see', png)
        q = self.server.Handler.questions.get(1)
        self.assertEqual(q['see'][0], {'url': 'http://alex-site/exit', 'label': 'Preview'})
        pic = q['see'][1]['picture']
        self.assertRegex(pic, r'^/api/questions/file/[0-9a-f]{16}\.png$')
        self.assertFalse(q['needs_see'])
        # The screenshot can show anything: his login or the token only.
        self.assertEqual(self.call(pic)[0], 401)
        status, body = self.call(pic, headers=self.me())
        self.assertEqual((status, body), (200, b'\x89PNG\r\n\x1a\nfake'))
        self.assertEqual(self.call('/api/questions/file/../u.db', headers=self.me())[0], 404)
        # Nothing to see is said out loud; a link found later is added with `tasks see`.
        self.run_cli('ask', 'Merge the README typo fix?', '-o', 'Yes', '--no-see', 'one word in a README')
        self.assertFalse(self.server.Handler.questions.get(2)['needs_see'])
        self.run_cli('see', '2', 'https://github.com/x/y/pull/3', 'http://beacon/shot.png')
        self.assertEqual(self.server.Handler.questions.get(2)['see'],
                         [{'url': 'https://github.com/x/y/pull/3'}, {'picture': 'http://beacon/shot.png'}])
        with self.assertRaises(SystemExit):
            self.run_cli('see', '2', 'javascript:alert(1)')
        # A voice sample plays on the card (2026-11-23: "I need the voice actually there to be able to play it").
        mp3 = os.path.join(self.tmp.name, 'voice.mp3')
        with open(mp3, 'wb') as f:
            f.write(b'ID3fake')
        self.run_cli('ask', 'Keep this voice?', '-o', 'Keep it', '--see', 'Voice :: ' + mp3, '--see', 'https://x.example/v.m4a')
        see = self.server.Handler.questions.get(3)['see']
        self.assertRegex(see[0]['audio'], r'^/api/questions/file/[0-9a-f]{16}\.mp3$')
        self.assertEqual(see[0]['label'], 'Voice')
        self.assertEqual(see[1], {'audio': 'https://x.example/v.m4a'})
        self.assertEqual(self.call(see[0]['audio'], headers=self.me()), (200, b'ID3fake'))

    def test_a_look_question_from_elsewhere_is_marked_on_its_card(self):
        q = self.server.Handler.questions.create({'question': 'Merge the redesign?', 'options': ['Yes'], 'project': 'x'})
        self.assertTrue(q['needs_see'])
        self.assertFalse(self.server.Handler.questions.create({'question': 'Renew OpenCode Go?', 'options': ['Yes'],
                                                               'project': 'x'})['needs_see'])
        with self.assertRaises(ValueError):
            self.server.Handler.questions.create({'question': 'x?', 'see': ['javascript:alert(1)']})

    def test_wait_gives_up_on_a_timeout_or_a_withdrawn_question(self):
        self.run_cli('ask', 'x?', '-o', 'a', '--blocking')
        with self.assertRaises(SystemExit) as e:
            self.run_cli('wait', '1', '--timeout', '0.2', '--every', '0.1')
        self.assertEqual(e.exception.code, 2)
        self.run_cli('withdraw', '1', '-m', 'found it in the README')
        with self.assertRaises(SystemExit) as e:
            self.run_cli('wait', '1', '--every', '0.1')
        self.assertEqual(e.exception.code, 3)

    def test_the_log_and_the_kinds(self):
        self.run_cli('ask', 'a?', '-o', 'yes', '-k', 'merge', '--see', 'https://github.com/o/r/pull/1')
        self.run_cli('answer', '1', 'yes', '--always')
        self.assertIn('answered: yes', self.run_cli('questions', '--answered'))
        self.assertEqual(self.run_cli('questions'), 'nothing open\n')
        self.assertIn("merge            answered   1  took the agent's default   1  standing 1", self.run_cli('questions', '--kinds'))

    def test_an_answer_in_the_asking_chat_is_logged_as_taken(self):
        self.run_cli('ask', 'Ship it?', '-o', 'Yes', '-o', 'No', '-k', 'release', '--no-see', 'a backend release')
        self.run_cli('answer', '1', 'No', '--chat', '-m', 'wait for Monday')
        q = self.server.Handler.questions.get(1)
        self.assertEqual((q['status'], q['answer'], q['answered_via'], q['matched_default']), ('answered', 'No', 'chat', False))
        self.assertTrue(q['applied_at'])  # the asking session has it: questions-applier must not run it again
        self.assertEqual(self.server.Handler.questions.list('answered', pending=True), [])

    def test_a_chat_answer_is_logged_by_the_hook(self):
        ev = {'session_id': 'sess-1', 'tool_use_id': 'tu-1', 'cwd': '/nonexistent', 'tool_name': 'AskUserQuestion',
              'tool_input': {'questions': [
                  {'question': 'Stripe account?', 'header': 'Stripe', 'options': [{'label': 'Existing', 'description': 'keys in the vault'}, {'label': 'New'}]},
                  {'question': 'Price?', 'header': 'Pricing', 'options': [{'label': '$9'}, {'label': '$19 (Recommended)'}]}]},
              'tool_response': {'answers': {'Stripe account?': 'New', 'Price?': '$19 (Recommended)'}}}
        self.run_cli('hook-ask', stdin=json.dumps(ev))
        self.run_cli('hook-ask', stdin=json.dumps(ev))  # a retried hook logs nothing twice
        rows = self.server.Handler.questions.list('answered')
        self.assertEqual(sorted((q['kind'], q['answer'], q['matched_default'], q['answered_via']) for q in rows),
                         [('pricing', '$19 (Recommended)', True, 'chat'), ('stripe', 'New', False, 'chat')])
        self.assertEqual(rows[0]['session_id'], 'Claude Code:sess-1')

    def test_the_hook_reads_a_text_result_and_never_fails(self):
        ev = {'session_id': 's', 'tool_input': {'questions': [{'question': 'Go?', 'options': [{'label': 'Yes'}]}]},
              'tool_response': 'Your questions have been answered: "Go?"="Yes". You can now continue.'}
        self.run_cli('hook-ask', stdin=json.dumps(ev))
        self.assertEqual(self.server.Handler.questions.list('answered')[0]['answer'], 'Yes')
        self.run_cli('hook-ask', stdin='not json')  # nothing raised


    def test_the_next_step_box_question_goes_to_the_page_and_a_click_answers_it(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        tasks = self.server.Handler.tasks
        t = tasks.create({'title': 'Discord watcher', 'status': 'doing'})
        tasks.link(t['id'], 'Claude Code:sb')
        ev = {'session_id': 'sb', 'tool_use_id': 'tu-9', 'cwd': '/nonexistent', 'tool_name': 'mcp__ccd_turn__wrap_up',
              'tool_input': {'recap': 'The checker works.', 'ask': 'Raise the daily limit to 16?',
                             'actions': [{'label': 'Raise to 16'}, {'label': 'Keep 8'}]}}
        self.run_cli('hook-ask', stdin=json.dumps(ev))
        self.run_cli('hook-ask', stdin=json.dumps(ev))  # a retried hook files nothing twice
        rows = self.server.Handler.questions.list('open')
        self.assertEqual(len(rows), 1)
        q = rows[0]
        self.assertEqual((q['question'], q['default_answer'], q['task_id'], q['kind'], q['session_id']),
                         ('Raise the daily limit to 16?', 'Raise to 16', t['id'], 'next-step', 'Claude Code:sb'))
        self.assertEqual([o['label'] for o in q['options']], ['Raise to 16', 'Keep 8'])
        self.assertIn('The checker works.', q['context'])
        self.assertIn(f"#{t['id']} Discord watcher", q['context'])  # tied (task 1776): his answer on the page resumes it
        self.run_cli('hook-prompt', stdin=json.dumps({'session_id': 'sb', 'prompt': 'keep 8', 'cwd': '/nonexistent'}))
        q = self.server.Handler.questions.get(q['id'])
        self.assertEqual((q['status'], q['answer'], q['answered_via']), ('answered', 'Keep 8', 'chat'))

    def test_a_box_answered_on_the_page_is_told_to_the_session(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        ev = {'session_id': 'sp', 'tool_use_id': 'tu-1', 'tool_name': 'mcp__ccd_turn__wrap_up',
              'tool_input': {'ask': 'Merge it?', 'actions': '[{"action": "merge"}, {"label": "Close it"}]'}}
        self.run_cli('hook-ask', stdin=json.dumps(ev))
        q = self.server.Handler.questions.list('open')[0]
        self.assertEqual([o['label'] for o in q['options']], ['Merge', 'Close it'])
        self.server.Handler.questions.answer(q['id'], {'answer': 'Close it', 'note': 'after the demo'})
        out = self.run_cli('hook-prompt', stdin=json.dumps({'session_id': 'sp', 'prompt': 'jak to jde?', 'cwd': '/nonexistent'}))
        ctx = json.loads(out)['hookSpecificOutput']['additionalContext']
        self.assertIn('Close it', ctx)
        self.assertIn('after the demo', ctx)
        self.assertTrue(self.server.Handler.questions.get(q['id'])['applied_at'])
        self.assertNotIn('next-step box', self.run_cli('hook-prompt', stdin=json.dumps({'session_id': 'sp', 'prompt': 'x', 'cwd': '/nonexistent'})))

    def test_a_typed_reply_or_a_second_box_never_leaves_a_question_behind(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        ev = {'session_id': 'st', 'tool_use_id': 'tu-1', 'tool_name': 'mcp__ccd_turn__wrap_up',
              'tool_response': 'Shown in the next-step box. Your closing message...',
              'tool_input': {'ask': 'Ship it?', 'actions': [{'label': 'Yes'}, {'label': 'No'}]}}
        self.run_cli('hook-ask', stdin=json.dumps(ev))
        self.run_cli('hook-ask', stdin=json.dumps({**ev, 'tool_use_id': 'tu-2', 'tool_input': {'ask': 'Ship it now?'}}))
        qs = {q['question']: q['status'] for q in self.server.Handler.questions.list('all')}
        self.assertEqual(qs, {'Ship it?': 'withdrawn', 'Ship it now?': 'open'})
        out = self.run_cli('hook-prompt', stdin=json.dumps({'session_id': 'st', 'prompt': 'jo, ale az v pondeli', 'cwd': '/nonexistent'}))
        ctx = json.loads(out)['hookSpecificOutput']['additionalContext']
        self.assertIn('still open', ctx)
        self.assertIn('--chat', ctx)

    def test_a_box_the_app_dropped_is_not_filed(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        self.run_cli('hook-ask', stdin=json.dumps({
            'session_id': 'sd', 'tool_use_id': 'tu-1', 'tool_name': 'mcp__ccd_turn__wrap_up',
            'tool_response': 'The commit action is no longer available, so the question was dropped.',
            'tool_input': {'ask': 'Commit?', 'actions': [{'action': 'commit'}]}}))
        self.assertEqual(self.server.Handler.questions.list('all'), [])

    def test_a_box_without_a_question_or_after_tasks_ask_files_nothing(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        base = {'session_id': 'sn', 'tool_use_id': 'tu-1', 'tool_name': 'mcp__ccd_turn__wrap_up'}
        self.run_cli('hook-ask', stdin=json.dumps({**base, 'tool_input': {'recap': 'Done.'}}))
        path = os.path.join(os.environ['HOME'], 't.jsonl')
        with open(path, 'w') as f:
            f.write(json.dumps({'type': 'user', 'message': {'content': 'zvedni limit?'}}) + '\n')
            f.write(json.dumps({'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'input': {'command': 'tasks ask "Limit?" -o 16 -o 8'}}]}}) + '\n')
        self.run_cli('hook-ask', stdin=json.dumps({**base, 'transcript_path': path,
                                                   'tool_input': {'ask': 'Limit?', 'actions': [{'label': '16'}]}}))
        self.assertEqual(self.server.Handler.questions.list('all'), [])
        os.environ['TASKS_HOOKS'] = '0'  # an automation's box is never filed
        self.run_cli('hook-ask', stdin=json.dumps({**base, 'tool_use_id': 'tu-2', 'tool_input': {'ask': 'Go?'}}))
        os.environ['TASKS_HOOKS'] = '1'
        self.assertEqual(self.server.Handler.questions.list('all'), [])

    def _turn(self, *tool_uses, notify=False):
        path = os.path.join(os.environ['HOME'], f'turn-{time.time()}.jsonl')
        with open(path, 'w') as f:
            f.write(json.dumps({'type': 'user', 'message': {'content': 'udelej to'}}) + '\n')
            for name, inp in tool_uses:
                if notify:  # a background agent finished mid-turn: not his message
                    f.write(json.dumps({'type': 'user', 'message': {'content': '<task-notification>done</task-notification>'}}) + '\n')
                f.write(json.dumps({'type': 'assistant', 'message': {'content': [
                    {'type': 'tool_use', 'name': name, 'input': inp}]}}) + '\n')
        return path

    def test_a_warning_reply_lands_the_plane_in_a_task(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        tasks = self.server.Handler.tasks
        t = tasks.create({'title': 'Rename the questions page', 'status': 'doing'})
        tasks.link(t['id'], 'Claude Code:sw')
        ev = {'session_id': 'sw', 'last_assistant_message': '⚠️ The rename is not done yet.',
              'transcript_path': self._turn(('Bash', {'command': 'git push'}))}
        out = json.loads(self.run_cli('hook-stop', stdin=json.dumps(ev)))
        self.assertEqual(out['decision'], 'block')
        self.assertIn('tasks handoff ID', out['reason'])
        self.assertIn(f"#{t['id']} (doing) Rename the questions page", out['reason'])
        ok = {**ev, 'transcript_path': self._turn(('Bash', {'command': f'tasks note {t["id"]} "queued on devbox"'}),
                                                  ('Bash', {'command': 'ls'}), notify=True)}
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(ok)), '')
        took = {**ev, 'transcript_path': self._turn(('Bash', {'command': f'tasks doing {t["id"]}'}))}
        self.assertIn('tasks handoff', self.run_cli('hook-stop', stdin=json.dumps(took)))  # taking it says nothing left
        chat = {**ev, 'session_id': 'nothing-linked'}
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(chat)), '')  # a chat, not work
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps({**ev, 'stop_hook_active': True})), '')  # never twice

    PLAN = ('✅ You can do this cleanly.\n\n**Setup (about 20 minutes, in this order):**\n\n'
            '1. 📱 **Reset the spare phone** (Android is easier).\n'
            '2. 📶 **Turn Wi-Fi off and use mobile data only**.\n'
            '3. ✉️ **Make a new email at proton.me** on that phone.\n'
            '4. 📌 **Sign up for Pinterest with that email only.**\n\n'
            '👉 **Next step:** reset the spare phone now.')

    def test_hands_plan_counts_orders_not_reports(self):
        self.assertEqual(self.cli.hands_plan(self.PLAN), 4)
        self.assertEqual(self.cli.hands_plan('1. Otevři porkbun.com\n2. Klikni na Email\n3. Přidej `x`\n- Pošli mi kód'), 3)
        done = '✅ Live.\n\n- **Fixed** the card\n- Added a test\n- The hub restarted\n\n👉 Next step: reload Decisions'
        self.assertLessEqual(self.cli.hands_plan(done), 1)  # what the agent did, then one next step: not a plan
        self.assertEqual(self.cli.hands_plan('```\n1. run this\n2. run that\n3. run more\n```'), 0)  # a quoted example

    def test_a_plan_for_him_becomes_a_task_and_a_card(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        ev = {'session_id': 'idea', 'last_assistant_message': self.PLAN,
              'transcript_path': self._turn(('Bash', {'command': 'ls'}))}
        out = json.loads(self.run_cli('hook-stop', stdin=json.dumps(ev)))
        self.assertEqual(out['decision'], 'block')
        self.assertIn('tasks add', out['reason'])
        self.assertIn('--step', out['reason'])
        filed = {**ev, 'transcript_path': self._turn(('Bash', {'command': 'tasks add "Pinterest: 10 pins" --doing -b -'}))}
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(filed)), '')
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps({**ev, 'stop_hook_active': True})), '')  # never twice
        t = self.server.Handler.tasks.create({'title': 'Pinterest test account', 'status': 'doing'})
        self.server.Handler.tasks.link(t['id'], 'Claude Code:idea')
        self.assertNotIn('tasks add', self.run_cli('hook-stop', stdin=json.dumps(ev)))  # already in a task

    def test_a_question_reply_must_be_on_the_page(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        t = self.server.Handler.tasks.create({'title': 'Discord watcher', 'status': 'doing'})
        self.server.Handler.tasks.link(t['id'], 'Claude Code:sq')
        ev = {'session_id': 'sq', 'last_assistant_message': '❓ Mám zvednout limit na 16?',
              'transcript_path': self._turn(('Bash', {'command': 'ls'}))}
        out = json.loads(self.run_cli('hook-stop', stdin=json.dumps(ev)))
        self.assertIn('tasks ask', out['reason'])
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(
            {**ev, 'transcript_path': self._turn(('Bash', {'command': 'tasks ask "Limit?" -o 16 -o 8'}))})), '')
        box = {'session_id': 'sq', 'tool_use_id': 'tu-1', 'tool_name': 'mcp__ccd_turn__wrap_up',
               'tool_response': 'Shown in the next-step box.', 'tool_input': {'ask': 'Limit?', 'actions': [{'label': '16'}]}}
        self.run_cli('hook-ask', stdin=json.dumps(box))  # filed: the .box file says so
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(ev)), '')
        lone = {**ev, 'session_id': 'no-task'}  # no task in hand: still filed, or it is a bare card (2026-11-22)
        self.assertIn('tasks ask', json.loads(self.run_cli('hook-stop', stdin=json.dumps(lone)))['reason'])

    def test_the_hub_serves_the_cli(self):
        code, body = self.call('/cli/tasks')
        self.assertEqual(code, 200)
        with open(os.path.join(ROOT, 'client', 'tasks'), 'rb') as f:
            self.assertEqual(body, f.read())
        self.assertEqual(self.call('/cli/settings.py')[0], 200)
        self.assertEqual(self.call('/cli/config.example.json')[0], 200)

    def test_an_open_question_never_makes_the_session_wait(self):
        # task 1884: a background `tasks wait` re-woke its session at every shell timeout; the dispatcher resumes it instead
        os.environ['HOME'] = tempfile.mkdtemp()
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'sx'
        out = self.run_cli('ask', 'Limit 16?', '-o', '16', '-o', '8')
        del os.environ['CLAUDE_CODE_SESSION_ID']
        self.assertIn("Don't run `tasks wait`", out)
        ev = {'session_id': 'sx', 'last_assistant_message': 'Hotovo, ptám se na stránce.'}
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(ev)), '')

    def test_a_resumed_run_sees_its_own_answer_as_its_to_do(self):
        self.run_cli('ask', 'Merge?', '-o', 'Yes', '-o', 'No', '--see', 'http://budget')
        self.server.Handler.questions.answer(1, {'answer': 'No'})
        self.server.Handler.questions.claim(1, 'answer-dispatcher')
        os.environ['BEACON_QID'] = '1'
        try:
            out = self.run_cli('wait', '1', '--timeout', '5')
        finally:
            del os.environ['BEACON_QID']
        self.assertIn('do it now', out)
        self.assertNotIn('do not repeat', out)

    def test_his_next_prompt_hands_in_answers_to_its_own_questions(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'so'
        self.run_cli('ask', 'Limit 16?', '-o', '16', '-o', '8')
        self.run_cli('ask', 'Rename it?', '-o', 'Yes', '-o', 'No')
        del os.environ['CLAUDE_CODE_SESSION_ID']
        qs = self.server.Handler.questions
        qs.answer(1, {'answer': '8'})
        qs.answer(2, {'answer': 'No'})
        qs.claim(2, 'answer-dispatcher')  # a fork already carried this one out
        ev = {'session_id': 'so', 'prompt': 'continue', 'cwd': '/nonexistent'}
        ctx = json.loads(self.run_cli('hook-prompt', stdin=json.dumps(ev)))['hookSpecificOutput']['additionalContext']
        self.assertIn('?1 answered: 8', ctx)
        self.assertIn('already carried it out in another run', ctx)
        self.assertEqual(qs.get(1)['taken_by'], 'Claude Code:so (chat)')
        again = self.run_cli('hook-prompt', stdin=json.dumps(ev))
        self.assertNotIn('?1 answered', again)  # told once
        self.assertNotIn('another run', again)

    def test_a_yes_no_box_on_another_question_is_its_own_question(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'syn'
        self.run_cli('ask', 'Rename the helper to fold?', '-o', 'Yes', '-o', 'No')
        del os.environ['CLAUDE_CODE_SESSION_ID']
        box = {'session_id': 'syn', 'tool_use_id': 'tu-7', 'tool_name': 'mcp__ccd_turn__wrap_up', 'cwd': '/nonexistent',
               'tool_response': 'Shown in the next-step box.',
               'tool_input': {'ask': 'Deploy to production?', 'recap': 'r', 'actions': [{'label': 'Yes'}, {'label': 'No'}]}}
        self.run_cli('hook-ask', stdin=json.dumps(box))
        qs = self.server.Handler.questions
        self.assertEqual(sorted(q['question'] for q in qs.list('open')), ['Deploy to production?', 'Rename the helper to fold?'])
        self.run_cli('hook-prompt', stdin=json.dumps({'session_id': 'syn', 'prompt': 'Yes', 'cwd': '/nonexistent'}))
        self.assertEqual(qs.get(1)['status'], 'open')  # his Yes was for the deploy, never the rename

    def test_an_answer_won_by_a_hook_that_died_is_handed_in_next_time(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'sd'
        self.run_cli('ask', 'Limit 16?', '-o', '16', '-o', '8')
        del os.environ['CLAUDE_CODE_SESSION_ID']
        qs = self.server.Handler.questions
        qs.answer(1, {'answer': '8'})
        qs.claim(1, 'Claude Code:sd (chat)')  # the claim landed, its reply never did
        ev = {'session_id': 'sd', 'prompt': 'continue', 'cwd': '/nonexistent'}
        ctx = json.loads(self.run_cli('hook-prompt', stdin=json.dumps(ev)))['hookSpecificOutput']['additionalContext']
        self.assertIn('?1 answered: 8', ctx)
        self.assertNotIn('?1 answered', self.run_cli('hook-prompt', stdin=json.dumps(ev)))  # once

    def test_a_done_reply_is_not_held_by_the_hubs_holder_task(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'sh'
        self.run_cli('ask', 'Call the helper fold or norm?', '-o', 'fold', '-o', 'norm', '-k', 'naming')
        del os.environ['CLAUDE_CODE_SESSION_ID']
        q = self.server.Handler.questions.get(1)
        self.assertIn('decision', self.server.Handler.tasks.get(q['task_id'])['tags'])
        ev = {'session_id': 'sh', 'last_assistant_message': '✅ Renamed.', 'cwd': '/nonexistent'}
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(ev)), '')  # no "close or drop it": that withdraws his card

    def test_his_prompt_leaves_other_work_to_the_dispatcher(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        tasks = self.server.Handler.tasks
        t = tasks.create({'title': 'Clean up repos', 'project': 'web', 'status': 'doing'})
        tasks.link(t['id'], 'Claude Code:sw')
        s, q = self.call('/api/questions', {'question': 'Archive the old budget repo?', 'options': ['Yes', 'No'],
                                            'kind': 'cleanup', 'project': 'budget', 'project_asked': True,
                                            'task_id': t['id'], 'session_id': 'Claude Code:sw', 'blocking': True},
                         {'Authorization': 'Bearer tok'})
        self.server.Handler.questions.answer(q['id'], {'answer': 'No'})
        self.run_cli('hook-prompt', stdin=json.dumps({'session_id': 'sw', 'prompt': 'continue', 'cwd': '/nonexistent'}))
        self.assertIsNone(self.server.Handler.questions.get(q['id'])['applied_at'])  # a fresh run in budget takes it

    def test_a_session_on_a_machine_outside_beacon_stays_out(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        self.cli.socket_host = lambda: 'laptop'  # config.example.json: the laptop is manual
        ev = {'session_id': 'sm', 'prompt': 'try this', 'cwd': '/nonexistent'}
        ctx = json.loads(self.run_cli('hook-prompt', stdin=json.dumps(ev)))['hookSpecificOutput']['additionalContext']
        self.assertIn('outside Beacon', ctx)
        self.assertEqual(self.run_cli('hook-prompt', stdin=json.dumps(ev)), '')  # told once
        stop = {'session_id': 'sm', 'last_assistant_message': '❓ Which one do you want?', 'cwd': '/nonexistent'}
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(stop)), '')  # no block, no card
        box = {'session_id': 'sm', 'tool_use_id': 'tu-1', 'tool_name': 'mcp__ccd_turn__wrap_up', 'cwd': '/nonexistent',
               'tool_input': {'ask': 'Grid or list?', 'recap': 'r', 'actions': [{'label': 'Grid'}, {'label': 'List'}]}}
        self.run_cli('hook-ask', stdin=json.dumps(box))
        self.assertEqual(self.server.Handler.questions.list('all'), [])
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'sm'
        self.addCleanup(os.environ.pop, 'CLAUDE_CODE_SESSION_ID', None)
        with self.assertRaises(SystemExit) as e:
            self.run_cli('ask', 'Grid or list?', '-o', 'Grid', '-o', 'List')
        self.assertIn('outside Beacon', str(e.exception.code))

    def test_wait_never_repeats_an_answer_already_carried_out(self):
        self.run_cli('ask', 'Merge?', '-o', 'Yes', '-o', 'No', '--see', 'http://budget')
        self.run_cli('answer', '1', 'No', '--chat')  # he clicked the button in the chat: taken there
        out = self.run_cli('wait', '1', '--timeout', '5')
        self.assertIn('Already carried out in this chat', out)
        self.assertNotIn('change course', out)

    def test_a_box_he_answered_on_the_page_is_replaced(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'sz'
        self.run_cli('ask', 'Smarter brake?', '-o', 'Yes', '-o', 'No')
        del os.environ['CLAUDE_CODE_SESSION_ID']
        path = os.path.join(os.environ['HOME'], 't.jsonl')
        box_at = datetime.datetime.utcfromtimestamp(time.time() - 60).isoformat() + 'Z'
        with open(path, 'w') as f:
            f.write(json.dumps({'type': 'assistant', 'timestamp': box_at, 'message': {'content': [
                {'type': 'tool_use', 'name': 'mcp__ccd_turn__wrap_up', 'input': {'recap': 'r', 'ask': 'Udělat brzdu chytřejší?', 'actions': [{'label': 'Yes'}, {'label': 'No'}]}}]}}) + '\n')
        ev = {'session_id': 'sz', 'last_assistant_message': 'Brzda běží.', 'transcript_path': path}
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(ev)), '')  # not answered yet: the box is right
        self.server.Handler.questions.answer(1, {'answer': 'Yes'})  # on the page
        reason = json.loads(self.run_cli('hook-stop', stdin=json.dumps(ev)))['reason']
        self.assertIn('wrap_up', reason)
        with open(path, 'a') as f:  # the session replaced the box
            f.write(json.dumps({'type': 'assistant', 'timestamp': datetime.datetime.utcnow().isoformat() + 'Z', 'message': {'content': [
                {'type': 'tool_use', 'name': 'mcp__ccd_turn__wrap_up', 'input': {'recap': 'Brzda běží.'}}]}}) + '\n')
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(ev)), '')

    def test_an_open_question_is_shown_in_the_box_and_never_filed_twice(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'sq'
        self.run_cli('ask', 'Finish labeling or stop at what is done?', '-o', 'Finish it', '-o', 'Stop at what is done')
        del os.environ['CLAUDE_CODE_SESSION_ID']
        self.server.Handler.questions.get(1)  # exists
        path = os.path.join(os.environ['HOME'], 't.jsonl')
        with open(path, 'w') as f:  # the turn ended with a box that has no question
            f.write(json.dumps({'type': 'assistant', 'timestamp': datetime.datetime.utcnow().isoformat() + 'Z', 'message': {'content': [
                {'type': 'tool_use', 'name': 'mcp__ccd_turn__wrap_up', 'input': {'recap': 'Brzda běží.'}}]}}) + '\n')
        ev = {'session_id': 'sq', 'last_assistant_message': '⚠️ Buildy čekají.', 'transcript_path': path}
        reason = json.loads(self.run_cli('hook-stop', stdin=json.dumps(ev)))['reason']
        self.assertIn('?1', reason)
        self.assertIn('Finish labeling or stop at what is done?', reason)
        box = {'session_id': 'sq', 'tool_use_id': 'tu-5', 'tool_name': 'mcp__ccd_turn__wrap_up',
               'tool_response': 'Shown in the next-step box.',
               'tool_input': {'ask': 'Dodělat označování, nebo skončit?', 'recap': 'r',
                              'actions': [{'label': 'Stop at what is done'}, {'label': 'Finish it'}]}}
        self.run_cli('hook-ask', stdin=json.dumps(box))  # same buttons: the same question, not a new one
        self.assertEqual(len(self.server.Handler.questions.list('all')), 1)
        with open(path, 'a') as f:
            f.write(json.dumps({'type': 'assistant', 'timestamp': datetime.datetime.utcnow().isoformat() + 'Z', 'message': {'content': [
                {'type': 'tool_use', 'name': 'mcp__ccd_turn__wrap_up', 'input': box['tool_input']}]}}) + '\n')
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(ev)), '')  # shown now
        self.run_cli('hook-prompt', stdin=json.dumps({'session_id': 'sq', 'prompt': 'Stop at what is done', 'cwd': '/nonexistent'}))
        q = self.server.Handler.questions.get(1)
        self.assertEqual((q['status'], q['answer'], q['answered_via']), ('answered', 'Stop at what is done', 'chat'))

    def test_handoff_keeps_his_go_ahead_but_not_for_an_agents_idea(self):
        tasks = self.server.Handler.tasks
        his = tasks.create({'title': 'His ask', 'status': 'doing'})
        idea = tasks.create({'title': 'Agent idea', 'status': 'doing', 'tags': ['auto']})
        self.assertIn('picks it up', self.run_cli('handoff', str(his['id']), 'run the eval'))
        self.assertIn('waits for his OK', self.run_cli('handoff', str(idea['id']), 'try it'))
        his, idea = tasks.get(his['id']), tasks.get(idea['id'])
        self.assertEqual((his['status'], his['tags']), ('todo', ['claude']))
        self.assertEqual((idea['status'], idea['tags']), ('todo', ['auto']))
        self.assertIn('run the eval', his['comments'][-1]['text'])
        parked = tasks.create({'title': 'Not now', 'status': 'doing', 'tags': ['parked']})
        self.assertIn('stays parked', self.run_cli('handoff', str(parked['id']), 'later'))
        self.assertEqual(tasks.get(parked['id'])['tags'], ['parked'])
        running = tasks.create({'title': 'Agent on it', 'status': 'doing', 'tags': ['claude']})
        with self.assertRaises(SystemExit):
            self.run_cli('handoff', str(running['id']), 'x')

    def test_a_done_reply_with_an_open_task_goes_back_once(self):
        os.environ['HOME'] = tempfile.mkdtemp()  # the hook remembers what it named under ~/.cache
        tasks = self.server.Handler.tasks
        took = tasks.create({'title': 'Fix the login', 'status': 'doing'})
        later = tasks.create({'title': 'Follow-up: docs'})
        other = tasks.create({'title': 'Someone else', 'status': 'doing'})
        tasks.link(took['id'], 'Claude Code:s9')
        tasks.link(later['id'], 'Claude Code:s9')
        tasks.link(other['id'], 'Claude Code:s8')
        ev = {'session_id': 's9', 'stop_hook_active': False, 'last_assistant_message': '✅ Login fixed.'}
        out = json.loads(self.run_cli('hook-stop', stdin=json.dumps(ev)))
        self.assertEqual(out['decision'], 'block')
        self.assertIn('#1 Fix the login', out['reason'])
        self.assertIn('#2 Follow-up: docs', out['reason'])
        self.assertNotIn('Someone else', out['reason'])
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps({**ev, 'stop_hook_active': True})), '')
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps(ev)), '')  # a later ✅: each task is named once
        third = tasks.create({'title': 'Taken later', 'status': 'doing'})
        tasks.link(third['id'], 'Claude Code:s9')
        out = json.loads(self.run_cli('hook-stop', stdin=json.dumps(ev)))
        self.assertIn('Taken later', out['reason'])
        self.assertNotIn('Fix the login', out['reason'])
        os.environ['TASKS_HOOKS'] = '0'  # forced off
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps({**ev, 'session_id': 's5'})), '')
        os.environ['TASKS_HOOKS'] = '1'
        self.assertEqual(self.run_cli('hook-stop', stdin=json.dumps({**ev, 'last_assistant_message': '⚠️ not yet'})), '')
        self.run_cli('hook-stop', stdin='not json')  # nothing raised

    def test_a_session_without_a_task_is_shown_the_ones_its_prompt_is_about(self):
        os.environ['HOME'] = tempfile.mkdtemp()
        tasks = self.server.Handler.tasks
        tasks.create({'title': 'Poslat Kovovi zprávu o reklamách', 'project': 'game'})
        tasks.create({'title': 'Koupit sůl na chodník', 'project': 'game'})
        ev = {'session_id': 's7', 'prompt': 'Pošli Kovovi tu zpravu o reklamach', 'cwd': '/nonexistent'}
        ctx = json.loads(self.run_cli('hook-prompt', stdin=json.dumps(ev)))['hookSpecificOutput']['additionalContext']
        self.assertIn('#1 [game] todo: Poslat Kovovi', ctx)
        self.assertNotIn('sůl', ctx)
        tasks.update(1, {'status': 'doing'})
        tasks.link(1, 'Claude Code:s7')
        self.assertEqual(self.run_cli('hook-prompt', stdin=json.dumps(ev)), '')  # it has its task: quiet
        ev = {**ev, 'session_id': 's6'}
        for _ in range(3):
            self.run_cli('hook-prompt', stdin=json.dumps(ev))
        self.assertEqual(self.run_cli('hook-prompt', stdin=json.dumps(ev)), '')  # 3 prompts at most
        self.run_cli('hook-prompt', stdin='not json')  # nothing raised

    def test_taking_a_parked_task_brings_it_back(self):
        t = self.server.Handler.tasks.create({'title': 'Old one', 'tags': ['parked', 'x']})
        self.run_cli('doing', str(t['id']))
        self.assertEqual(self.server.Handler.tasks.get(t['id'])['tags'], ['x'])

if __name__ == '__main__':
    unittest.main()


class HarvestTest(unittest.TestCase):
    """A question that links a PR shows the PR's screenshots on its card (2026-11-22: a slideshow)."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, 'u.db'))
        self.qs = Questions(self.store, Tasks(self.store))

    def tearDown(self):
        self.tmp.cleanup()

    def test_pr_screenshots_land_on_the_card_with_labels(self):
        import pictures
        png = b'\x89PNG\r\n\x1a\n' + b'x' * 20
        body = ('<img src="https://github.com/o/r/blob/feat/v1/docs/before/1-onboarding.png?raw=true">\n'
                '![x](https://github.com/o/r/blob/feat/v1/docs/pass4/a01-welcome.png?raw=true)')
        calls = []

        def gh(path, raw=False):
            calls.append(path)
            if path == 'repos/o/r/pulls/7':
                return json.dumps({'head': {'ref': 'feat/v1'}, 'body': body}).encode()
            if raw and 'ref=feat%2Fv1' in path:
                return png + path.encode()  # a different picture per path
            return None

        q = self.qs.create({'question': 'Merge the onboarding PR?', 'options': ['Yes'],
                            'context': 'PR: https://github.com/o/r/pull/7'})
        self.assertEqual(q['see'], [])  # harvesting is off in tests: asking never waits on GitHub
        with mock.patch.object(pictures, 'gh', gh):
            q = self.qs.harvest(q['id'])
        self.assertEqual([x.get('label') for x in q['see']], ['Before · Onboarding', 'Welcome'])
        self.assertTrue(all(x['picture'].startswith('/api/questions/file/') for x in q['see']))
        self.assertEqual(self.qs.file(q['see'][0]['picture'].rsplit('/', 1)[1])[1], 'image/png')
        # The branch with a slash was cut by the PR's own ref, not guessed: one fetch per picture.
        self.assertEqual(sum('contents/' in c for c in calls), 2)
        with mock.patch.object(pictures, 'gh', gh):
            self.assertEqual(len(self.qs.harvest(q['id'])['see']), 2)  # run again: no duplicates

    def test_a_reply_that_is_not_a_picture_is_skipped(self):
        import pictures
        with mock.patch.object(pictures, 'gh', lambda path, raw=False: b'{"message": "Not Found"}'):
            self.assertEqual(pictures.harvest('https://github.com/o/r/blob/main/a.png'), [])


class BadQuestionsTest(LiveHub, unittest.TestCase):
    """A card he can't answer as asked (2026-11-22): caught on every ask, his 👎 kept, all in one log."""
    AGENT = {'Authorization': 'Bearer tok'}

    def test_a_strict_ask_without_context_is_refused_with_the_reason(self):
        code, out = self.call('/api/questions', {'question': 'Ship it?', 'options': ['Yes', 'No'], 'strict': True}, self.AGENT)
        self.assertEqual(code, 400)
        self.assertIn('no context', out['error'])
        code, q = self.call('/api/questions', {'question': 'Ship it?', 'options': ['Yes', 'No'], 'strict': True,
                                               'context': '- 🚀 the new page, tested on the phone'}, self.AGENT)
        self.assertEqual((code, q['lint']), (201, []))
        code, out = self.call('/api/questions', {'question': 'Ship it?', 'options': ['Yes'], 'strict': True,
                                                 'context': '- 🚀 another page'}, self.AGENT)
        self.assertEqual(code, 400)
        self.assertIn('the same words as 1 open card', out['error'])

    def test_a_refused_ask_outside_any_task_files_no_task(self):
        before = len(self.call('/api/tasks')[1]['tasks'])
        code, _ = self.call('/api/questions', {'question': 'Ship it?', 'options': ['Yes', 'No'], 'strict': True,
                                               'session_id': 'lonely-session'}, self.AGENT)
        self.assertEqual(code, 400)
        self.assertEqual(len(self.call('/api/tasks')[1]['tasks']), before)
        for bad in ({'see_none': 'x' * 301}, {'created_at': 'soon'}):
            code, _ = self.call('/api/questions', {'question': 'Ship it?', 'options': ['Yes'], 'session_id': 'lonely-session',
                                                   'context': '- 🚀 the page', **bad}, self.AGENT)
            self.assertEqual(code, 400, bad)
            self.assertEqual(len(self.call('/api/tasks')[1]['tasks']), before, bad)

    def test_a_card_with_the_long_hub_link_is_refused(self):
        import questions
        with mock.patch.dict(questions.HUB, {'https': 'https://hub.example.ts.net'}):
            code, out = self.call('/api/questions', {'question': 'Try the talk page?', 'options': ['Works'], 'strict': True,
                                                     'context': '- 🎙️ the new voice', 'see': ['https://hub.example.ts.net/labs?talk']},
                                  self.AGENT)
        self.assertEqual(code, 400)
        self.assertIn('http://hub/<page>', out['error'])

    def test_a_task_note_card_is_never_lost_only_logged(self):
        _, t = self.call('/api/tasks', {'title': 'Pick a plan', 'body': '## Goal\nx\n## Done-test\npicked\n## Steps\n1. z'}, self.AGENT)
        self.call(f"/api/tasks/{t['id']}/comments", {'text': '⏳ Needs you: monthly or yearly?', 'author': 'task-agent'}, self.AGENT)
        q = self.call('/api/questions?status=open')[1]['questions'][0]
        self.assertTrue(any(w.startswith('no context') for w in q['lint']), q['lint'])
        self.assertTrue(any(w.startswith('nothing to tap') for w in q['lint']), q['lint'])
        self.assertEqual([x['id'] for x in self.call('/api/questions/bad')[1]['questions']], [q['id']])

    def test_a_task_note_splits_into_question_and_context(self):
        _, t = self.call('/api/tasks', {'title': 'Pick a plan', 'body': '## Goal\nx\n## Done-test\npicked\n## Steps\n1. z'}, self.AGENT)
        self.call(f"/api/tasks/{t['id']}/comments",
                  {'text': '⏳ Needs you: Monthly or yearly?\n- 💰 yearly is 2 months free', 'author': 'task-agent'}, self.AGENT)
        q = self.call('/api/questions?status=open')[1]['questions'][0]
        self.assertEqual(q['question'], 'Monthly or yearly?')
        self.assertIn('yearly is 2 months free', q['context'])

    def test_only_he_flags_and_the_asking_task_hears_it(self):
        _, t = self.call('/api/tasks', {'title': 'Pick a plan', 'body': '## Goal\nx\n## Done-test\npicked\n## Steps\n1. z'}, self.AGENT)
        _, q = self.call('/api/questions', {'question': 'Plan?', 'options': ['A', 'B'], 'context': '- x', 'task_id': t['id']}, self.AGENT)
        self.assertEqual(self.call(f"/api/questions/{q['id']}/flag", {}, self.AGENT)[0], 403)
        code, f = self.call(f"/api/questions/{q['id']}/flag", {'note': 'which plan?'}, self.me())
        self.assertEqual((code, f['flag'], bool(f['flagged_at'])), (200, 'which plan?', True))
        notes = self.call(f"/api/tasks/{t['id']}")[1]['comments']
        self.assertIn('👎 He flagged card', notes[-1]['text'])
        self.assertEqual(self.call('/api/questions/bad')[1]['questions'][0]['flag'], 'which plan?')
        _, f = self.call(f"/api/questions/{q['id']}/flag", {}, self.me())  # a second tap takes it back
        self.assertIsNone(f['flagged_at'])
        self.assertEqual(self.call('/api/questions/bad')[1]['questions'], [])
