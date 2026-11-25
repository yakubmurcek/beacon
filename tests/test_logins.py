"""Claude sign-ins (hub/logins.py): end dates, the notice that links to the sign-in, and the sign-in's steps."""
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hub'))
import logins
from test_tasks import LiveHub

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


# A stand-in for ssh: the login prints its link and waits for the code ('good-code-123' signs in), the end-date
# command prints FAKE_EXP, anything else (the after-command) just succeeds.
FAKE_SSH = """#!/bin/sh
for a; do last=$a; done
case "$last" in
  *'auth login'*)
    printf '\\033[1mOpening browser\\033[0m\\r\\nvisit:\\r\\n'
    printf 'https://x/oauth/authorize?code=1&s=2\\r\\nPaste code here if prompted > '
    read code
    [ "$code" = good-code-123 ] && { echo 'Login successful'; exit 0; }
    echo 'Invalid code'; exit 1 ;;
  *credentials*) echo "$FAKE_EXP" ;;
  *) exit 0 ;;
esac
"""


class SignInProcess(unittest.TestCase):
    """The sign-in process end to end, against a fake ssh."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        ssh = os.path.join(self.dir.name, 'bin', 'ssh')
        os.makedirs(os.path.dirname(ssh))
        with open(ssh, 'w') as f:
            f.write(FAKE_SSH)
        os.chmod(ssh, stat.S_IRWXU)
        env = {'PATH': os.path.dirname(ssh) + os.pathsep + os.environ['PATH'],
               'FAKE_EXP': str(int((time.time() + 30 * DAY) * 1000))}
        for p in (mock.patch.dict(os.environ, env),
                  mock.patch.object(logins, 'RUN_DIR', os.path.join(self.dir.name, 'run')),
                  mock.patch.object(logins, 'BOXES', {'devbox': {'ssh': 'root@devbox', 'login': 'claude auth login',
                                                                 'after': 'systemctl restart x'}})):
            p.start()
            self.addCleanup(p.stop)
        self.l = logins.Logins(spawn=lambda box: None)

    def sign_in(self, code, limit=20):
        out = {}
        t = threading.Thread(target=lambda: out.setdefault('rc', logins.run('devbox', limit=limit)))
        t.start()
        for _ in range(100):
            st = logins.read_status('devbox')
            if st and st['state'] == 'waiting':
                break
            time.sleep(0.05)
        self.assertEqual(self.l.view(with_url=True)['boxes'][0]['flow']['url'], 'https://x/oauth/authorize?code=1&s=2')
        if code:
            self.l.code('devbox', code)
        t.join(30)
        return out['rc'], logins.read_status('devbox') or {}

    def test_good_code_signs_in_and_reads_the_new_end(self):
        rc, st = self.sign_in('good-code-123')
        self.assertEqual((rc, st['state']), (0, 'done'))
        self.assertGreater(st['ends_at'], time.time() + 29 * DAY)
        self.assertEqual(self.l.view()['boxes'][0]['state'], 'ok')

    def test_wrong_code_fails_with_what_the_box_said(self):
        rc, st = self.sign_in('wrong-code-123')
        self.assertEqual((rc, st['state']), (1, 'failed'))
        self.assertIn('Invalid code', st['error'])

    def test_nobody_finishing_ends_it(self):
        with mock.patch.object(logins, 'finish', return_value=-15):
            rc, st = self.sign_in(None, limit=1)
        self.assertEqual((rc, st['state']), (1, 'failed'))

    def test_end_date_read_over_ssh(self):
        self.assertGreater(logins.read_expiry('devbox') or 0, time.time() + 29 * DAY)
        os.environ['FAKE_EXP'] = 'nope'
        self.assertEqual(logins.read_expiry('devbox'), 0)

    def test_spawn_without_systemd_starts_a_process(self):
        with mock.patch.object(logins.shutil, 'which', return_value=None), \
                mock.patch.object(logins.subprocess, 'Popen') as popen:
            logins.Logins._spawn('devbox')
        self.assertEqual(popen.call_args[0][0][-2:], ['run', 'devbox'])
        with mock.patch.object(logins.shutil, 'which', return_value='/bin/systemd-run'), \
                mock.patch.object(logins.subprocess, 'run') as run:
            logins.Logins._spawn('devbox')
        self.assertIn('--unit=beacon-claude-login-devbox', run.call_args[0][0])

    def test_watch_runs_a_check(self):
        watcher = logins.Logins(expiry=lambda b: None)
        with mock.patch.object(logins.threading, 'Thread') as thread:
            watcher.watch()
        loop = thread.call_args.kwargs['target']
        with mock.patch.object(watcher, 'check', side_effect=[RuntimeError('x'), None]), mock.patch.object(logins, 'log'), \
                mock.patch.object(logins.time, 'sleep', side_effect=[None, StopIteration]), self.assertRaises(StopIteration):
            loop()


class Routes(LiveHub, unittest.TestCase):
    """GET /api/logins for anyone, the link and the writes for his login only."""
    def setUp(self):
        super().setUp()
        self.l = mock.Mock()
        self.l.view.side_effect = lambda with_url=False: {'boxes': [], 'with_url': with_url}
        self.l.start.side_effect = lambda box: {'state': 'starting'} if box == 'devbox' else (_ for _ in ()).throw(KeyError(box))
        self.l.code.side_effect = lambda box, code: (_ for _ in ()).throw(ValueError('no sign-in is waiting'))
        p = mock.patch.object(self.server.Handler, 'logins', self.l, create=True)
        p.start()
        self.addCleanup(p.stop)

    def test_link_only_for_him(self):
        self.assertEqual(self.call('/api/logins')[1]['with_url'], False)
        self.assertEqual(self.call('/api/logins', headers=self.me())[1]['with_url'], True)

    def test_writes(self):
        self.assertEqual(self.call('/api/logins/devbox/start', {}, {'Authorization': 'Bearer tok'})[0], 403)
        self.assertEqual(self.call('/api/logins/devbox/start', {}, self.me()), (200, {'state': 'starting'}))
        self.assertEqual(self.call('/api/logins/nobox/start', {}, self.me())[0], 404)
        self.assertEqual(self.call('/api/logins/devbox/code', {'code': 'x'}, self.me())[0], 400)
        self.assertEqual(self.call('/api/logins/devbox/bogus', {}, self.me())[0], 404)
        self.assertEqual(self.call('/api/logins/devbox/start', {})[0], 401)


if __name__ == '__main__':
    unittest.main()
