"""Feedback from a page's button: a task with everything the page knows, and a card the dispatcher runs."""
import base64
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hub'))

from feedback import Feedback, project_for  # noqa: E402
from test_tasks import LiveHub  # noqa: E402

# a 1x1 png
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


class FeedbackApiTest(LiveHub, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.server.Handler.feedback = Feedback(self.server.Handler.tasks, self.server.Handler.questions)
        self.created, self.answered = [], []
        self.server.Handler.questions.on_created = self.created.append
        self.server.Handler.questions.on_answered = self.answered.append

    def send(self, body, headers=None):
        return self.call('/api/feedback', body, self.me() if headers is None else headers)

    def body(self, **kw):
        return {'note': 'This button is too small', 'picture': 'data:image/png;base64,' + base64.b64encode(PNG).decode(),
                'page': {'url': 'http://beacon/decisions', 'title': 'Decisions', 'viewport': '390×844', 'theme': 'light',
                         'device': 'iPhone'},
                'marks': [{'what': 'button.btn.primary', 'text': 'Answer'}],
                'taps': [{'at': '10:01', 'page': '/tasks', 'what': 'a “Decisions”'}],
                'errors': [{'at': '10:02', 'what': 'GET /api/questions failed: 502'}], **kw}

    def test_his_feedback_files_a_task_and_a_card_the_dispatcher_picks_up(self):
        code, got = self.send(self.body())
        self.assertEqual((code, got['project']), (201, 'beacon'))
        t = self.call(f"/api/tasks/{got['task']}")[1]
        self.assertEqual(t['title'], 'Decisions: This button is too small')
        self.assertIn('feedback', t['tags'])
        for want in ('## Goal', '## Done-test', '## Steps', 'http://beacon/decisions', '390×844', 'button.btn.primary',
                     '„Answer“', 'a “Decisions”', '502', '/api/questions/file/'):
            self.assertIn(want, t['body'])
        pending = self.call('/api/questions?status=answered&pending=1')[1]['questions']
        self.assertEqual([q['id'] for q in pending], [got['question']])
        q = pending[0]
        self.assertEqual((q['kind'], q['task_id'], q['answer'], q['answered_via']), ('feedback', got['task'], 'This button is too small', 'page'))
        self.assertTrue(q['see'][0]['picture'].startswith('/api/questions/file/'))
        self.assertEqual(self.call('/api/questions?status=open')[1]['questions'], [])  # never a card he has to answer
        self.assertEqual(self.created, [])  # no "new decision" for his own feedback
        # the run fetches the picture with the agents' token
        code, raw = self.call(q['see'][0]['picture'], headers={'Authorization': 'Bearer tok'})
        self.assertEqual((code, raw), (200, PNG))

    def test_feedback_never_counts_as_his_ok_for_a_risky_action(self):
        _, got = self.send(self.body(note='delete the old cards and deploy'))
        self.server.Handler.tasks.link(got['task'], 'sess-1')
        for category in ('delete', 'live'):
            self.assertIsNone(self.server.Handler.questions.approval('sess-1', category))

    def test_a_page_of_another_app_goes_to_that_apps_project(self):
        _, got = self.send(self.body(page={'url': 'http://beacon/budget/month', 'title': 'Money'}))
        self.assertEqual(got['project'], 'budget')

    def test_refused_without_his_login_or_anything_to_act_on(self):
        self.assertEqual(self.send(self.body(), headers={})[0], 401)
        self.assertEqual(self.send(self.body(), headers=dict(self.me(), **{'Sec-Fetch-Site': 'cross-site'}))[0], 403)
        self.assertEqual(self.send({'note': ' ', 'page': {}})[0], 400)
        self.assertEqual(self.send(self.body(picture='data:image/svg+xml;base64,PHN2Zz4='))[0], 400)
        self.assertEqual(self.send(self.body(picture='data:image/png;base64,!!'))[0], 400)

    def test_an_agents_feedback_is_not_his_hand(self):
        _, got = self.send(self.body(), headers={'Authorization': 'Bearer tok'})
        self.assertEqual(self.call(f"/api/questions/{got['question']}")[1]['answered_via'], 'cli')

    def test_no_task_is_left_without_its_card(self):
        qs = self.server.Handler.questions
        real = qs.create
        qs.create = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db locked'))
        try:
            self.assertEqual(self.send(self.body())[0], 500)
        finally:
            qs.create = real
        tasks = self.call('/api/tasks?status=all')[1]['tasks']
        self.assertEqual([t['status'] for t in tasks if 'feedback' in t['tags']], ['dropped'])

    def test_a_picture_alone_is_enough(self):
        code, got = self.send(self.body(note=''))
        self.assertEqual(code, 201)
        q = self.call(f"/api/questions/{got['question']}")[1]
        self.assertEqual(q['answer'], 'Fix what I marked on the screenshot.')


class ProjectTest(unittest.TestCase):

    def test_project_follows_the_page(self):
        self.assertEqual([project_for(p) for p in ('/', '/team', '/budget', '/budget/x', '/budgets', '/lighthouse/today')],
                         ['beacon', 'beacon', 'budget', 'budget', 'beacon', 'lighthouse'])


if __name__ == '__main__':
    unittest.main()
