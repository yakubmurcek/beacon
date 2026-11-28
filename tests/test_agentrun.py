"""The one agent policy (client/agentrun.py, task 2043): every launcher takes its claude flags from it."""
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location('agentrun', os.path.join(ROOT, 'client', 'agentrun.py'))
agentrun = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agentrun)

LAUNCHERS = ('task-agent', 'answer-dispatcher', 'beacon-agent')


def starts(src):
    """Every `[CLAUDE, ...]` command in a launcher's source, to its closing bracket."""
    out = []
    for m in re.finditer(r'\[CLAUDE,', src):
        depth, i = 0, m.start()
        while True:
            depth += {'[': 1, ']': -1}.get(src[i], 0)
            if depth == 0:
                break
            i += 1
        out.append(src[m.start():i + 1])
    return out
# The only claude calls outside the policy: plain model one-shots with nothing loaded and no tools, never agents
# (task-agent's router and its login refresh).
ONE_SHOTS = 2


class PolicyTest(unittest.TestCase):
    def test_every_trigger_is_capped_or_says_why_not(self):
        for name, p in agentrun.POLICY.items():
            self.assertIn(p['mode'], ('auto', 'bypassPermissions', 'default'), name)
            if name != 'live':  # --max-budget-usd needs --print: a live chat is held by the live cap and idle reap
                self.assertTrue(p.get('budget'), name)
                self.assertIn('--max-budget-usd', agentrun.flags(name), name)
        self.assertNotIn('--max-budget-usd', agentrun.flags('live', printed=False))

    def test_every_agent_runs_at_medium_effort_unless_its_repo_pins_one(self):
        for name in agentrun.POLICY:
            f = agentrun.flags(name)
            self.assertEqual(f.count('--effort'), 1, name)
            self.assertEqual(f[f.index('--effort') + 1], 'medium', name)
        f = agentrun.flags('repo', effort='high')
        self.assertEqual(f[f.index('--effort') + 1], 'high')

    def test_answer_runs_are_capped_in_money_and_turns(self):
        f = agentrun.flags('answer')
        self.assertEqual(f[f.index('--max-turns') + 1], '80')
        self.assertEqual(f[f.index('--max-budget-usd') + 1], f"{agentrun.USD:g}")

    def test_a_repo_run_loads_only_the_repo_and_his_core_rules(self):
        f = agentrun.flags('repo')
        self.assertEqual(f[f.index('--setting-sources') + 1], 'project')
        self.assertIn('--strict-mcp-config', f)
        self.assertIn('--no-chrome', f)
        if agentrun.core_rules():
            self.assertEqual(f[f.index('--append-system-prompt') + 1], agentrun.core_rules())

    def test_the_reviewer_sees_nothing_but_what_it_is_given(self):
        f = agentrun.flags('review')
        self.assertEqual(f[f.index('--setting-sources') + 1], '')
        self.assertEqual(f[f.index('--tools') + 1], '')
        self.assertEqual(f[f.index('--model') + 1], 'sonnet')


class LaunchersTest(unittest.TestCase):
    def source(self, name):
        with open(os.path.join(ROOT, 'client', name), encoding='utf-8') as f:
            return f.read()

    def test_no_launcher_sets_its_own_permission_mode_or_caps(self):
        for name in LAUNCHERS:
            src = self.source(name)
            for flag in ('--permission-mode', '--max-budget-usd', '--max-turns', '--dangerously-skip-permissions',
                         '--append-system-prompt'):
                self.assertNotIn(f"'{flag}'", src, f'{name} sets {flag} itself: take it from agentrun.flags')

    def test_every_agent_start_takes_the_policy(self):
        """Each `claude` agent command in a launcher carries agentrun.flags(...): a new start path that skips the
        policy fails here."""
        for name in LAUNCHERS:
            src = self.source(name)
            cmds = starts(src)
            policed = [s for s in cmds if 'agentrun.flags(' in s]
            loose = [s for s in cmds if 'agentrun.flags(' not in s]
            self.assertTrue(policed, f'{name} starts no agent through the policy')
            for s in loose:  # only a one-shot with nothing loaded and no tools may skip it
                self.assertIn("'--setting-sources', ''", s, f'{name}: {s[:120]}')
                self.assertIn("'--tools', ''", s, f'{name}: {s[:120]}')
        self.assertEqual(sum(len([s for s in starts(self.source(n)) if 'agentrun.flags(' not in s])
                             for n in LAUNCHERS), ONE_SHOTS)


class ViewTest(unittest.TestCase):
    """A headless run's Herdr tab (agentrun.show / watch)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.dir.name, 'hc.log')
        stub = os.path.join(self.dir.name, 'hc')
        with open(stub, 'w') as f:
            f.write(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {self.log}\n')
        os.chmod(stub, 0o700)
        self.old = agentrun.HC, agentrun.VIEW_DIR
        agentrun.HC, agentrun.VIEW_DIR = stub, os.path.join(self.dir.name, 'Beacon agents')

    def tearDown(self):
        agentrun.HC, agentrun.VIEW_DIR = self.old
        self.dir.cleanup()

    def test_the_tab_sits_in_the_beacon_agents_space_and_never_takes_a_live_chats_name(self):
        name = agentrun.show('abcdef12-3456', 'Write the draft', 'move', 2043, '/home')
        with open(self.log) as f:
            run = next(x for x in f.read().splitlines() if x.startswith('run '))
        self.assertRegex(name, r'^view-abcdef12-\d{8}$')  # a live chat is beacon-<sid8>: answers are typed into it
        self.assertLessEqual(len(name), 32)  # hc's limit
        self.assertIn(f'--name {name}', run)
        self.assertIn('herdr.space=beacon-agents', run)
        self.assertIn(f'herdr.tab=beacon-agents-{name}', run)
        self.assertIn('beacon.trigger=move', run)
        self.assertIn('Write the draft · move · task 2043', run)
        self.assertTrue(os.path.isdir(agentrun.VIEW_DIR))  # a new space is named after this folder

    def test_no_hc_means_no_tab_and_no_error(self):
        agentrun.HC = os.path.join(self.dir.name, 'missing')
        self.assertEqual(agentrun.show('abcdef12', 'x', 'task', None, '/home'), '')
        self.assertFalse(os.path.exists(self.log))
        agentrun.ended('')  # nothing to mark

    def test_it_shows_words_and_tools_not_thinking(self):
        entry = {'type': 'assistant', 'message': {'content': [
            {'type': 'thinking', 'thinking': 'secret'}, {'type': 'text', 'text': 'Looking at the page'},
            {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'git   status'}}]}}
        self.assertEqual(agentrun.lines_of(entry), ['💬 Looking at the page', '🔧 Bash: git status'])
        self.assertEqual(agentrun.lines_of({'type': 'user', 'message': {'content': []}}), [])

    def watch(self, lines, pid=None, done=''):
        sid = 'feed0000'
        folder = os.path.join(self.dir.name, 'projects', 'x')
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, sid + '.jsonl'), 'w') as f:
            f.writelines(json.dumps(x) + '\n' for x in lines)
        if pid is None:
            gone = subprocess.Popen(['true'])
            gone.wait()
            pid = gone.pid
        out = io.StringIO()
        old_linger, agentrun.LINGER = agentrun.LINGER, 0
        try:
            with contextlib.redirect_stdout(out):
                self.assertEqual(agentrun.watch(sid, self.dir.name, pid, 'Write the draft', 1795600000.0, done), 0)
        finally:
            agentrun.LINGER = old_linger
        return out.getvalue()

    @staticmethod
    def said(text, when):
        return {'type': 'assistant', 'timestamp': when, 'message': {'content': [{'type': 'text', 'text': text}]}}

    def test_it_follows_the_transcript_and_ends_with_the_run(self):
        out = self.watch([self.said('Done.', '2026-11-25T22:00:00.000Z')])
        self.assertIn('💬 Done.', out)
        self.assertIn('The run ended', out)

    def test_a_resumed_sessions_history_stays_out(self):
        out = self.watch([self.said('Old work', '2026-11-20T10:00:00.000Z'), self.said('New work', '2026-11-25T22:00:00Z')])
        self.assertNotIn('Old work', out)
        self.assertIn('New work', out)

    def test_ended_closes_the_tab_while_its_launcher_goes_on(self):
        mark = os.path.join(self.dir.name, 'done')
        with open(mark, 'w'):
            pass
        out = self.watch([self.said('Round one', '2026-11-25T22:00:00Z')], pid=os.getpid(), done=mark)
        self.assertIn('The run ended', out)
        self.assertFalse(os.path.exists(mark))

if __name__ == '__main__':
    unittest.main()
