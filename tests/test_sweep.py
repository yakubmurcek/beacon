"""tasks-sweep: what it may close."""
import importlib.machinery
import importlib.util
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
loader = importlib.machinery.SourceFileLoader('tasks_sweep', os.path.join(ROOT, 'client', 'tasks-sweep'))
spec = importlib.util.spec_from_loader('tasks_sweep', loader)
sweep = importlib.util.module_from_spec(spec)
loader.exec_module(sweep)


def task(i, status='todo', tags=()):
    return {'id': i, 'status': status, 'tags': list(tags)}


class SettleTest(unittest.TestCase):
    def verdicts(self, decisions, tasks):
        return {d['id']: d['verdict'] for d in sweep.settle(decisions, tasks)}

    def test_of_two_tasks_pointing_at_each_other_one_stays(self):
        d = [{'id': 1, 'verdict': 'duplicate', 'duplicate_of': 2, 'evidence': ''},
             {'id': 2, 'verdict': 'duplicate', 'duplicate_of': 1, 'evidence': ''}]
        self.assertEqual(sorted(self.verdicts(d, [task(1), task(2)]).values()), ['duplicate', 'open'])

    def test_a_duplicate_of_a_kept_or_done_task_closes(self):
        d = [{'id': 1, 'verdict': 'duplicate', 'duplicate_of': 2, 'evidence': ''},
             {'id': 3, 'verdict': 'duplicate', 'duplicate_of': 4, 'evidence': ''}]
        self.assertEqual(self.verdicts(d, [task(1), task(2), task(3), task(4, 'done')]), {1: 'duplicate', 3: 'duplicate'})

    def test_a_duplicate_of_a_missing_parked_or_dropped_task_stays(self):
        d = [{'id': 1, 'verdict': 'duplicate', 'duplicate_of': 9, 'evidence': ''},
             {'id': 2, 'verdict': 'duplicate', 'duplicate_of': 5, 'evidence': ''},
             {'id': 3, 'verdict': 'duplicate', 'duplicate_of': 6, 'evidence': ''}]
        tasks = [task(1), task(2), task(3), task(5, tags=['parked']), task(6, 'dropped')]
        self.assertEqual(self.verdicts(d, tasks), {1: 'open', 2: 'open', 3: 'open'})


if __name__ == '__main__':
    unittest.main()
