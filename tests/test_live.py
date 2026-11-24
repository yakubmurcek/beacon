"""Live state: the collector's snapshot and the hub's /api/live."""
import json
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'collector'), os.path.join(ROOT, 'hub')]

import live  # noqa: E402
from store import Store  # noqa: E402

SID = '2325a7d2-d3bc-42f5-a0a1-9c7e7c1b1581'


def write_jsonl(path, rows, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')
    if mtime:
        os.utime(path, (mtime, mtime))


def is_claude(cmd):
    return live.claude_session(cmd) is not None


class ClaudeProcessTest(unittest.TestCase):
    """Ported from lighthouse's claude-proc.test.mjs, plus the 2.1.x background plumbing."""

    def test_counts_sessions_however_they_were_launched(self):
        for cmd in ('claude', '/root/.local/bin/claude --dangerously-skip-permissions',
                    '"C:\\Users\\murcj\\.local\\bin\\claude.exe" --continue',
                    'node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js', 'claude --model opus'):
            self.assertTrue(is_claude(cmd), cmd)

    def test_reads_the_session_id_wrapped_or_not(self):
        self.assertEqual(live.claude_session(f'claude --session-id {SID}')['session_id'], SID)
        self.assertEqual(live.claude_session(
            f"pwsh -NoExit -Command claude --resume {SID} --remote-control 'jev lab work'")['session_id'], SID)
        self.assertIsNone(live.claude_session('claude')['session_id'])
        desktop = ('C:\\Users\\m\\AppData\\Roaming\\Claude\\claude-code\\2.1.286\\x\\claude.exe --output-format stream-json '
                   f'--input-format stream-json --permission-prompt-tool stdio --resume={SID} --allowedTools a,b')
        self.assertEqual(live.claude_session(desktop)['session_id'], SID)

    def test_an_empty_quoted_argument_is_a_word(self):
        self.assertEqual(live.argv('foo "" \'\' bar'), ['foo', '', '', 'bar'])

    def test_a_prompt_starting_with_a_subcommand_word_is_a_session(self):
        for cmd in ('claude install deps and run tests', 'claude update the README',
                    'claude doctor this flaky test', 'claude auth flow is broken, fix it'):
            self.assertTrue(is_claude(cmd), cmd)
        for cmd in ('claude install stable', 'claude auth status', 'claude plugin install foo'):
            self.assertFalse(is_claude(cmd), cmd)

    def test_ignores_logins_one_shots_servers_headless_and_plumbing(self):
        for cmd in ('/bin/bash -lic set +m; HOME=/root /root/.local/bin/claude auth login 2>&1',
                    'claude mcp list', 'claude --dangerously-skip-permissions mcp list', 'claude update',
                    'claude -p summarize this', 'claude --version',
                    '"C:\\Users\\murcj\\.local\\bin\\claude.exe" remote-control --permission-mode auto',
                    'grep claude /var/log/syslog', '/usr/bin/python3 /opt/claude-usage/poller.py',
                    # background-session plumbing, seen on the Mac 2026-10-05
                    '/Users/x/.local/bin/claude daemon run --origin transient',
                    'claude bg-pty-host --bg-pty-host /tmp/cc/spare/a.pty.sock 200 50 -- /x/2.1.289 --bg-spare /tmp/s',
                    'claude bg-spare --bg-spare /tmp/cc/spare/a.claim.sock',
                    '/Users/x/.local/bin/claude --chrome-native-host', '/Users/x/.local/bin/claude agents'):
            self.assertFalse(is_claude(cmd), cmd)

    def test_never_mistakes_the_desktop_app(self):
        for cmd in ('/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper --type=utility',
                    '/Applications/Claude.app/Contents/MacOS/Claude'):
            self.assertFalse(is_claude(cmd), cmd)

    def test_a_wrapper_and_its_claude_count_once(self):
        got = live.claude_processes([
            {'pid': 10, 'ppid': 1, 'args': 'bash -c claude'},
            {'pid': 11, 'ppid': 10, 'args': '/usr/local/bin/claude'},
            {'pid': 20, 'ppid': 1, 'args': f'claude --session-id {SID}'},
            {'pid': 30, 'ppid': 20, 'args': '/bin/zsh -c -l source /tmp/s.sh && eval which claude < /dev/null'},
        ])
        self.assertEqual([p['pid'] for p in got], [11, 20])


class ClaudeLiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, 'projects')
        self.dir = os.path.join(self.root, '-Users-x-Projects-demo')
        self.now = time.time()

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, sid, rows, age=5):
        write_jsonl(os.path.join(self.dir, sid + '.jsonl'), rows, self.now - age)

    def prompt(self, text):
        return {'type': 'user', 'cwd': '/Users/x/Projects/demo', 'gitBranch': 'main',
                'message': {'role': 'user', 'content': text}}

    def tool(self, name, inp):
        return {'type': 'assistant', 'message': {'model': 'claude-opus-5-5',
                                                 'content': [{'type': 'tool_use', 'name': name, 'input': inp}]}}

    def run_live(self, procs=(), jobs=None):
        return {s['id']: s for s in live.claude_live(self.now, [self.root], list(procs), jobs)}

    def test_turn_markers_not_timing_decide_the_state(self):
        self.session('busy', [{'type': 'ai-title', 'aiTitle': 'Fix login'}, self.prompt('fix it'),
                              self.tool('Edit', {'file_path': '/Users/x/Projects/demo/src/auth.ts'})], age=300)
        self.session('done', [self.prompt('hi'),
                              {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Done.\nMore.'}]}},
                              {'type': 'system', 'subtype': 'turn_duration'}])
        self.session('stuck', [self.prompt('go'), self.tool('Bash', {'command': 'npm test'})], age=live.WORKING_STALL + 60)
        got = self.run_live()
        self.assertEqual((got['busy']['state'], got['busy']['doing'], got['busy']['title']),
                         ('working', 'Editing auth.ts', 'Fix login'))
        self.assertEqual((got['busy']['project'], got['busy']['branch'], got['busy']['prompt']), ('demo', 'main', 'fix it'))
        self.assertEqual((got['done']['state'], got['done']['doing']), ('waiting', 'Done.'))
        self.assertEqual((got['stuck']['state'], got['stuck']['doing']), ('idle', '$ npm test'))

    def test_a_shell_call_reads_as_its_plain_description(self):
        self.assertEqual(live.describe_tool('Bash', {'command': 'git log -3', 'description': 'Show recent commits'}),
                         'Show recent commits')
        self.assertEqual(live.describe_tool('Bash', {'command': 'git log -3', 'description': ' '}), '$ git log -3')

    def reply(self, text):
        return [{'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}},
                {'type': 'system', 'subtype': 'turn_duration'}]

    def test_a_reply_that_ends_all_done_is_done_not_waiting(self):
        self.session('fin', [self.prompt('go')] + self.reply('Here is the prompt.\n\n✅ All done.'))
        self.session('close', [self.prompt('go')] + self.reply('✅ You can close this session — nothing pending.'))
        self.session('ask', [self.prompt('go')] + self.reply(
            '- ✅ **Tests:** green\n\n✅ All done.\n\n⏳ Needs you:\n- 👀 **Merge PR #3** → `merge`'))
        self.session('bullet', [self.prompt('go')] + self.reply('Shipped.\n- ✅ **Cleanup:** done'))
        self.session('again', [self.prompt('go')] + self.reply('✅ All done.') + [self.prompt('one more thing')])
        self.session('empty', [])
        got = self.run_live()
        self.assertEqual({k: v['state'] for k, v in got.items()},
                         {'fin': 'done', 'close': 'done', 'ask': 'waiting', 'bullet': 'waiting', 'again': 'working', 'empty': 'working'})
        self.assertEqual(live.RANK['done'], max(live.RANK.values()))

    def test_an_interrupted_turn_is_back_with_the_human(self):
        self.session('esc', [{'type': 'custom-title', 'customTitle': 'My name'}, {'type': 'ai-title', 'aiTitle': 'Auto'},
                             self.prompt('go'), self.tool('Bash', {'command': 'sleep 99'}),
                             {'type': 'user', 'message': {'role': 'user', 'content': [
                                 {'type': 'text', 'text': '[Request interrupted by user]'}]}}], age=live.WORKING_STALL + 60)
        got = self.run_live()['esc']
        self.assertEqual((got['state'], got['doing'], got['title'], got['prompt']), ('waiting', 'Stopped by you', 'My name', 'go'))

    def test_old_logs_need_a_process_or_an_open_job(self):
        old = live.LIVE_WITHIN + 600
        for sid in ('gone', SID, 'jobbed'):
            self.session(sid, [self.prompt('x'), {'type': 'system', 'subtype': 'turn_duration'}], age=old)
        got = self.run_live(procs=[{'pid': 5, 'ppid': 1, 'args': f'claude --resume {SID}'}],
                            jobs={'jobbed': {'over': False, 'name': 'Invoice run'}})
        self.assertEqual(sorted(got), sorted([SID, 'jobbed']))
        self.assertEqual(got['jobbed']['title'], 'Invoice run')

    def test_closing_a_session_does_not_bring_it_back(self):
        # Archived in the desktop app: no process, no registry entry, but the shutdown just
        # appended untimed bookkeeping rows, so the file itself is fresh.
        old = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(self.now - 86400))
        new = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(self.now - 60))
        self.session('archived', [dict(self.prompt('go'), timestamp=old),
                                  {'type': 'system', 'subtype': 'turn_duration', 'timestamp': old},
                                  {'type': 'mode', 'mode': 'normal'}, {'type': 'cost-state', 'totalCostUSD': 0}])
        self.session('recent', [dict(self.prompt('go'), timestamp=new),
                                {'type': 'system', 'subtype': 'turn_duration', 'timestamp': new}])
        got = self.run_live()
        self.assertEqual(sorted(got), ['recent'])
        self.assertAlmostEqual(got['recent']['last'], self.now - 60, delta=2)

    def test_a_process_nothing_can_name_is_not_listed(self):
        orig = live.process_cwd
        live.process_cwd = lambda pid: None  # Windows: no cwd
        try:
            got = self.run_live(procs=[{'pid': 7, 'ppid': 1, 'started': self.now - 60, 'args': 'claude --model opus'}])
        finally:
            live.process_cwd = orig
        self.assertEqual(got, {})

    def test_a_finished_job_is_not_waiting_even_when_fresh(self):
        self.session('fin', [self.prompt('x'), {'type': 'system', 'subtype': 'turn_duration'}])
        self.assertEqual(self.run_live(jobs={'fin': {'over': True, 'name': None}}), {})

    def test_active_subagents_are_helpers(self):
        self.session('lead', [self.prompt('x'), self.tool('Agent', {'description': 'scan repos'})])
        write_jsonl(os.path.join(self.dir, 'lead', 'subagents', 'agent-a.jsonl'), [{}], self.now - 10)
        write_jsonl(os.path.join(self.dir, 'lead', 'subagents', 'agent-b.jsonl'), [{}], self.now - 3600)
        got = self.run_live()['lead']
        self.assertEqual((got['helpers'], got['doing']), (1, 'Delegating: scan repos'))

    def test_a_turn_keeps_its_latest_steps_for_the_task_page(self):
        # task 1944: a run nobody watches can be followed step by step on its task page
        old = [self.prompt('first'), self.tool('Bash', {'command': 'ls'})]
        steps = [self.tool('Bash', {'command': f'echo {i}'}) for i in range(live.STEPS + 2)]
        self.session('run', old + [self.prompt('go')] + steps)
        got = self.run_live()['run']['steps']
        self.assertEqual([x['text'] for x in got], [f'$ echo {i}' for i in range(2, live.STEPS + 2)])

    def test_secrets_never_reach_the_snapshot(self):
        self.session('k', [self.prompt('use sk-ant-' + 'a' * 40), self.tool('Bash', {'command': 'export K=sk-' + 'b' * 40})])
        got = self.run_live()['k']
        self.assertNotIn('aaaa', got['prompt'])
        self.assertNotIn('bbbb', got['doing'])

    def test_the_session_registry_says_who_is_open_and_how(self):
        reg_dir = os.path.join(self.tmp.name, 'sessions')
        os.makedirs(reg_dir)
        rows = {101: ('quiet', 'idle', 'Agent architecture', 'user'), 102: ('busy', 'busy', 'auto name', 'auto'),
                103: ('dead', 'idle', 'gone', 'auto'), 104: ('fresh', 'idle', 'brand new', 'auto'),
                105: ('old-cc', None, 'projects-96', 'derived'), 106: ('finished', 'idle', 'Snow Helper', 'user')}
        for pid, (sid, status, name, source) in rows.items():
            with open(os.path.join(reg_dir, f'{pid}.json'), 'w') as f:
                d = {'pid': pid, 'sessionId': sid, 'cwd': '/Users/x/Projects/demo', 'name': name, 'nameSource': source,
                     'startedAt': (self.now - 7 * 86400) * 1000}
                if status:  # an older Claude Code writes neither status nor its timestamp
                    d.update(status=status, statusUpdatedAt=(self.now - 60) * 1000)
                json.dump(d, f)
        week = 7 * 86400
        # quiet for a week, its last turn still open in the log: the registry's "idle" wins
        self.session('quiet', [{'type': 'ai-title', 'aiTitle': 'Auto title'}, self.prompt('go')], age=week)
        self.session('busy', [self.prompt('go'), self.tool('Bash', {'command': 'sleep 900'}),
                              {'type': 'system', 'subtype': 'turn_duration'}], age=live.WORKING_STALL + 60)
        self.session('dead', [self.prompt('x')], age=week)
        self.session('old-cc', [self.prompt('x'), self.tool('Bash', {'command': 'ls'})], age=week)
        self.session('finished', [self.prompt('x')] + self.reply('Done.\n\n✅ All done.'), age=week)
        procs = [{'pid': pid, 'ppid': 1, 'args': 'C:\\Users\\m\\AppData\\Roaming\\Claude\\claude-code\\x\\claude.exe --output-format stream-json'}
                 for pid in (101, 102, 104, 105, 106)] + [{'pid': 103, 'ppid': 1, 'args': 'notepad.exe'}]
        got = {s['id']: s for s in live.claude_live(self.now, [self.root], procs, {}, live.claude_registry(reg_dir, procs))}
        self.assertEqual(sorted(got), ['busy', 'finished', 'fresh', 'old-cc', 'quiet'])
        self.assertEqual(got['finished']['state'], 'done')  # the registry says idle, the reply says nothing is left
        self.assertEqual(got['old-cc']['state'], 'waiting')  # no status reported: quiet and open
        self.assertEqual((got['quiet']['state'], got['quiet']['title']), ('waiting', 'Agent architecture'))
        self.assertAlmostEqual(got['quiet']['last'], self.now - 60, delta=2)
        self.assertEqual(got['busy']['state'], 'working')
        self.assertEqual(got['busy']['title'], 'auto name')
        self.assertEqual((got['fresh']['title'], got['fresh']['project']), ('brand new', 'demo'))
        self.assertEqual(got['fresh']['state'], 'done')  # opened, never prompted: nothing waits on you

    def test_a_finished_turn_with_background_work_is_still_working(self):
        reg_dir = os.path.join(self.tmp.name, 'sessions')
        os.makedirs(reg_dir)
        for pid, sid in ((201, 'bg-build'), (202, 'bg-ask'), (203, 'bg-helper'), (204, 'bg-none')):
            with open(os.path.join(reg_dir, f'{pid}.json'), 'w') as f:
                json.dump({'pid': pid, 'sessionId': sid, 'cwd': '/Users/x/Projects/demo', 'status': 'idle',
                           'statusUpdatedAt': (self.now - 60) * 1000}, f)
            self.session(sid, [self.prompt('go')] + self.reply('Running it.'), age=60)
        snap = "/bin/zsh -c source /Users/x/.claude/shell-snapshots/snapshot-zsh-1.sh 2>/dev/null || true && eval '{}' < /dev/null && pwd -P"
        procs = [{'pid': pid, 'ppid': 1, 'args': 'claude --output-format stream-json'} for pid in (201, 202, 203, 204)]
        procs += [{'pid': 301, 'ppid': 201, 'args': snap.format('npm run build')},
                  {'pid': 302, 'ppid': 301, 'args': 'node build.js'},
                  {'pid': 303, 'ppid': 202, 'args': snap.format('tasks wait 148')},
                  {'pid': 304, 'ppid': 204, 'args': 'node mcp-server.js'}]  # an MCP server is not work
        helper_dir = os.path.join(self.dir, 'bg-helper', 'subagents')
        os.makedirs(helper_dir)
        with open(os.path.join(helper_dir, 'agent-1.jsonl'), 'w') as f:
            f.write('{}\n')
        os.utime(os.path.join(helper_dir, 'agent-1.jsonl'), (self.now - 100, self.now - 100))
        got = {s['id']: s for s in live.claude_live(self.now, [self.root], procs, {}, live.claude_registry(reg_dir, procs))}
        self.assertEqual((got['bg-build']['state'], got['bg-build']['doing']), ('working', 'Background: $ npm run build'))
        self.assertEqual((got['bg-ask']['state'], got['bg-ask']['doing']), ('waiting', 'Waiting for your answer on http://beacon/decisions'))
        self.assertEqual((got['bg-helper']['state'], got['bg-helper']['doing']), ('working', '1 helper agent working'))
        self.assertEqual(got['bg-none']['state'], 'waiting')

    def test_jobs_are_read_from_state_files(self):
        jobs = os.path.join(self.tmp.name, 'jobs')
        for name, state in (('a', 'blocked'), ('b', 'done')):
            os.makedirs(os.path.join(jobs, name))
            with open(os.path.join(jobs, name, 'state.json'), 'w') as f:
                json.dump({'sessionId': 'sess-' + name, 'state': state, 'name': name.upper()}, f)
        self.assertEqual(live.claude_jobs(jobs), {'sess-a': {'over': False, 'name': 'A'},
                                                  'sess-b': {'over': True, 'name': 'B'}})


class OtherAgentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = time.time()

    def tearDown(self):
        self.tmp.cleanup()

    def test_codex_turns_and_subagents(self):
        root = os.path.join(self.tmp.name, 'sessions')
        meta = lambda i, **kw: {'type': 'session_meta', 'payload': dict({'id': i, 'cwd': '/Users/x/Projects/demo'}, **kw)}
        write_jsonl(os.path.join(root, '2026', 'rollout-a.jsonl'), [
            meta('a'), {'type': 'turn_context', 'payload': {'model': 'gpt-5.5', 'cwd': '/Users/x/Projects/demo'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'text': 'ship it'}]}},
            {'type': 'event_msg', 'payload': {'type': 'task_started'}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'shell',
                                                  'arguments': json.dumps({'command': ['bash', '-lc', 'pytest -q']})}}])
        write_jsonl(os.path.join(root, '2026', 'rollout-b.jsonl'), [
            meta('b'), {'type': 'event_msg', 'payload': {'type': 'task_complete', 'last_agent_message': 'All green.'}}])
        write_jsonl(os.path.join(root, '2026', 'rollout-c.jsonl'), [meta('c', parent_thread_id='a')])
        got = {s['id']: s for s in live.codex_live(self.now, root)}
        self.assertEqual(sorted(got), ['a', 'b'])
        self.assertEqual((got['a']['state'], got['a']['doing'], got['a']['prompt'], got['a']['model']),
                         ('working', '$ pytest -q', 'ship it', 'gpt-5.5'))
        self.assertEqual((got['b']['state'], got['b']['doing']), ('waiting', 'All green.'))

    def test_hermes_reads_turns_from_its_database(self):
        db = os.path.join(self.tmp.name, 'state.db')
        con = sqlite3.connect(db)
        con.executescript('''CREATE TABLE sessions (id TEXT, title TEXT, display_name TEXT, model TEXT, cwd TEXT,
                             ended_at REAL, archived INTEGER DEFAULT 0, parent_session_id TEXT);
                             CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                             tool_calls TEXT, tool_name TEXT, timestamp REAL);''')
        con.executemany('INSERT INTO sessions(id, title, model, ended_at) VALUES (?,?,?,?)',
                        [('s1', 'Morning brief', 'glm', None), ('s2', None, 'glm', None), ('s3', None, 'glm', 1)])
        call = json.dumps([{'function': {'name': 'web_search', 'arguments': '{"query": "x"}'}}])
        con.executemany('INSERT INTO messages(session_id, role, content, tool_calls, timestamp) VALUES (?,?,?,?,?)', [
            ('s1', 'user', 'brief me', None, self.now - 30), ('s1', 'assistant', None, call, self.now - 20),
            ('s2', 'user', 'thanks', None, self.now - 60), ('s2', 'assistant', 'Anytime.', None, self.now - 50),
            ('s2', 'session_meta', None, None, self.now - 49),
            ('s3', 'user', 'old', None, self.now - 10)])
        con.commit()
        con.close()
        got = {s['id']: s for s in live.hermes_live(self.now, db)}
        self.assertEqual(sorted(got), ['s1', 's2'])
        self.assertEqual((got['s1']['state'], got['s1']['doing'], got['s1']['title'], got['s1']['project']),
                         ('working', 'Using web_search', 'Morning brief', 'hermes'))
        self.assertEqual((got['s2']['state'], got['s2']['doing'], got['s2']['prompt']), ('waiting', 'Anytime.', 'thanks'))

    def test_opencode_prefers_v2_rows_and_reads_v1_where_v2_never_ran(self):
        db = os.path.join(self.tmp.name, 'opencode.db')
        ms = lambda ago: round((self.now - ago) * 1000)
        con = sqlite3.connect(db)
        con.executescript('''
            CREATE TABLE session (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT,
                                  time_updated INTEGER, time_archived INTEGER);
            CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT,
                                     time_updated INTEGER, time_archived INTEGER);''')
        con.executemany('INSERT INTO session VALUES (?,?,?,?,?,?,?)', [
            ('ses_old', None, '/Users/x/Projects/demo', 'old', 'z-1', ms(30), None),
            ('ses_a', None, '/Users/x/Projects/demo', 'a', 'z-1', ms(40), None),
            ('ses_arch', None, '/Users/x/Projects/demo', 'archived', 'z-1', ms(50), None)])
        con.executemany('INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?)', [
            ('ses_old', None, '/Users/x/Projects/demo', 'old', 'z-1', ms(10), None),  # V2 row is the live one
            ('ses_new', None, '/Users/x/Projects/demo', 'new', json.dumps({'id': 'q-2.1', 'providerID': 'alibaba'}), ms(5), None),
            ('ses_arch', None, '/Users/x/Projects/demo', 'archived', 'z-1', ms(50), ms(20)),  # V2 archived it
            ('ses_sub', 'ses_a', '/Users/x/Projects/demo', 'sub', 'z-1', ms(5), None)])
        con.commit()
        con.close()
        got = {s['id']: s for s in live.opencode_live(self.now, db, [])}
        # ses_old: read from V2, not the stale V1 copy; ses_a: V1-only but recent, kept;
        # ses_arch: V2 archived it even though the V1 table still lists it; ses_sub: a
        # subagent, not a session.
        self.assertEqual(sorted(got), ['ses_a', 'ses_new', 'ses_old'])
        self.assertAlmostEqual(got['ses_old']['last'], self.now - 10, delta=1)  # last is rounded to whole seconds
        self.assertEqual(got['ses_new']['model'], 'q-2.1')
        self.assertAlmostEqual(got['ses_a']['last'], self.now - 40, delta=1)
        # An install that never ran V2 still shows the sessions in the V1 table.
        db2 = os.path.join(self.tmp.name, 'v1.db')
        con = sqlite3.connect(db2)
        con.executescript('CREATE TABLE session (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT, '
                          'time_updated INTEGER, time_archived INTEGER);')
        con.execute('INSERT INTO session VALUES (?,?,?,?,?,?,?)',
                    ('ses_v1', None, '/Users/x/Projects/demo', 'v1', 'glm', ms(5), None))
        con.commit()
        con.close()
        self.assertEqual([s['id'] for s in live.opencode_live(self.now, db2, [])], ['ses_v1'])

    def test_a_broken_source_does_not_blank_the_snapshot(self):
        orig = live.codex_live
        live.codex_live = lambda *a: 1 / 0
        try:
            snap = live.snapshot()
        finally:
            live.codex_live = orig
        self.assertIsInstance(snap['sessions'], list)


class HubLiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, 'u.db'))

    def tearDown(self):
        self.tmp.cleanup()

    def snap(self, *sessions):
        return {'scanned_at': time.time(), 'sessions': list(sessions)}

    def sess(self, sid, state, **kw):
        return dict({'agent': 'Claude Code', 'id': sid, 'project': 'demo', 'state': state, 'last': time.time() - 30}, **kw)

    def test_latest_snapshot_per_machine_with_session_ids_matching_events(self):
        self.store.ingest('mac', [], 2, {}, self.snap(self.sess('a', 'working', doing='Editing x.py')))
        self.store.ingest('mac', [], 2, {}, self.snap(self.sess('b', 'waiting')))
        self.store.ingest('pc', [], 2, {}, self.snap())
        got = {m['machine']: m for m in self.store.live_now()['machines']}
        self.assertEqual([s['id'] for s in got['mac']['sessions']], ['Claude Code:b'])
        self.assertTrue(got['pc']['online'])
        self.assertEqual(got['mac']['sessions'][0]['machine'], 'mac')
        self.assertAlmostEqual(got['mac']['sessions'][0]['idle_s'], 30, delta=3)

    def test_a_silent_machine_is_offline_with_no_sessions(self):
        self.store.ingest('mac', [], 2, {}, self.snap(self.sess('a', 'working')))
        self.store.ingest('old', [], 1, {})  # an old collector: known machine, never sent a snapshot
        with self.store.connect() as con:
            con.execute('UPDATE live SET seen_at = seen_at - 1000')
        got = {m['machine']: m for m in self.store.live_now()['machines']}
        self.assertEqual((got['mac']['online'], got['mac']['sessions']), (False, []))
        self.assertEqual((got['old']['online'], got['old']['seen_at']), (False, None))

    def test_bad_sessions_are_dropped_and_fields_cleaned(self):
        self.store.ingest('mac', [], 2, {}, self.snap(
            self.sess('ok', 'waiting', helpers=True, last='soon', prompt='key sk-ant-' + 'a' * 40),
            self.sess('bad', 'exploded'), 'junk', {'state': 'working'}))
        got = self.store.live()
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]['helpers'], got[0]['last'], got[0]['idle_s']), (0, None, None))
        self.assertNotIn('aaaa', got[0]['prompt'])

    def test_a_sessions_steps_are_capped_redacted_and_typed(self):
        steps = [{'text': f's{i}', 'at': i} for i in range(15)] + [
            'junk', {'text': 5}, {'text': ' '}, {'text': 'said', 'said': True}, {'text': 'key sk-ant-' + 'a' * 40, 'at': True}]
        self.store.ingest('mac', [], 2, {}, self.snap(self.sess('a', 'working', steps=steps),
                                                      self.sess('b', 'working', steps='not a list')))
        got = {s['id']: s['steps'] for s in self.store.live()}
        self.assertEqual([x['text'] for x in got['Claude Code:a']][:6], ['s10', 's11', 's12', 's13', 's14', 'said'])
        self.assertTrue(got['Claude Code:a'][-2]['said'])
        self.assertNotIn('said', got['Claude Code:a'][-1])
        self.assertNotIn('aaaa', got['Claude Code:a'][-1]['text'])
        self.assertIsNone(got['Claude Code:a'][-1]['at'])
        self.assertEqual(got['Claude Code:b'], [])

    def test_running_now_strip_is_most_urgent_first(self):
        self.store.ingest('mac', [], 2, {}, self.snap(self.sess('w', 'working'), self.sess('i', 'idle'),
                                                      self.sess('q', 'waiting'), self.sess('d', 'done')))
        self.assertEqual([x['state'] for x in self.store.live()], ['waiting', 'working', 'idle'])
        # a done session is kept in /api/live (readers show it as finished), just not as running
        self.assertIn('done', [x['state'] for x in self.store.live_now()['machines'][0]['sessions']])


if __name__ == '__main__':
    unittest.main()
