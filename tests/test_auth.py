"""Who may write: the token, or his own device as tailscaled names it. A login header proves nothing."""
import email.message
import unittest

from test_tasks import LiveHub  # first: it puts hub/ on the path and the example settings in place
from whois import Caller, Whois, peer_address


def headers(*forwarded):
    msg = email.message.Message()
    for ip in forwarded:
        msg['X-Forwarded-For'] = ip
    return msg


class PeerAddressTest(unittest.TestCase):
    def test_only_a_single_tailnet_address_forwarded_on_loopback_counts(self):
        local = ('127.0.0.1', 5000)
        self.assertEqual(peer_address(local, headers('100.100.1.1')), '100.100.1.1')
        self.assertEqual(peer_address(('::1', 5000), headers('fd7a:115c:a1e0::1')), 'fd7a:115c:a1e0::1')
        self.assertIsNone(peer_address(('100.100.9.9', 5000), headers('100.100.1.1')))  # not through serve
        self.assertIsNone(peer_address(local, headers()))
        self.assertIsNone(peer_address(local, headers('100.100.1.1, 100.100.2.2')))
        self.assertIsNone(peer_address(local, headers('100.100.1.1', '100.100.2.2')))
        self.assertIsNone(peer_address(local, headers('203.0.113.5')))  # the public door's callers
        self.assertIsNone(peer_address(local, headers('not an address')))


class WhoisCacheTest(unittest.TestCase):
    def test_a_known_device_is_asked_once_a_minute_and_a_failure_never_sticks(self):
        asked, now = [], [0.0]
        answers = {'100.100.1.1': Caller('me@example.com', False)}
        whois = Whois(lambda ip: asked.append(ip) or answers.get(ip), lambda: now[0])
        self.assertEqual(whois('100.100.1.1').login, 'me@example.com')
        self.assertEqual(whois('100.100.1.1').login, 'me@example.com')
        self.assertIsNone(whois('100.100.3.3'))
        self.assertIsNone(whois('100.100.3.3'))
        self.assertEqual(asked, ['100.100.1.1', '100.100.3.3', '100.100.3.3'])
        now[0] = 61
        whois('100.100.1.1')
        self.assertEqual(asked[-1], '100.100.1.1')


FORGED = {'Tailscale-User-Login': LiveHub.OWNER}
REC = {'ts': 1.0, 'session': 's', 'tool': 'Bash', 'category': 'delete', 'what': 'rm -r x'}


class ForgedLoginTest(LiveHub, unittest.TestCase):

    def test_a_login_header_alone_is_refused(self):
        self.assertEqual(self.call('/api/tasks', {'title': 'x'}, FORGED)[0], 401)
        self.assertEqual(self.call('/api/tasks/auth', headers=FORGED)[0], 401)
        self.assertEqual(self.call('/api/knowledge/overview', headers=FORGED)[0], 401)

    def test_a_login_header_from_another_device_is_refused(self):
        other = {**FORGED, 'X-Forwarded-For': '100.100.3.3'}
        self.assertEqual(self.call('/api/tasks', {'title': 'x'}, other)[0], 401)
        server = {**FORGED, 'X-Forwarded-For': self.SERVER_IP}  # a tagged server is not him
        self.assertEqual(self.call('/api/tasks', {'title': 'x'}, server)[0], 401)

    def test_his_device_writes_without_any_header_of_its_own(self):
        code, t = self.call('/api/tasks', {'title': 'mine'}, self.me())
        self.assertEqual((code, t['title']), (201, 'mine'))
        self.assertEqual(self.call('/api/tasks/auth', headers=self.me()), (200, {'writer': 'owner'}))


class GateLogAuthTest(LiveHub, unittest.TestCase):

    def test_the_gate_log_takes_a_writer_or_his_tagged_server_only(self):
        self.assertEqual(self.call('/api/gate/log', REC)[0], 401)
        self.assertEqual(self.call('/api/gate/log', REC, {'Tailscale-User-Login': self.OWNER})[0], 401)
        self.assertEqual(self.call('/api/gate/log', REC, {'X-Forwarded-For': '100.100.3.3'})[0], 401)
        self.assertEqual(self.call('/api/gate/log', REC, {'X-Forwarded-For': self.SERVER_IP})[0], 201)
        self.assertEqual(self.call('/api/gate/log', REC, self.me())[0], 201)
        self.assertEqual(self.call('/api/gate/log', REC, {'Authorization': 'Bearer tok'})[0], 201)


if __name__ == '__main__':
    unittest.main()
