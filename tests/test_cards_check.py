"""cards-check: what the model is shown for each card, and the model-call cap (task 1989)."""
import importlib.machinery
import importlib.util
import os
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('BEACON_CONFIG', os.path.join(ROOT, 'config.example.json'))
_loader = importlib.machinery.SourceFileLoader('cards_check', os.path.join(ROOT, 'client', 'cards-check'))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
cc = importlib.util.module_from_spec(_spec)
_loader.exec_module(cc)

NOW = 1_800_000_000


def card(i, task, at, **kw):
    return {'id': i, 'task_id': task, 'created_at': at, 'status': 'open', 'question': f'card {i}', 'project': 'p', **kw}


class CardPrompt(unittest.TestCase):
    def setUp(self):
        self.machines = mock.patch.object(cc, 'machines_lines', return_value=['pi: online'])
        self.machines.start()
        self.addCleanup(self.machines.stop)

    def test_shows_task_notes_newer_cards_and_machines(self):
        old = card(1, 7, NOW - 2 * 86400, context='the Pi is offline')
        newer = card(2, 7, NOW - 3600, status='answered', answer='done')
        task = {'id': 7, 'status': 'doing', 'title': 'Fix the Pi', 'body': 'b',
                'comments': [{'at': NOW - 100, 'text': 'Pi back after replug'}]}
        text = cc.card_prompt([old], {7: task}, [old, newer], NOW)
        for want in ('?1 asked 2 days ago', 'the Pi is offline', 'task #7 [doing] Fix the Pi',
                     'Pi back after replug', 'newer card ?2 [answered]', '-> answer: done', 'pi: online'):
            self.assertIn(want, text)

    def test_age_alone_is_never_moot(self):
        self.assertIn('Age alone never makes a card moot', cc.CARD_RULES)


class Cap(unittest.TestCase):
    def test_no_model_call_past_the_cap(self):
        with mock.patch.object(cc, 'COST', [0] * cc.MAX_CALLS), mock.patch.object(cc.subprocess, 'run') as run:
            self.assertEqual(cc.judge('x', {1}), [])
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class Helpers(unittest.TestCase):
    def test_clip_collapses_space_and_cuts_with_an_ellipsis(self):
        self.assertEqual(cc.clip('a   b\n c', 20), 'a b c')
        self.assertEqual(cc.clip('abcdef', 4), 'abc…')
        self.assertEqual(cc.clip(None, 4), '')

    def test_ago_uses_hours_then_days(self):
        self.assertEqual(cc.ago(NOW - 3600 * 5, NOW), '5 h')
        self.assertEqual(cc.ago(NOW - 3600 * 72, NOW), '3 days')

    def test_day_of_a_missing_time_is_a_question_mark(self):
        self.assertEqual(cc.day(None), '?')
        self.assertRegex(cc.day(NOW), r'^\d{4}-\d\d-\d\d$')

    def test_task_lines_newest_notes_first_and_none_when_missing(self):
        self.assertEqual(cc.task_lines(None), ['   task: none'])
        task = {'id': 7, 'status': 'doing', 'title': 'T', 'body': 'B',
                'comments': [{'at': NOW - 100, 'text': 'old'}, {'at': NOW, 'text': 'new'}]}
        lines = cc.task_lines(task)
        self.assertIn('task #7 [doing] T', lines[0])
        self.assertIn('new', lines[2])
        self.assertIn('old', lines[3])

    def test_get_refuses_a_non_web_address(self):
        with mock.patch.object(cc, 'URL', 'file:///etc/passwd'), self.assertRaises(ValueError):
            cc.get('/x')
