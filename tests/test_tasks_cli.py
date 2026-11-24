"""The `tasks` command line, end to end against a fake hub: every subcommand's requests and printout.

A characterization test: tests/fixtures/tasks-cli.json holds what each command sent and printed, recorded from the
CLI as it was before its `main` became one function per subcommand. `BEACON_RECORD=1` rewrites the fixture; any
other difference is a change in behaviour.
"""
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ['BEACON_CONFIG'] = os.path.join(ROOT, 'config.example.json')  # the example settings, never the box's own
FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'tasks-cli.json')
NOW = 1_800_000_000.0


def load_cli():
    loader = importlib.machinery.SourceFileLoader('tasks_cli', os.path.join(ROOT, 'client', 'tasks'))
    spec = importlib.util.spec_from_loader('tasks_cli', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


TASK = {'id': 7, 'title': 'Fix the login', 'project': 'beacon', 'status': 'todo', 'tags': ['claude'],
        'priority': 2, 'due': '2026-11-30', 'updated_at': 5, 'body': 'Why.\n\n## Steps\n1. 🤖 a\n2. 👤 b',
        'plan': [{'label': 'a', 'owner': False, 'done': False}, {'label': 'b', 'owner': True, 'done': False}],
        'comments': [{'author': 'Claude Code abc', 'text': 'started'}],
        'sessions': [{'session_id': 'Claude Code:abc', 'state': 'working', 'machine': 'box'}], 'source': 'chat'}
QUESTION = {'id': 41, 'question': 'Which name?', 'project': 'beacon', 'kind': 'naming', 'status': 'open',
            'blocking': False, 'default_answer': 'Beacon', 'answer': None, 'task_id': 7, 'see': [], 'steps': []}


class FakeHub:
    """Answers the CLI's `call(path, body, auth)` from canned rows; `script` overrides a path with a list of
    answers given in turn (the last one repeats), or an exception to raise."""

    def __init__(self, script=None):
        self.log, self.script = [], {k: list(v) if isinstance(v, list) else [v] for k, v in (script or {}).items()}

    def answer(self, path, body):
        if path in self.script:
            got = self.script[path][0] if len(self.script[path]) == 1 else self.script[path].pop(0)
            if isinstance(got, BaseException):
                raise got
            return copy.deepcopy(got)
        base = path.split('?')[0]
        task = {**TASK, **(body or {})} if re.fullmatch(r'/api/tasks/\d+', base) else TASK
        question = {**QUESTION, **{k: v for k, v in (body or {}).items() if k in ('blocking', 'see', 'steps')}}
        routes = [
            (r'/api/tasks', {'tasks': [TASK, {**TASK, 'id': 8, 'tags': ['parked'], 'priority': 1, 'due': None}]}
             if body is None else TASK),
            (r'/api/tasks/auth', {'writer': 'owner'}),
            (r'/api/tasks/\d+', task),
            (r'/api/tasks/\d+/(comments|sessions)', TASK),
            (r'/api/questions', {'questions': [QUESTION]} if body is None else {**QUESTION, 'blocking': body.get('blocking', False)}),
            (r'/api/questions/kinds', {'kinds': [{'kind': 'naming', 'answered': 3, 'took_default': 1, 'standing': 0}]}),
            (r'/api/questions/bad', {'questions': [{**QUESTION, 'id': 2, 'flagged_at': 1, 'flag': 'vague'}]}),
            (r'/api/questions/rules', {'rules': [{'text': 'One plain line.'}]}),
            (r'/api/questions/\d+/(waiting|claim|handoff)', {}),
            (r'/api/questions/\d+/(answer|withdraw)', {**QUESTION, 'status': 'answered', 'answer': (body or {}).get('answer')}),
            (r'/api/questions/\d+/see', question),
            (r'/api/questions/\d+', QUESTION),
        ]
        for pattern, rows in routes:
            if re.fullmatch(pattern, base):
                return copy.deepcopy(rows)
        raise AssertionError(f'no fake answer for {path}')

    def __call__(self, path, body=None, auth=True):
        self.log.append([path, body, auth])
        return self.answer(path, body)


ANSWERED = {**QUESTION, 'status': 'answered', 'answer': 'Lighthouse', 'applied_at': None}
CASES = [
    ('ls', [], {}),
    ('ls everything', ['ls', '--all', '-p', 'beacon', '--due', 'today', '-q', 'login', '--claude'], {}),
    ('ls stale json', ['ls', '--stale', '--json'], {}),
    ('ls none', ['ls'], {'/api/tasks?status=open': {'tasks': []}}),
    ('auth', ['auth'], {}),
    ('add', ['add', 'Ship it', '-b', 'the body', '--priority', '2', '--due', '2026-12-01', '-t', 'x', '--doing'], {}),
    ('add no link json', ['add', 'Ship it', '--no-link', '--json', '-p', 'other'], {}),
    ('add progress refused', ['add', 'DONE: shipped'], {}),
    ('show', ['show', '7'], {}),
    ('show json', ['show', '7', '--json'], {}),
    ('done', ['done', '7', '-m', 'merged'], {}),
    ('drop', ['drop', '7'], {}),
    ('todo', ['todo', '7'], {}),
    ('doing', ['doing', '7'], {}),
    ('doing parked', ['doing', '7'], {'/api/tasks/7': [{**TASK, 'tags': ['parked', 'claude']}, TASK, {**TASK, 'step': 1}]}),
    ('doing no plan', ['doing', '7'], {'/api/tasks/7': {**TASK, 'plan': []}}),
    ('step', ['step', '7', '2'], {}),
    ('step out of range', ['step', '7', '5'], {}),
    ('set', ['set', '7', 'priority=1', 'tags=a,b', 'due=', 'title=New'], {}),
    ('set bad field', ['set', '7', 'colour=red'], {}),
    ('set no equals', ['set', '7', 'priority'], {}),
    ('handoff', ['handoff', '7', 'finish step 2'], {'/api/tasks/7': {**TASK, 'tags': []}}),
    ('handoff auto', ['handoff', '7', 'later'], {'/api/tasks/7': {**TASK, 'tags': ['auto']}}),
    ('handoff running', ['handoff', '7', 'later'], {'/api/tasks/7': {**TASK, 'status': 'doing'}}),
    ('handoff closed', ['handoff', '7', 'later'], {'/api/tasks/7': {**TASK, 'status': 'done'}}),
    ('note', ['note', '7', 'progress'], {}),
    ('link', ['link', '7', 'abc'], {}),
    ('link full id', ['link', '7', 'Codex:xyz'], {}),
    ('ask', ['ask', 'Which name?', '-o', 'Beacon :: short', '-o', 'Hub', '-c', '- 💰 free', '--task', '7',
             '-k', 'naming', '-d', 'Beacon'], {}),
    ('ask blocking', ['decide', 'Which name?', '-o', 'Beacon', '--blocking', '-p', 'other'], {}),
    ('ask gate', ['ask', 'Which name?', '-o', 'Beacon'],
     {'/api/questions': {**QUESTION, 'task_id': None, 'gate_blocking': True}}),
    ('ask json', ['ask', 'Which name?', '--json'], {}),
    ('ask other work', ['ask', 'Which name?', '-p', 'other'],
     {'/api/questions': {**QUESTION, 'project_asked': True, 'project': 'other'}}),
    ('ask too long', ['ask', 'x' * 130], {}),
    ('ask look refused', ['ask', 'Does the landing page look good?'], {}),
    ('ask look seen', ['ask', 'Does the landing page look good?', '--see', 'http://beacon', '--no-see', 'n/a'], {}),
    ('ask hands refused', ['ask', 'Sign the agreement?', '-o', 'Done'], {}),
    ('ask hands steps', ['ask', 'Sign the agreement?', '-o', 'Done', '--step', 'Open it :: https://example.com/a'], {}),
    ('ask refused by hub', ['ask', 'Which name?'], {'/api/questions': SystemExit('tasks: refused by the hub')}),
    ('block', ['block', '?41'], {}),
    ('see', ['see', '41', 'https://example.com/shot'], {}),
    ('steps', ['steps', '#41', 'Open it :: https://example.com', 'Tap Accept'], {}),
    ('questions', ['questions'], {}),
    ('questions none', ['questions', '--answered', '-k', 'naming', '-p', 'beacon', '--since', '2'],
     {'/api/questions?status=answered&limit=2000&kind=naming&project=beacon&since=1799827200.0': {'questions': []}}),
    ('questions open none', ['questions'], {'/api/questions?status=open&limit=2000': {'questions': []}}),
    ('questions json', ['questions', '--all', '--json'], {}),
    ('questions kinds', ['questions', '--kinds', '--since', '1'], {}),
    ('questions kinds json', ['questions', '--kinds', '--json'], {}),
    ('answer', ['answer', '41', 'Beacon', '-m', 'why', '--always', '--chat'], {}),
    ('withdraw', ['withdraw', '41', '-m', 'moot'], {}),
    ('wait withdrawn', ['wait', '41'], {'/api/questions/41': {**QUESTION, 'status': 'withdrawn', 'answer_note': 'moot'}}),
    ('wait timeout', ['wait', '41', '--timeout', '10'], {}),
    ('wait claimed', ['wait', '41', '42'], {'/api/questions/41': ANSWERED, '/api/questions/41/claim': {'won': True}}),
    ('wait lost claim', ['wait', '41', '--timeout', '6'],
     {'/api/questions/41': [ANSWERED, ANSWERED, QUESTION], '/api/questions/41/claim': {'won': False}}),
    ('wait applied', ['wait', '41', '42'], {'/api/questions/41': {**ANSWERED, 'applied_at': 1, 'answered_via': 'chat'}}),
    ('wait handed earlier', ['wait', '41'], {'/api/questions/41': {**ANSWERED, 'handoff_at': 1, 'handoff_reason': 'big'}}),
    ('wait hand off', ['wait', '41'],
     {'/api/questions/41': {**ANSWERED, 'project_asked': True, 'project': 'other'}, '/api/questions/41/handoff': {}}),
    ('wait unreachable', ['wait', '41', '--timeout', '6'],
     {'/api/questions/41': [SystemExit('tasks: hub unreachable at x'), QUESTION]}),
    ('wait handoff fails', ['wait', '41'],
     {'/api/questions/41': {**ANSWERED, 'project_asked': True, 'project': 'other'},
      '/api/questions/41/handoff': SystemExit('tasks: hub server error 502'), '/api/questions/41/claim': {'won': True}}),
    ('wait claim fails', ['wait', '41'],
     {'/api/questions/41': ANSWERED, '/api/questions/41/claim': SystemExit('tasks: hub unreachable at x')}),
    ('wait started for it', ['wait', '41'], {'/api/questions/41': {**ANSWERED, 'applied_at': 1}}, '41'),
    ('wait hub error', ['wait', '41'], {'/api/questions/41': SystemExit('tasks: no such question')}),
]


def run(cli, argv, script, qid=''):
    """What one command line sends and prints: the requests, stdout, and how it exited."""
    hub = FakeHub(script)
    out, clock = io.StringIO(), iter(NOW + i * 5 for i in range(10_000))
    patches = [mock.patch.object(cli, 'call', hub), mock.patch.object(cli, 'current_project', lambda: 'beacon'),
               mock.patch.object(cli, 'manual_here', lambda: False), mock.patch.object(cli, 'session_log', lambda sid: None),
               mock.patch.object(cli, 'session_size', lambda sid=None: (None, None)),
               mock.patch.object(cli.time, 'time', lambda: next(clock)), mock.patch.object(cli.time, 'sleep', lambda s: None),
               mock.patch.object(cli.os, 'getppid', lambda: 1), mock.patch.object(cli.datetime, 'date', FixedDate),
               mock.patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'abc12345-0000', 'BEACON_QID': qid}),
               mock.patch('sys.stdin', io.StringIO('from stdin'))]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(contextlib.redirect_stdout(out))
        try:
            cli.main(argv)
            code = None
        except SystemExit as e:
            code = e.code
    return {'requests': hub.log, 'stdout': out.getvalue(), 'exit': code}


class FixedDate(__import__('datetime').date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 24)


class TasksCliTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.cli = load_cli()
        cls.got = {name: run(cls.cli, *case) for name, *case in CASES}
        if os.environ.get('BEACON_RECORD'):
            with open(FIXTURE, 'w') as f:
                json.dump(cls.got, f, ensure_ascii=False, indent=1)
                f.write('\n')
        with open(FIXTURE) as f:
            cls.want = json.load(f)

    def test_every_command_sends_and_prints_what_it_did(self):
        self.assertEqual(sorted(self.want), sorted(self.got))
        for name, *_ in CASES:
            with self.subTest(name):
                self.assertEqual(self.want[name], json.loads(json.dumps(self.got[name])))

    def test_the_cases_reach_every_subcommand(self):
        commands = {argv[0] if argv else 'ls' for _, argv, *_ in CASES}
        with open(os.path.join(ROOT, 'client', 'tasks')) as f:
            parser_names = set(re.findall(r"add_parser\('([\w-]+)'", f.read()))
        self.assertEqual(parser_names - commands - {'hook-ask', 'hook-stop', 'hook-prompt', 'hook-gate', 'hook-name'},
                         set())


if __name__ == '__main__':
    unittest.main()
