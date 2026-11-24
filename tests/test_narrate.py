import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hub'))
from narrate import Narrator  # noqa: E402


def snap(*sessions):
    return [{'machine': 'devbox', 'sessions': [dict({'state': 'working', 'idle_s': 3, 'project': 'p'}, **s) for s in sessions]}]


class NarratorTest(unittest.TestCase):
    def setUp(self):
        self.asked = []
        self.n = Narrator(None, model=lambda p: (self.asked.append(p), {'1': {'now': 'Putting the new Team page live.', 'role': 'deploy'}})[1], background=False)

    def test_a_working_session_gets_one_plain_sentence(self):
        self.n.observe(snap({'id': 'a', 'doing': '$ git push origin main', 'title': 'Team page'}))
        self.assertEqual(self.n.run_once(), 1)
        self.assertIn('$ git push origin main', self.asked[0])
        live = {'machines': snap({'id': 'a', 'doing': 'x'})}
        self.n.annotate(live)
        self.assertEqual(live['machines'][0]['sessions'][0]['now'], 'Putting the new Team page live')
        self.assertEqual(live['machines'][0]['sessions'][0]['role'], 'deploy')

    def test_nothing_new_asks_nothing(self):
        self.n.observe(snap({'id': 'a', 'doing': 'Editing a.py'}))
        self.n.run_once()
        self.n.observe(snap({'id': 'a', 'doing': 'Editing a.py'}))
        self.assertEqual(self.n.run_once(), 0)
        self.assertEqual(len(self.asked), 1)

    def test_waiting_and_quiet_sessions_are_not_narrated(self):
        self.n.observe(snap({'id': 'w', 'state': 'waiting', 'doing': '✅ done'}, {'id': 'q', 'idle_s': 900, 'doing': 'x'}))
        self.assertEqual(self.n.run_once(), 0)
        live = {'machines': snap({'id': 'w', 'state': 'waiting'})}
        self.n.annotate(live)
        self.assertNotIn('now', live['machines'][0]['sessions'][0])

    def test_the_daily_cap_holds(self):
        self.n.calls = [__import__('time').time()] * 400
        self.n.observe(snap({'id': 'a', 'doing': 'Editing a.py'}))
        self.assertEqual(self.n.run_once(), 0)


if __name__ == '__main__':
    unittest.main()
