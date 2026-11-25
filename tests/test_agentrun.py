"""The one agent policy (client/agentrun.py, task 2043): every launcher takes its claude flags from it."""
import importlib.util
import os
import re
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


if __name__ == '__main__':
    unittest.main()
