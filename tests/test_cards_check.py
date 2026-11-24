"""cards-check: what the model is shown for each card, and the model-call cap (task 1989)."""
import importlib.machinery
import importlib.util
import json
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


def answered(stdout='', code=0):
    return mock.Mock(stdout=stdout, stderr='', returncode=code)


class Judge(unittest.TestCase):
    def setUp(self):
        for name in ('COST', 'FAILED'):
            patcher = mock.patch.object(cc, name, [])
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_keeps_only_asked_ids_with_evidence_and_counts_the_cost(self):
        out = {'total_cost_usd': 0.02, 'structured_output': {'decisions': [
            {'id': 1, 'evidence': 'PR merged'}, {'id': 2, 'evidence': ''}, {'id': 9, 'evidence': 'not asked'}]}}
        with mock.patch.object(cc.subprocess, 'run', return_value=answered(json.dumps(out))):
            self.assertEqual(cc.judge('x', {1, 2}), [{'id': 1, 'evidence': 'PR merged'}])
        self.assertEqual(cc.COST, [0.02])

    def test_a_broken_answer_counts_as_failed_and_judges_nothing(self):
        with mock.patch.object(cc.subprocess, 'run', return_value=answered('not json')):
            self.assertEqual(cc.judge('x', {1}), [])
        self.assertEqual(cc.FAILED, [1])


class Passes(unittest.TestCase):
    def test_check_cards_skips_the_last_hour_and_joins_each_decision_to_its_card(self):
        cards = [card(1, 7, NOW - 2 * 86400), card(2, None, NOW - 60), card(3, None, NOW - 86400, status='answered')]
        with mock.patch.object(cc, 'get', side_effect=lambda path: {'questions': cards} if 'questions' in path else {}), \
             mock.patch.object(cc, 'judge', return_value=[{'id': 1, 'evidence': 'done'}]) as judge, \
             mock.patch.object(cc, 'machines_lines', return_value=[]):
            decisions, n = cc.check_cards(NOW)
        self.assertEqual(n, 1)
        self.assertEqual(judge.call_args.args[1], {1})
        self.assertEqual(decisions[0]['card']['id'], 1)

    def test_old_tasks_skips_parked_recent_and_already_proposed(self):
        old = NOW - 30 * 86400
        tasks = [{'id': 1, 'status': 'todo', 'tags': [], 'updated_at': old},
                 {'id': 2, 'status': 'todo', 'tags': ['parked'], 'updated_at': old},
                 {'id': 3, 'status': 'todo', 'tags': [], 'updated_at': NOW},
                 {'id': 4, 'status': 'todo', 'tags': [], 'updated_at': old}]
        full = {1: {**tasks[0], 'title': 'a', 'project': 'p', 'created_at': old, 'body': '', 'comments': []},
                4: {**tasks[3], 'title': 'b', 'project': 'p', 'created_at': old, 'body': '',
                    'comments': [{'at': NOW - 86400, 'text': f'{cc.MARK}: proposed'}]}}
        with mock.patch.object(cc, 'get', return_value={'tasks': tasks}), \
             mock.patch.object(cc, 'tasks_by_id', return_value=full), \
             mock.patch.object(cc, 'judge', return_value=[]) as judge:
            cc.old_tasks(NOW)
        self.assertEqual(judge.call_args.args[1], {1})


class Writes(unittest.TestCase):
    def test_withdraw_notes_the_card_task(self):
        with mock.patch.object(cc.subprocess, 'run', return_value=answered()) as run:
            self.assertTrue(cc.withdraw({'card': {'id': 5, 'task_id': 7, 'question': 'q'}, 'evidence': 'merged'}))
        self.assertEqual([c.args[0][1] for c in run.call_args_list], ['withdraw', 'note'])

    def test_propose_park_files_one_task_one_card_and_a_note_each(self):
        moot = [{'id': 1, 'evidence': 'shipped'}, {'id': 2, 'evidence': 'dropped'}]
        by = {1: {'title': 'one'}, 2: {'title': 'two'}}
        with mock.patch.object(cc.subprocess, 'run', return_value=answered('#42  Old tasks')) as run:
            self.assertTrue(cc.propose_park(moot, by))
        self.assertEqual([c.args[0][1] for c in run.call_args_list], ['add', 'ask', 'note', 'note'])

    def test_propose_park_stops_when_no_task_was_filed(self):
        with mock.patch.object(cc.subprocess, 'run', return_value=answered('', 1)) as run:
            self.assertFalse(cc.propose_park([{'id': 1, 'evidence': 'x'}], {1: {'title': 't'}}))
        self.assertEqual(run.call_count, 1)


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
