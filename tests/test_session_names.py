"""Session names are made where a session starts (client/tasks), so no page ever shows Claude Code's own
folder-and-hex name ("beacon-7c"): named from the first prompt, from another session's brief, and tried again
when a try names nothing."""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from test_tasks_cli import load_cli


def log_with(*records):
    with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')
    return f.name


TYPED = {'type': 'user', 'cwd': '/x/beacon', 'message': {'content': 'Fix the session names on the Tasks page'}}
BRIEF = {'type': 'user', 'cwd': '/x/beacon', 'promptSource': 'system', 'origin': {'kind': 'task-notification'},
         'message': {'content': 'You are a senior engineer reviewing a migration design note.'}}
TOOL = {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't', 'content': 'x'}]}}


class SessionNames(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.dir = tempfile.mkdtemp()
        self.cli.NAMES_DIR = self.dir
        self.logs = []

    def tearDown(self):
        for p in self.logs:
            os.unlink(p)

    def log(self, *records):
        p = log_with(*records)
        self.logs.append(p)
        return p

    def test_typed_prompt_wins_over_a_brief(self):
        prompts, project = self.cli.first_prompts(self.log(BRIEF, TYPED))
        self.assertEqual(prompts, ['Fix the session names on the Tasks page'])
        self.assertEqual(project, 'beacon')

    def test_a_session_another_session_started_is_named_from_its_brief(self):
        prompts, _ = self.cli.first_prompts(self.log(BRIEF, TOOL))
        self.assertEqual(prompts, ['You are a senior engineer reviewing a migration design note.'])

    def test_named_from_the_hook_prompt_before_it_reaches_the_log(self):
        path = self.log()
        with mock.patch.object(self.cli, 'task_name', side_effect=OSError), \
                mock.patch.object(self.cli, 'ask_haiku_name', return_value='Fix session names') as ask:
            self.cli.name_session('s1', path, prompt='Fix the session names')
        self.assertEqual(ask.call_args[0][1], ['Fix the session names'])
        self.assertEqual(self.cli.log_titles(path), ['Fix session names'])

    def test_a_task_title_beats_the_model(self):
        path = self.log(TYPED)
        with mock.patch.object(self.cli, 'task_name', return_value='Tasks page: one agent list'), \
                mock.patch.object(self.cli, 'ask_haiku_name') as ask:
            self.cli.name_session('s1', path)
        ask.assert_not_called()
        self.assertEqual(self.cli.log_titles(path), ['Tasks page: one agent list'])

    def test_a_failed_try_is_tried_again_up_to_the_limit(self):
        path = self.log(TYPED)
        marker = os.path.join(self.dir, 's1')
        started = []
        with mock.patch.object(self.cli.subprocess, 'Popen', side_effect=lambda *a, **k: started.append(a)), \
                mock.patch.object(self.cli, 'task_name', return_value=None), \
                mock.patch.object(self.cli, 'ask_haiku_name', return_value=None):
            for _ in range(5):
                self.cli._start_naming('s1', path)
                if os.path.exists(marker):
                    self.cli.name_session('s1', path)
        self.assertEqual(len(started), self.cli.NAME_TRIES)
        self.assertTrue(os.path.exists(marker))  # given up: no more model calls
        self.assertEqual(self.cli.log_titles(path), [])

    def test_the_prompt_hook_starts_naming_on_the_first_prompt(self):
        path = self.log(TYPED)
        ev = {'session_id': 's1', 'transcript_path': path, 'prompt': 'Fix the names', 'cwd': '/x/beacon'}
        with mock.patch.object(self.cli, 'hooks_off', return_value=False), \
                mock.patch.object(self.cli, 'manual_here', return_value=True), \
                mock.patch.object(self.cli, '_first_time', return_value=False), \
                mock.patch.object(self.cli, '_start_naming') as start:
            self.cli.hook_prompt(io.StringIO(json.dumps(ev)))
        start.assert_called_once_with('s1', path, prompt='Fix the names')

    def test_a_named_session_is_left_alone(self):
        path = self.log(TYPED, {'type': 'custom-title', 'customTitle': 'His name', 'sessionId': 's1'})
        ev = {'session_id': 's1', 'transcript_path': path, 'prompt': 'more', 'cwd': '/x/beacon'}
        with mock.patch.object(self.cli, 'hooks_off', return_value=False), \
                mock.patch.object(self.cli, 'manual_here', return_value=True), \
                mock.patch.object(self.cli, '_first_time', return_value=False), \
                mock.patch.object(self.cli, '_start_naming') as start:
            self.cli.hook_prompt(io.StringIO(json.dumps(ev)))
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
