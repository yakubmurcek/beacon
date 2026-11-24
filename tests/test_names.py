"""The tailnet names snapshot: the checker's last POST is kept whole and read back as is."""

import importlib
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('BEACON_CONFIG', os.path.join(ROOT, 'config.example.json'))
sys.path.insert(0, os.path.join(ROOT, 'hub'))

Names = importlib.import_module("names").Names
Store = importlib.import_module("store").Store


class NamesSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.names = Names(Store(os.path.join(self.tmp.name, 'u.db')))

    def tearDown(self):
        self.tmp.cleanup()

    def test_nothing_posted_yet_reads_empty(self):
        self.assertEqual(self.names.view(), {'at': None, 'names': [], 'flags': []})

    def test_the_last_snapshot_replaces_the_one_before(self):
        self.assertEqual(self.names.save({'at': 100, 'names': [{'name': 'a'}], 'flags': []}), {'ok': True, 'names': 1})
        self.names.save({'at': 200, 'names': [{'name': 'b'}, {'name': 'c'}], 'flags': ['doubled']})
        self.assertEqual(self.names.view(), {'at': 200.0, 'names': [{'name': 'b'}, {'name': 'c'}], 'flags': ['doubled']})

    def test_a_snapshot_without_a_time_gets_now(self):
        self.names.save({'names': []})
        self.assertGreater(self.names.view()['at'], 1.7e9)

    def test_anything_but_a_names_list_is_refused(self):
        for body in (None, [], {}, {'names': 'a'}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.names.save(body)
        self.assertEqual(self.names.view()['names'], [])


if __name__ == '__main__':
    unittest.main()
