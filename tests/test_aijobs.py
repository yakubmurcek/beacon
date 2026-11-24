import json
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'collector'), os.path.join(ROOT, 'hub')]

import llm  # noqa: E402
from aijobs import AIJobs, group_of, model_name, prompt_key  # noqa: E402
from machines import Machines  # noqa: E402
from store import Store  # noqa: E402


def script(d, name, text):
    p = os.path.join(d, name)
    with open(p, 'w') as f:
        f.write(text)
    return p


class SniffTest(unittest.TestCase):
    def setUp(self):
        # not under /tmp or /var: the sniffer never reads scripts from there (temp files, system dirs)
        self.d = tempfile.mkdtemp(dir=os.path.expanduser('~'), prefix='.aijobs-test-')
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_a_headless_claude_run_with_model_effort_and_prompt(self):
        p = script(self.d, 'harvest.sh', '#!/bin/sh\nclaude -p --model sonnet --effort medium "Daily reading for $DAY, unattended"\n')
        ai = llm.sniff(['/bin/sh', p])
        self.assertEqual(ai['tools'], ['Claude Code'])
        self.assertEqual(ai['models'], ['sonnet'])
        self.assertEqual(ai['efforts'], ['medium'])
        self.assertIn('daily reading for', ai['prompts'])

    def test_a_python_argument_list_through_a_variable(self):
        p = script(self.d, 'scout.py', 'CLAUDE = "claude"\nMODEL = os.environ.get("X_MODEL", "haiku")\n'
                                       'subprocess.run([CLAUDE, "-p", "--model", MODEL, prompt])\n')
        ai = llm.sniff(['/usr/bin/python3', p])
        self.assertEqual(ai['tools'], ['Claude Code'])
        self.assertEqual(ai['models'], ['haiku'])

    def test_prose_and_comments_are_not_a_call(self):
        p = script(self.d, 'sweep.py', '"""Sessions started by headless `claude -p` never clean up."""\n'
                                       '# pause the claude -p jobs\nprint("opus")\n')
        self.assertIsNone(llm.sniff(['/usr/bin/python3', p]))

    def test_a_direct_api_caller_and_a_second_level_script(self):
        inner = script(self.d, 'ask.py', 'URL = "https://api.deepseek.com/chat/completions"\nmodel = "deepseek-chat"\n')
        outer = script(self.d, 'run.sh', f'#!/bin/sh\npython3 {inner}\n')
        ai = llm.sniff(['/bin/sh', outer])
        self.assertEqual(ai['providers'], ['DeepSeek'])
        self.assertEqual(ai['models'], ['deepseek-chat'])

    def test_the_agent_is_the_program_not_a_wrapper(self):
        self.assertEqual(llm.agent_of('/root/.local/bin/claude -p hi'), 'Claude Code')
        self.assertIsNone(llm.agent_of('timeout 2h /root/.local/bin/claude -p hi'))
        self.assertEqual(llm.agent_of('opencode run x'), 'OpenCode')


class AIJobsTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), 'u.db'))
        self.machines = Machines(self.store)
        self.ai = AIJobs(self.store)
        self.now = time.time()

    def call(self, i, session, model='claude-sonnet-5-5', effort='medium', ts=None, machine='devbox'):
        return {'id': f'c{session}{i}', 'agent': 'Claude Code', 'session': session, 'project': 'p', 'model': model,
                'ts': ts or self.now - 3600, 'kind': 'call', 'input': 1000, 'output': 1000, 'effort': effort}

    def prompt(self, session, text, ts=None):
        return {'id': f'p{session}', 'agent': 'Claude Code', 'session': session, 'project': 'p',
                'ts': (ts or self.now - 3600) - 1, 'kind': 'prompt', 'text': text}

    def test_a_linked_run_and_its_unlinked_twin_land_on_the_job(self):
        jobs = [{'id': 'systemd:czech-harvest', 'name': 'czech-harvest', 'kind': 'timer', 'desc': 'Czech harvest',
                 'state': 'ok', 'ai': {'tools': ['Claude Code'], 'models': ['sonnet'], 'efforts': ['medium']}},
                {'id': 'systemd:rules-review', 'name': 'rules-review', 'kind': 'timer', 'state': 'ok',
                 'ai': {'tools': ['Claude Code'], 'models': ['sonnet'], 'efforts': []}}]
        self.store.ingest('devbox', [
            self.prompt('Claude Code:a', '[scheduled] Daily reading for 2026-11-21, unattended'), self.call(1, 'Claude Code:a'),
            self.prompt('Claude Code:b', '[scheduled] Daily reading for 2026-11-22, unattended'), self.call(1, 'Claude Code:b'),
            self.prompt('Claude Code:h', 'fix the bug please'), self.call(1, 'Claude Code:h', model='claude-opus-5-5'),
            self.prompt('Claude Code:r', '[scheduled] Review this change before it ships.'),
            self.call(1, 'Claude Code:r', model='claude-opus-5-5', effort='high'),
        ])
        self.machines.save('devbox', {'jobs': jobs, 'jobs_at': 1})
        self.ai.save('devbox', {'llm_runs': [{'job': 'systemd:czech-harvest', 'agent': 'Claude Code',
                                             'session': 'Claude Code:b', 'pid': 5, 'started': self.now}]})
        v = self.ai.build()
        by = {j['name']: j for j in v['jobs']}
        harvest = by['Czech harvest']
        self.assertEqual(harvest['runs'], 2)  # the linked run and the one before it, same prompt start
        self.assertEqual(harvest['models'][0]['name'], 'Sonnet 5.5')
        self.assertEqual(harvest['efforts'][0]['name'], 'medium')
        self.assertTrue(harvest['linked'])
        review = by['Review this change before it ships.']
        self.assertFalse(review['linked'])
        self.assertEqual(review['models'][0]['name'], 'Opus 5.5')
        self.assertNotIn('fix the bug please', json.dumps(v))  # his own session is not a job
        self.assertFalse(by['Rules review']['measured'])  # set up, no runs
        self.assertAlmostEqual(sum(j['cost'] for j in v['jobs']), v['total']['cost'], places=2)

    def test_a_run_that_keeps_no_log_still_counts(self):
        self.ai.save('cloud', {'llm_runs': [{'job': 'systemd:answer-run-180-17953', 'agent': 'Claude Code',
                                               'session': None, 'pid': 9, 'started': self.now}]})
        v = self.ai.build()
        self.assertEqual(v['jobs'][0]['runs'], 1)
        self.assertEqual(v['jobs'][0]['name'], 'Answer run')


    def test_a_loop_inside_his_session_counts_only_its_scheduled_turns(self):
        t0 = self.now - 7200
        self.store.ingest('laptop', [
            {**self.prompt('Claude Code:s', '/loop 10m check the deploy', ts=t0), 'id': 'p1'},
            self.call(1, 'Claude Code:s', model='claude-opus-5-5', ts=t0 + 5),
            {**self.prompt('Claude Code:s', '[scheduled] check the deploy', ts=t0 + 600), 'id': 'p2'},
            self.call(2, 'Claude Code:s', model='claude-sonnet-5-5', ts=t0 + 605),
            {**self.prompt('Claude Code:s', '[scheduled] check the deploy', ts=t0 + 1200), 'id': 'p3'},
            self.call(3, 'Claude Code:s', model='claude-sonnet-5-5', ts=t0 + 1205),
        ])
        v = self.ai.build()
        self.assertEqual(len(v['jobs']), 1)
        loop = v['jobs'][0]
        self.assertEqual(loop['kind'], 'loop in a session')
        self.assertEqual(loop['runs'], 2)  # two fires; his own first turn on Opus is his
        self.assertEqual([m['name'] for m in loop['models']], ['Sonnet 5.5'])

    def test_a_linked_run_with_no_log_counts_with_its_command_line_model(self):
        self.ai.save('cloud', {'llm_runs': [{'job': 'systemd:agent-usage', 'agent': 'Claude Code',
                                               'session': 'Claude Code:t1', 'pid': 3, 'started': self.now,
                                               'model': 'claude-haiku-5-5', 'effort': 'low'}]})
        j = self.ai.build()['jobs'][0]
        self.assertEqual((j['name'], j['runs'], j['measured']), ('Agent usage', 1, False))
        self.assertEqual(j['models'][0]['name'], 'Haiku 5.5')
        self.assertTrue(j['models'][0]['nolog'])
        self.assertEqual(j['efforts'][0]['name'], 'low')

    def test_a_session_a_scheduled_task_started_with_a_prompt_is_that_jobs(self):
        cmd = ('conhost.exe --headless "C:\\Users\\m\\.local\\bin\\claude.exe" --remote-control="Smash the Car prototype" '
               '"Read BRIEF.md in this folder and do it."')
        ai = llm.sniff([cmd])
        self.assertEqual(ai['launches'], ['read brief md'])
        self.store.ingest('gamer', [self.prompt('Claude Code:w', 'Read BRIEF.md in this folder and do it.'),
                                      self.call(1, 'Claude Code:w', model='claude-opus-5-5')])
        self.machines.save('gamer', {'jobs': [{'id': 'schtask:Smash the Car session', 'name': 'Smash the Car session',
                                                 'kind': 'schtask', 'state': 'ok', 'ai': ai}], 'jobs_at': 1})
        j = self.ai.build()['jobs'][0]
        self.assertEqual((j['runs'], j['models'][0]['name']), (1, 'Opus 5.5'))
        self.assertTrue(j['cost'] > 0)
        self.assertIsNone(llm.sniff(['/root/.local/bin/claude remote-control --name "Usage hub"']))
        self.assertIsNone(llm.sniff(['claude --append-system-prompt "Answer in Czech and be brief"']))

    def test_what_he_types_later_into_a_launched_session_is_his(self):
        cmd = 'claude.exe --remote-control=x "Read BRIEF.md in this folder and do it."'
        t0 = self.now - 7200
        self.store.ingest('gamer', [
            {**self.prompt('Claude Code:w', 'Read BRIEF.md in this folder and do it.', ts=t0), 'id': 'p1'},
            self.call(1, 'Claude Code:w', ts=t0 + 5),
            {**self.prompt('Claude Code:w', 'now also fix my other game', ts=t0 + 600), 'id': 'p2'},
            self.call(2, 'Claude Code:w', model='claude-opus-5-5', ts=t0 + 605)])
        self.machines.save('gamer', {'jobs': [{'id': 'schtask:S', 'name': 'S', 'kind': 'schtask', 'state': 'ok',
                                                 'ai': llm.sniff([cmd])}], 'jobs_at': 1})
        j = self.ai.build()['jobs'][0]
        self.assertEqual((j['calls'], [m['name'] for m in j['models']]), (1, ['Sonnet 5.5']))

    def test_a_timer_naming_no_model_takes_no_nearby_run(self):
        t0 = self.now - 7200
        self.store.ingest('devbox', [self.prompt('Claude Code:r', '[scheduled] Review this change before it ships.', ts=t0 + 61),
                                    self.call(1, 'Claude Code:r', model='claude-opus-5-5', ts=t0 + 70)])
        self.machines.save('devbox', {'jobs': [{'id': 'systemd:answer-dispatcher', 'name': 'answer-dispatcher',
                                               'kind': 'timer', 'state': 'ok', 'last_start': t0,
                                               'ai': {'tools': ['Claude Code'], 'models': [], 'efforts': []}}],
                                     'jobs_at': 1})
        by = {j['name']: j for j in self.ai.build()['jobs']}
        self.assertEqual(by['Review this change before it ships.']['runs'], 1)
        self.assertEqual(by['Answer dispatcher']['runs'], 0)

    def test_the_window_is_the_seven_days_the_bars_show(self):
        v = self.ai.build()
        self.store.ingest('devbox', [self.prompt('Claude Code:o', '[scheduled] Old run of a thing', ts=self.now - 7 * 86400 + 60),
                                    self.call(1, 'Claude Code:o', ts=self.now - 7 * 86400 + 60)])
        v = self.ai.build()
        self.assertAlmostEqual(v['total']['cost'], sum(sum(j['days']) for j in v['jobs']), places=4)


class NamesTest(unittest.TestCase):
    def test_words(self):
        self.assertEqual(model_name('claude-haiku-4-5-20251001'), 'Haiku 4.5')
        self.assertEqual(model_name('sonnet'), 'Sonnet')
        self.assertEqual(group_of('systemd:answer-run-180-1795358745'), 'systemd:answer-run')
        self.assertEqual(prompt_key('[scheduled] Daily reading for 2026-11-16, unattended on devbox'),
                         prompt_key('[scheduled] Daily reading for 2026-11-21, unattended on devbox'))


if __name__ == '__main__':
    unittest.main()
