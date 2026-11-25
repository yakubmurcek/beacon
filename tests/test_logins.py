"""Claude sign-ins (hub/logins.py): end dates, the notice that links to the sign-in, and the sign-in's steps."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hub'))
import logins

DAY = 86400
NOW = 1_800_000_000


class Logins(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patches = [mock.patch.object(logins, 'RUN_DIR', self.dir.name),
                   mock.patch.object(logins, 'BOXES', {'devbox': {'ssh': 'root@devbox', 'login': 'claude auth login'}})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.sent, self.spawned = [], []
        self.ends: dict[str, float | None] = {'devbox': NOW + 30 * DAY}
        self.l = logins.Logins(self.sent.append, 'https://hub', spawn=self.spawned.append, expiry=lambda b: self.ends[b])

    def test_states(self):
        self.assertEqual(logins.state(None, NOW), 'unknown')
        self.assertEqual(logins.state(0, NOW), 'out')
        self.assertEqual(logins.state(NOW - 1, NOW), 'out')
        self.assertEqual(logins.state(NOW + DAY, NOW), 'soon')
        self.assertEqual(logins.state(NOW + 10 * DAY, NOW), 'ok')

    def test_no_notice_while_far_off(self):
        self.l.check(NOW)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.l.view()['boxes'][0]['ends_at'], NOW + 30 * DAY)

    def test_notice_links_to_the_sign_in(self):
        self.ends['devbox'] = NOW + 2 * DAY
        self.l.check(NOW)
        n = self.sent[0]
        self.assertEqual(n['link'], 'https://hub/machines#/login/devbox')
        self.assertIn('devbox', n['title'])
        self.assertEqual(n['key'], 'claude-login:devbox')

    def test_expired_counts_as_signed_out(self):
        self.ends['devbox'] = NOW - 3600
        self.l.check(NOW)
        self.assertIn('signed out', self.sent[0]['title'])

    def test_box_that_does_not_answer_keeps_what_was_known(self):
        self.l.check(NOW)
        self.ends['devbox'] = None
        self.l.check(NOW + 60)
        self.assertEqual(self.l.ends['devbox'][0], NOW + 30 * DAY)
        self.assertEqual(self.sent, [])

    def test_start_spawns_once_while_running(self):
        self.l.start('devbox')
        self.assertEqual(self.spawned, ['devbox'])
        logins.write_status('devbox', state='waiting', pid=os.getpid(), url='https://x/oauth/authorize?a=1')
        self.l.start('devbox')
        self.assertEqual(self.spawned, ['devbox'])  # one is already going

    def test_link_only_for_his_login(self):
        logins.write_status('devbox', state='waiting', pid=os.getpid(), url='https://x/oauth/authorize?a=1')
        self.assertNotIn('url', self.l.view()['boxes'][0]['flow'])
        self.assertEqual(self.l.view(with_url=True)['boxes'][0]['flow']['url'], 'https://x/oauth/authorize?a=1')
        self.assertNotIn('pid', self.l.view(with_url=True)['boxes'][0]['flow'])

    def test_just_asked_for_reads_starting_and_is_not_started_twice(self):
        self.l.start('devbox')  # the fake spawn starts nothing: like the moment before the runner writes its pid
        self.assertEqual(self.l.view()['boxes'][0]['flow']['state'], 'starting')
        self.l.start('devbox')
        self.assertEqual(self.spawned, ['devbox'])

    def test_dead_sign_in_reads_failed(self):
        logins.write_status('devbox', state='waiting', pid=2 ** 22 + 7)
        self.assertEqual(self.l.view()['boxes'][0]['flow']['state'], 'failed')

    def test_code_goes_to_a_waiting_sign_in_only(self):
        with self.assertRaises(ValueError):
            self.l.code('devbox', 'abcdefghijkl#state')
        logins.write_status('devbox', state='waiting', pid=os.getpid())
        with self.assertRaises(ValueError):
            self.l.code('devbox', 'bad code; rm -rf /')
        self.l.code('devbox', '  abcdefghijkl#state  ')
        with open(os.path.join(self.dir.name, 'devbox', 'code')) as f:
            self.assertEqual(f.read(), 'abcdefghijkl#state')
        with self.assertRaises(KeyError):
            self.l.code('nobox', 'abcdefghijkl')

    def test_link_is_found_through_terminal_colours(self):
        raw = '\x1b[1mOpening browser\x1b[0m\r\nvisit:\r\n\x1b]8;;https://a/oauth/authorize?x=1\x07https://a/oauth/authorize?x=1&y=2\x1b]8;;\x07\r\n'
        m = logins.URL_RE.search(logins.ANSI_RE.sub('', raw))
        self.assertEqual(m and m.group(0), 'https://a/oauth/authorize?x=1&y=2')


if __name__ == '__main__':
    unittest.main()
