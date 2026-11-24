"""Outdated copies (hub/freshness.py): a box running an old checkout, file or tool is a problem and an alarm."""
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'collector'), os.path.join(ROOT, 'hub'), os.path.join(ROOT, 'tests')]

import freshness  # noqa: E402
import host  # noqa: E402
from test_machines import Box  # noqa: E402

NOW = time.time()
DAY = 86400


class Fake(freshness.Sources):
    def __init__(self, heads=None, files=None):
        super().__init__(gh=lambda repo: (heads or {}).get(repo))
        self.files = files or {}

    def file(self, source):
        return self.files.get(source)


def co(sha='old', when=NOW - 3 * DAY, branch='main', dirty=False):
    return {'checkouts': [{'name': 'Agent config', 'sha': sha, 'time': when, 'branch': branch, 'dirty': dirty}]}


class Check(unittest.TestCase):
    src = Fake({'example-user/agent-config': ('new', NOW - DAY)})

    def test_a_checkout_missing_a_day_of_changes(self):
        [p] = freshness.check('laptop', co(), self.src, NOW)
        self.assertEqual((p['key'], p['fix']), ('drift:laptop:Agent config', 'pull'))
        self.assertIn('missing 24 h of changes', p['text'])

    def test_local_edits_block_the_self_fix(self):
        [p] = freshness.check('laptop', co(dirty=True), self.src, NOW)
        self.assertIsNone(p['fix'])
        self.assertIn('local edits', p['text'])

    def test_quiet_when_current_ahead_on_a_branch_or_in_grace(self):
        self.assertEqual(freshness.check('laptop', co(sha='new'), self.src, NOW), [])
        self.assertEqual(freshness.check('laptop', co(when=NOW), self.src, NOW), [])  # a local commit not pushed yet
        self.assertEqual(freshness.check('laptop', co(branch='fix-x'), self.src, NOW), [])  # an agent's branch
        fresh = Fake({'example-user/agent-config': ('new', NOW - 3600)})
        self.assertEqual(freshness.check('laptop', co(), fresh, NOW), [])  # the source changed an hour ago
        self.assertEqual(freshness.check('laptop', {}, self.src, NOW), [])  # nothing reported: the collector check covers it

    def test_a_box_stuck_on_a_side_branch_where_nobody_works(self):
        freshness.CONFIG['freshness']['checkouts'][0]['strict'] = ['cloud']
        self.addCleanup(freshness.CONFIG['freshness']['checkouts'][0].pop, 'strict')
        [p] = freshness.check('cloud', co(branch='feat/x'), self.src, NOW)
        self.assertEqual(p['text'], 'Agent config is stuck on branch feat/x, not main')
        self.assertEqual(freshness.check('laptop', co(branch='feat/x'), self.src, NOW), [])

    def test_only_the_machines_it_applies_to(self):
        rep = {'files': [{'name': 'tasks CLI', 'sha256': 'abc'}]}
        src = Fake(files={'client/tasks': ('def', NOW - 2 * DAY)})
        [p] = freshness.check('gamer', rep, src, NOW)
        self.assertIn('tasks CLI is an old copy', p['text'])
        self.assertEqual(freshness.check('laptop', rep, src, NOW), [])
        self.assertEqual(freshness.check('gamer', {'files': [{'name': 'tasks CLI', 'sha256': 'def'}]}, src, NOW), [])

    def test_inventory(self):
        self.assertEqual([c['name'] for c in freshness.inventory('laptop')['checkouts']], ['Agent config'])
        self.assertEqual(freshness.inventory('devbox')['checkouts'][1]['paths'], ['~/projects/machine-setup'])
        self.assertEqual([f['name'] for f in freshness.inventory('gamer')['files']], ['tasks CLI'])

    def test_a_head_that_cannot_be_read_keeps_the_last_and_is_noted(self):
        heads = {'r': ('a', 1.0)}
        src = freshness.Sources(gh=lambda repo: heads.get(repo))
        self.assertEqual(src.head('r', NOW), ('a', 1.0))
        heads.clear()
        self.assertEqual(src.head('r', NOW + 700), ('a', 1.0))
        self.assertEqual(src.failing, {'r': NOW + 700})

    def test_the_hub_file_source(self):
        digest, changed = freshness.Sources.file('client/tasks')
        self.assertEqual(len(digest), 64)
        self.assertGreater(changed, 0)
        self.assertIsNone(freshness.Sources.file('no/such/file'))


class Versions(unittest.TestCase):
    def test_a_tool_far_behind_the_newest(self):
        ms = [{'machine': 'cloud', 'online': True, 'versions': {'claude': '2.1.291 (Claude Code)'}},
              {'machine': 'devbox', 'online': True, 'versions': {'claude': '2.1.330 (Claude Code)'}},
              {'machine': 'laptop', 'online': True, 'versions': {'claude': '2.1.325 (Claude Code)'}},
              {'machine': 'gamer', 'online': False, 'versions': {'claude': '1.0.0'}}]
        out = freshness.version_lag(ms)
        self.assertEqual(list(out), ['cloud'])
        self.assertEqual(out['cloud'][0]['text'], 'claude 2.1.291 is outdated (newest here 2.1.330)')
        self.assertEqual(out['cloud'][0]['fix'], 'update')


class Hooks(unittest.TestCase):
    def test_a_machine_whose_cards_all_came_from_the_backstop(self):
        con = sqlite3.connect(':memory:')
        con.row_factory = sqlite3.Row
        con.execute('CREATE TABLE endings (machine TEXT, source TEXT, created_at REAL)')
        con.executemany('INSERT INTO endings VALUES (?, ?, ?)', [('gamer', 'backstop', NOW)] * 3
                        + [('devbox', 'backstop', NOW)] * 5 + [('devbox', 'hook', NOW)] + [('laptop', 'backstop', NOW)])
        self.assertEqual(list(freshness.deaf_hooks(con, NOW)), ['gamer'])
        self.assertEqual(freshness.deaf_hooks(sqlite3.connect(':memory:'), NOW), {})


class OnTheMachinesPage(Box):
    def test_an_outdated_copy_is_a_problem_and_one_alarm_then_fixed(self):
        self.m.sources = Fake({'example-user/agent-config': ('new', NOW - DAY)})
        def push(rep):
            self.m.save('devbox', {'stats': {'disks': []}, 'versions': {}, 'jobs': [], 'jobs_at': time.time(), 'fresh': rep})
            with self.store.connect() as con:
                con.execute('INSERT OR REPLACE INTO live(machine, seen_at, sessions) VALUES (?, ?, ?)', ('devbox', time.time(), '[]'))
        push({})
        self.baseline()
        push(co())
        m = next(x for x in self.m.view()['machines'] if x['machine'] == 'devbox')
        self.assertEqual(m['state'], 'warn')
        self.assertIn('Agent config is outdated', m['problems'][0])
        self.assertEqual(self.m.freshness_for('devbox')['pull'], ['Agent config'])
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['drift:devbox:Agent config'])
        self.sent.clear()
        self.m.tick(send=self.send)
        self.assertEqual(self.sent, [])  # once a day, not every pass
        push(co(sha='new'))
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['drift:devbox:Agent config:ok'])

    def test_a_source_the_hub_cannot_read_is_its_own_problem(self):
        self.m.sources = Fake()
        self.m.sources.failing['example-user/agent-config'] = time.time() - 2 * 3600
        self.m.save('cloud', {'stats': {'disks': []}, 'versions': {}, 'jobs': [], 'jobs_at': time.time()})
        with self.store.connect() as con:
            con.execute('INSERT OR REPLACE INTO live(machine, seen_at, sessions) VALUES (?, ?, ?)', ('cloud', time.time(), '[]'))
        m = next(x for x in self.m.view()['machines'] if x['machine'] == 'cloud')
        self.assertIn("can't read the source of example-user/agent-config", m['problems'][0])


class Collector(unittest.TestCase):
    def test_local_facts_of_a_real_checkout_and_file(self):
        got = host.checkout(ROOT)
        self.assertEqual(len(got['sha']), 40)
        self.assertIn('branch', got)
        self.assertEqual(len(host.file_hash(os.path.join(ROOT, 'client', 'tasks'))), 64)
        self.assertIsNone(host.checkout(tempfile.gettempdir()))
        self.assertEqual(host.existing(['~/no-such-dir-x', ROOT]), os.path.realpath(ROOT))


if __name__ == '__main__':
    unittest.main()
