"""answer-dispatcher's herdr-core route: his answer is typed into the asking agent's own terminal, any harness (task 2034)."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from test_beacon_agent import load_dispatcher

SID = '11111111-2222-3333-4444-555555555555'


class HcRouteTest(unittest.TestCase):
    def setUp(self):
        self.d = load_dispatcher()
        self.d.MACHINE = 'box'
        self.home = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.home, 'sessions'))
        with open(os.path.join(self.home, 'sessions', '42.json'), 'w') as f:
            json.dump({'pid': 42, 'sessionId': SID}, f)
        self.d.CLAUDE_HOME = self.home
        self.q = {'id': 9, 'answer': 'Go', 'question': 'Go?', 'answered_via': 'page', 'task_id': 3,
                  'session_id': 'hc:box/t3', 'answered_at': time.time() - 120}
        self.screen = ''
        self.state = 'done'
        self.sent = []

    def fake_hc(self, *args, timeout=30):
        if args[0] == 'ls':
            return {'terminals': [{'address': 't3', 'state': 'live'}, {'address': 't4', 'state': 'live'},
                                  {'address': 't5', 'state': 'finished'},
                                  {'address': 'pc/t2', 'state': 'live'}]}
        if args[0] == 'get':
            return {'t3': {'foreground': {'pid': 7}, 'status': {'state': self.state}},
                    't4': {'foreground': {'pid': 42}, 'status': {'state': 'idle'}},
                    'pc/t2': {'status': {'state': 'idle'}}}[args[1]]
        if args[0] == 'send':
            self.sent.append(args[1:])
            self.screen += args[2]
            return {'started': True}
        return {}

    def run_takes(self, claimed=True):
        self.d._hc.clear()
        with mock.patch.object(self.d, 'hc', side_effect=self.fake_hc), \
                mock.patch.object(self.d, 'hc_screen', side_effect=lambda t: self.screen), \
                mock.patch.object(self.d, 'claim', return_value=claimed) as claim, \
                mock.patch.object(self.d, 'note') as note, mock.patch.object(self.d, 'watchdog') as dog:
            took = self.d.asker_takes(self.q, None, time.time(), False)
        return took, claim, note, dog

    def test_any_agent_gets_the_answer_typed_into_its_terminal_once(self):
        took, claim, note, _ = self.run_takes()
        self.assertTrue(took)
        self.assertEqual(len(self.sent), 1)
        term, text = self.sent[0]
        self.assertEqual(term, 't3')
        self.assertTrue(text.startswith('[Beacon ?9] '))
        self.assertIn('Go', text)
        claim.assert_called_once()
        self.assertIn('terminal', note.call_args[0][1])
        self.run_takes()  # a second tick (claim lost before): its mark is on screen, never typed twice
        self.assertEqual(len(self.sent), 1)

    def test_a_claude_code_in_hc_is_found_by_its_session(self):
        self.q['session_id'] = f'Claude Code:{SID}'
        took, *_ = self.run_takes()
        self.assertTrue(took)
        self.assertEqual(self.sent[0][0], 't4')

    def test_an_agent_on_an_added_machine_gets_it_too(self):
        self.q['session_id'] = 'hc:pc/t2'
        took, *_ = self.run_takes()
        self.assertTrue(took)
        self.assertEqual(self.sent[0][0], 'pc/t2')

    def test_mid_turn_it_waits_for_the_next_tick(self):
        self.state = 'working'
        took, claim, _, dog = self.run_takes()
        self.assertTrue(took)
        self.assertEqual(self.sent, [])
        claim.assert_not_called()
        dog.assert_called_once()

    def test_another_machine_or_a_gone_terminal_is_not_taken_here(self):
        for sid in ('hc:other/t3', 'hc:box/t5', 'hc:box/t9'):
            self.q['session_id'] = sid
            with mock.patch.object(self.d.agent, 'is_live', return_value=False), \
                    mock.patch.object(self.d, 'was_agent', return_value=False):
                took, *_ = self.run_takes()
            self.assertFalse(took, sid)
        self.assertEqual(self.sent, [])


class AskerIdTest(unittest.TestCase):
    """`tasks` files a card under the hc terminal when no Claude Code session asks."""

    def test_ids(self):
        from test_beacon_agent import agent
        cli = agent.cli
        with mock.patch.dict(os.environ, {'HC_TERMINAL_ID': 't3'}, clear=False):
            os.environ.pop('CLAUDE_CODE_SESSION_ID', None)
            with mock.patch.object(cli.socket, 'gethostname', return_value='box.lan'):
                self.assertEqual(cli.session_id(), 'hc:box/t3')
                self.assertEqual(cli.author(), 'agent in hc box/t3')
            with mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': SID}):
                self.assertEqual(cli.session_id(), f'Claude Code:{SID}')


if __name__ == '__main__':
    unittest.main()


class AgentInHcTest(unittest.TestCase):
    """Beacon's own live agents run in a herdr-core terminal his Herdr app shows (task 2034)."""

    def test_run_starts_it_in_hc_under_the_beacon_space(self):
        from test_beacon_agent import agent
        sid = '22222222-0000-0000-0000-000000000000'
        agent.write(sid, 'meta.json', {'sid': sid, 'task': 4, 'cwd': '/x', 'name': 'Fix it', 'resume': False})
        calls = []

        def fake(*args, timeout=30):
            calls.append(args)
            return mock.Mock(returncode=0, stdout='{}', stderr='')
        with mock.patch.object(agent, 'hc', side_effect=fake), mock.patch.object(agent, 'get_ready', return_value=True), \
                mock.patch.object(agent, 'has_session', return_value=False):
            self.assertEqual(agent.run(sid), 0)
        launch = next(c for c in calls if c[0] == 'run')
        self.assertIn('herdr.space=beacon-agents', launch)
        self.assertIn('--outlive-parent', launch)
        self.assertEqual(launch[launch.index('--name') + 1], 'beacon-22222222')
        rest = launch[launch.index('--') + 1:]
        self.assertEqual(rest[0], 'env')
        self.assertIn('BEACON_AGENT=1', rest)
        self.assertIn('--dangerously-load-development-channels', rest)

    def test_a_live_beacon_agent_is_left_to_its_channel(self):
        d = load_dispatcher()
        q = {'id': 1, 'session_id': f'Claude Code:{SID}', 'answered_at': time.time() - 120}
        with mock.patch.object(d.agent, 'is_live', return_value=True), mock.patch.object(d, 'watchdog'), \
                mock.patch.object(d, 'hc_deliver') as deliver:
            self.assertTrue(d.asker_takes(q, None, time.time(), False))
        deliver.assert_not_called()
