import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hub'))
from swr import SWR  # noqa: E402


class SWRTest(unittest.TestCase):
    def test_fresh_is_served_without_a_rebuild(self):
        c, calls = SWR(), []
        build = lambda: calls.append(1) or len(calls)  # noqa: E731
        self.assertEqual(c.get('k', build, fresh=60), 1)
        self.assertEqual(c.get('k', build, fresh=60), 1)
        self.assertEqual(len(calls), 1)

    def test_stale_is_served_at_once_and_rebuilt_behind(self):
        c, gate = SWR(), threading.Event()
        c.get('k', lambda: 'old', fresh=0)

        def slow():
            gate.wait(2)
            return 'new'
        t = time.time()
        self.assertEqual(c.get('k', slow, fresh=0), 'old')  # no wait on the slow build
        self.assertLess(time.time() - t, 0.5)
        gate.set()
        for _ in range(50):
            if c.get('k', lambda: 'x', fresh=60) == 'new':
                break
            time.sleep(0.02)
        self.assertEqual(c.get('k', lambda: 'x', fresh=60), 'new')

    def test_a_failed_rebuild_keeps_the_old_value(self):
        c = SWR()
        c.get('k', lambda: 'old', fresh=0)
        c.get('k', lambda: 1 / 0, fresh=0)
        time.sleep(0.1)
        self.assertEqual(c.get('k', lambda: 'x', fresh=60), 'old')

    def test_the_oldest_keys_go_past_the_limit(self):
        c = SWR(limit=2)
        for k in 'abc':
            c.get(k, lambda k=k: k)
        self.assertEqual(len(c._vals), 2)
        self.assertNotIn('a', c._vals)


if __name__ == '__main__':
    unittest.main()
