"""The quality baseline only moves one way: lint and type-error counts down, coverage and mutation scores up."""

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

ratchet = importlib.import_module('ratchet')
scores = importlib.import_module('scores')


class BaselineDirection(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / 'quality-baseline.json'
        self.old = {'lint': {'C901': 10}, 'at_most': {'pyright hub': 5}, 'at_least': {'coverage hub %': 70.0}}

    def raised(self, now, before=None):
        self.tmp.write_text(json.dumps(now))
        shown = mock.Mock(returncode=0, stdout=json.dumps(before or self.old))
        with mock.patch.object(ratchet, 'BASELINE', self.tmp), mock.patch.object(ratchet.subprocess, 'run', return_value=shown):
            return ratchet.baseline_raised('HEAD~1')

    def test_better_numbers_are_not_loosening(self):
        self.assertFalse(self.raised({'lint': {'C901': 9}, 'at_most': {'pyright hub': 4}, 'at_least': {'coverage hub %': 71.0}}))

    def test_each_way_of_loosening_is_caught(self):
        for now in (
            {'lint': {'C901': 11}, 'at_most': {'pyright hub': 5}, 'at_least': {'coverage hub %': 70.0}},
            {'lint': {'C901': 10}, 'at_most': {'pyright hub': 6}, 'at_least': {'coverage hub %': 70.0}},
            {'lint': {'C901': 10}, 'at_most': {'pyright hub': 5}, 'at_least': {'coverage hub %': 69.9}},
            {'lint': {'C901': 10}, 'at_most': {}, 'at_least': {'coverage hub %': 70.0}},
            {'lint': {'C901': 10}, 'at_most': {'pyright hub': 5}, 'at_least': {}},
        ):
            with self.subTest(now=now):
                self.assertTrue(self.raised(now))

    def test_the_old_flat_file_reads_as_lint_counts(self):
        self.assertTrue(self.raised({'lint': {'C901': 11}}, before={'C901': 10}))
        self.assertFalse(self.raised({'lint': {'C901': 10}}, before={'C901': 10}))


class ScoreJudge(unittest.TestCase):
    def judge(self, now, section, held, update=False):
        tmp = Path(tempfile.mkdtemp()) / 'quality-baseline.json'
        tmp.write_text(json.dumps({'lint': {}, section: held}))
        with mock.patch.object(scores, 'BASELINE', tmp), mock.patch('builtins.print'):
            code = scores.judge(now, section, update)
        return code, json.loads(tmp.read_text())[section]

    def test_coverage_may_only_go_up(self):
        self.assertEqual(self.judge({'coverage hub %': 69.9}, 'at_least', {'coverage hub %': 70.0})[0], 1)
        code, held = self.judge({'coverage hub %': 72.5}, 'at_least', {'coverage hub %': 70.0}, update=True)
        self.assertEqual((code, held), (0, {'coverage hub %': 72.5}))

    def test_type_errors_may_only_go_down(self):
        self.assertEqual(self.judge({'pyright hub': 6}, 'at_most', {'pyright hub': 5})[0], 1)
        code, held = self.judge({'pyright hub': 3}, 'at_most', {'pyright hub': 5}, update=True)
        self.assertEqual((code, held), (0, {'pyright hub': 3}))

    def test_a_machine_dependent_floor_moves_only_in_ci(self):
        held = {'coverage collector %': 50.0}
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': ''}):
            self.assertEqual(self.judge({'coverage collector %': 52.7}, 'at_least', held, update=True), (0, held))
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
            self.assertEqual(self.judge({'coverage collector %': 52.7}, 'at_least', held, update=True)[1],
                             {'coverage collector %': 52.7})

    def test_without_update_the_file_is_left_alone(self):
        self.assertEqual(self.judge({'pyright hub': 3}, 'at_most', {'pyright hub': 5}), (0, {'pyright hub': 5}))


class Areas(unittest.TestCase):
    def test_paths_map_to_their_area(self):
        self.assertEqual(scores.area_of('hub/store.py'), 'hub')
        self.assertEqual(scores.area_of(os.path.join(ROOT, 'collector', 'collect.py')), 'collector')
        self.assertEqual(scores.area_of('client/tasks'), 'client')
        self.assertIsNone(scores.area_of('web/app.js'))


if __name__ == '__main__':
    unittest.main()
