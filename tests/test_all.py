import json
import os
import re
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'collector'), os.path.join(ROOT, 'hub')]

import collect  # noqa: E402
import pricing  # noqa: E402
from store import Store, bursts_of, label_rank  # noqa: E402


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')


def claude_assistant(rid, ts, block, usage, model='claude-opus-5-5'):
    return {'type': 'assistant', 'timestamp': ts, 'requestId': rid, 'cwd': '/Users/x/Projects/demo',
            'message': {'model': model, 'content': [block], 'usage': usage}}


USAGE = {'input_tokens': 10, 'output_tokens': 500, 'cache_read_input_tokens': 100000,
         'cache_creation_input_tokens': 3000,
         'cache_creation': {'ephemeral_5m_input_tokens': 1000, 'ephemeral_1h_input_tokens': 2000}}


class PricingTest(unittest.TestCase):
    def test_opus_5_5_list_price(self):
        # $4 in, $5 5m write, $8 1h write, $0.20 read, $20 out per MTok
        c = pricing.cost('claude-opus-5-5', 'standard', input=1e6, cache_write_5m=1e6,
                         cache_write_1h=1e6, cache_read=1e6, output=1e6)
        self.assertAlmostEqual(c, 4 + 5 + 8 + 0.2 + 20)

    def test_fable_5_1_cheap_cache_reads(self):
        self.assertAlmostEqual(pricing.cost('claude-fable-5-1', 'standard', cache_read=1e6), 0.25)
        self.assertAlmostEqual(pricing.cost('claude-fable-5', 'standard', cache_read=1e6), 1.0)

    def test_older_and_other_claude(self):
        self.assertAlmostEqual(pricing.cost('claude-opus-4-8', 'standard', output=1e6), 25)
        self.assertAlmostEqual(pricing.cost('claude-opus-4-1', 'standard', output=1e6), 75)
        self.assertAlmostEqual(pricing.cost('claude-sonnet-5', 'standard', input=1e6), 2)
        self.assertAlmostEqual(pricing.cost('claude-haiku-4-5-20251001', 'standard', input=1e6), 1)

    def test_fast_mode(self):
        self.assertAlmostEqual(pricing.cost('claude-opus-5-5', 'fast', input=1e6, output=1e6), 48)
        self.assertAlmostEqual(pricing.cost('claude-opus-5-5', 'fast', cache_read=1e6), 0.4)

    def test_unknown_models_are_unpriced(self):
        for m in ('MiniMax-M2.7', 'minimax-m3.5', 'gemini (unreported)', 'gpt-5.5', 'stealth/ox-alpha', None):
            self.assertIsNone(pricing.cost(m, 'standard', input=1000))

    def test_minimax_m3_uses_its_makers_list_price(self):
        for m in ('MiniMax-M3', 'minimax/minimax-m3-20260531'):  # $0.30 in, $0.06 cache read, $1.20 out per MTok
            self.assertAlmostEqual(pricing.cost(m, 'standard', input=1e6, cache_read=1e6, output=1e6), 1.56)


class ProjectTest(unittest.TestCase):
    def test_names(self):
        p = collect.project_of
        self.assertEqual(p('/Users/alex/Projects/jev-fit/ios'), 'jev-fit')
        self.assertEqual(p('/Users/alex/Projects/jev-fit/.claude/worktrees/fix-x'), 'jev-fit')
        self.assertEqual(p('/root/projects/budget'), 'budget')
        self.assertEqual(p(r'C:\Users\murcj\Godot Projects\ayrus'), 'ayrus')
        self.assertEqual(p('/Users/alex/Projects/parked/old-app'), 'old-app')
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as d:  # a git worktree beside its repo is that repo (not under /tmp: that is scratch)
            os.makedirs(os.path.join(d, 'Projects', 'shop-fix', 'web'))
            with open(os.path.join(d, 'Projects', 'shop-fix', '.git'), 'w') as f:
                f.write(f'gitdir: {d}/Projects/shop/.git/worktrees/shop-fix\n')
            self.assertEqual(p(os.path.join(d, 'Projects', 'shop-fix', 'web')), 'shop')
        self.assertEqual(p('/Users/alex/Projects/ai-brain-wt-win'), 'ai-brain')
        self.assertEqual(p('/private/tmp/claude-501/x/scratchpad'), 'scratch')
        self.assertEqual(p('/Users/me/Library/Application Support/Claude/scratch-workspaces/e8/38/scratch-2026-09-12-43c75d'), 'scratch')
        self.assertEqual(p('/Users/alex/Projects'), '(projects root)')
        self.assertEqual(p(None), '(no project)')


class ClaudeCollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, 'projects')
        self.main = os.path.join(self.root, '-Users-x-Projects-demo', 'sess1.jsonl')
        write_jsonl(self.main, [
            {'type': 'user', 'timestamp': '2026-09-24T10:00:00Z', 'uuid': 'u1', 'cwd': '/Users/x/Projects/demo',
             'message': {'role': 'user', 'content': 'fix the bug'}},
            # one request streamed as two lines: same usage, two tool_use blocks
            claude_assistant('r1', '2026-09-24T10:00:05Z', {'type': 'tool_use', 'id': 't1'}, USAGE),
            claude_assistant('r1', '2026-09-24T10:00:06Z', {'type': 'tool_use', 'id': 't2'}, USAGE),
            {'type': 'user', 'timestamp': '2026-09-24T10:00:07Z', 'uuid': 'u2',
             'message': {'role': 'user', 'content': [{'type': 'tool_result', 'content': 'ok'}]}},
            {'type': 'user', 'timestamp': '2026-09-24T10:00:08Z', 'uuid': 'u3',
             'message': {'role': 'user', 'content': '<system-reminder>x</system-reminder>'}},
            {'type': 'user', 'timestamp': '2026-09-24T10:00:09Z', 'uuid': 'u4',
             'message': {'role': 'user', 'content': '<command-message>review</command-message>\n<command-name>/review</command-name>\n<command-args>low</command-args>'}},
            claude_assistant('r2', '2026-09-24T10:00:10Z', {'type': 'text', 'text': 'x'}, USAGE, model='<synthetic>'),
        ])
        sub = os.path.join(self.root, '-Users-x-Projects-demo', 'sess1', 'subagents', 'agent-a.jsonl')
        write_jsonl(sub, [
            {'type': 'user', 'timestamp': '2026-09-24T10:01:00Z', 'uuid': 's1', 'isSidechain': True,
             'message': {'role': 'user', 'content': 'subagent task'}},
            claude_assistant('r3', '2026-09-24T10:01:05Z', {'type': 'text', 'text': 'y'}, USAGE, model='claude-haiku-4-5-20251001'),
        ])
        self.state = collect.State(os.path.join(self.tmp.name, 'state.json'))
        self.state.pending = {}

    def tearDown(self):
        self.tmp.cleanup()

    def run_collect(self):
        events = collect.collect_claude(self.state, 'mac', [self.root])
        for k, v in self.state.pending.items():
            self.state.set(k, v)
        self.state.pending = {}
        return events

    def test_parses_prompts_calls_and_subagents(self):
        events = self.run_collect()
        prompts = [e for e in events if e['kind'] == 'prompt']
        calls = {e['id']: e for e in events if e['kind'] == 'call'}
        self.assertEqual([p['text'] for p in prompts], ['fix the bug', '/review low'])
        self.assertEqual(set(calls), {'claude:r1', 'claude:r3'})
        r1 = calls['claude:r1']
        self.assertEqual(r1['tools'], 2)
        self.assertEqual((r1['output'], r1['cache_write_5m'], r1['cache_write_1h']), (500, 1000, 2000))
        self.assertEqual(r1['project'], 'demo')
        self.assertEqual(calls['claude:r3']['session'], 'Claude Code:sess1')
        self.assertEqual(calls['claude:r3']['subagent'], 1)

    def test_second_run_reads_only_new_lines(self):
        self.run_collect()
        self.assertEqual(self.run_collect(), [])
        write_jsonl(self.main, [claude_assistant('r9', '2026-09-24T11:00:00Z', {'type': 'text', 'text': 'z'}, USAGE)])
        self.assertEqual([e['id'] for e in self.run_collect()], ['claude:r9'])


class PushFailureTest(unittest.TestCase):
    def test_an_unreachable_hub_is_one_line_and_nothing_is_marked_sent(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, 'HOME': tmp, 'AGENT_USAGE_HUB': 'http://127.0.0.1:9', 'AGENT_USAGE_TOKEN': 'x'}
            state = os.path.join(tmp, 'state.json')
            r = subprocess.run([sys.executable, collect.__file__, '--state', state, '--machine', 't'],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn('could not push', r.stderr)
            self.assertNotIn('Traceback', r.stderr)
            self.assertFalse(os.path.exists(state))


class CodexCollectorTest(unittest.TestCase):
    def test_subagent_runs_count_toward_their_parent_session(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tok = lambda ts, n: {'type': 'event_msg', 'timestamp': ts, 'ordinal': n, 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': {'total_tokens': n * 100}, 'last_token_usage': {'input_tokens': 50, 'output_tokens': 5}}}}
        write_jsonl(os.path.join(tmp.name, 'sessions', '2026', 'rollout-a.jsonl'), [
            {'type': 'session_meta', 'timestamp': '2026-09-24T10:00:00Z', 'payload': {'id': 'P', 'cwd': '/x/demo'}},
            {'type': 'response_item', 'timestamp': '2026-09-24T10:00:01Z', 'ordinal': 1,
             'payload': {'type': 'message', 'role': 'user', 'content': [{'text': 'build it'}]}},
            tok('2026-09-24T10:00:02Z', 2)])
        guardian = os.path.join(tmp.name, 'sessions', '2026', 'rollout-b.jsonl')
        write_jsonl(guardian, [
            {'type': 'session_meta', 'timestamp': '2026-09-24T10:00:03Z', 'payload': {
                'id': 'G', 'session_id': 'P', 'parent_thread_id': 'P', 'thread_source': 'subagent', 'cwd': '/x/demo'}},
            {'type': 'response_item', 'timestamp': '2026-09-24T10:00:03Z', 'ordinal': 1, 'payload': {
                'type': 'message', 'role': 'user', 'content': [{'text': 'The following is the Codex agent history…'}]}},
            tok('2026-09-24T10:00:04Z', 2)])
        state = collect.State(os.path.join(tmp.name, 'state.json'))
        state.pending = {}
        events = collect.collect_codex(state, 'pc', os.path.join(tmp.name, 'sessions'))
        self.assertEqual([e['text'] for e in events if e['kind'] == 'prompt'], ['build it'])
        calls = sorted((e['id'], e['session'], e['subagent']) for e in events if e['kind'] == 'call')
        self.assertEqual(calls, [('codex:G:2', 'Codex:P', 1), ('codex:P:2', 'Codex:P', 0)])
        save = lambda: [state.set(k, v) for k, v in state.pending.items()] and setattr(state, 'pending', {})
        save()
        # Archived threads (moved out of sessions/) are read too.
        write_jsonl(os.path.join(tmp.name, 'archived_sessions', 'rollout-c.jsonl'), [
            {'type': 'session_meta', 'timestamp': '2026-09-24T09:00:00Z', 'payload': {'id': 'A', 'cwd': '/x/demo'}},
            tok('2026-09-24T09:00:02Z', 2)])
        self.assertEqual([e['id'] for e in collect.collect_codex(state, 'pc', os.path.join(tmp.name, 'sessions'))], ['codex:A:2'])
        save()
        # A state from before this fix re-reads only the subagent file, once.
        del state.data['codex-subagents-v1']
        state.pending = {}
        self.assertEqual({e['id'] for e in collect.collect_codex(state, 'pc', os.path.join(tmp.name, 'sessions'))}, {'codex:G:2'})


class OpenCodeCollectorTest(unittest.TestCase):
    def test_v2_stores_messages_in_session_message(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = os.path.join(tmp.name, 'opencode.db')
        oc_id = lambda ms: 'msg_' + format((ms % (1 << 36)) << 12, '012x')  # ids carry their mint time
        t0 = 1792246314000  # ms
        con = sqlite3.connect(db)
        con.executescript('''
            CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT,
                                     time_created INTEGER, time_updated INTEGER, time_archived INTEGER);
            CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER,
                                          time_created INTEGER, time_updated INTEGER, data TEXT);''')
        ins = 'INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?)'
        con.execute(ins, ('ses_a', None, 'C:/Users/x/Projects/demo', 'a', '{"id":"qwen3.5-flash"}', t0, t0, None))
        con.execute(ins, ('ses_sub', 'ses_a', 'C:/Users/x/Projects/demo', 's', None, t0, t0, None))
        con.execute('INSERT INTO session_message VALUES (?,?,?,?,?,?,?)',
                    (oc_id(t0), 'ses_a', 'user', 1, t0, t0, json.dumps({'time': {'created': t0}, 'text': 'ship the fix'})))
        con.execute('INSERT INTO session_message VALUES (?,?,?,?,?,?,?)',
                    (oc_id(t0 + 1000), 'ses_a', 'assistant', 2, t0 + 1000, t0 + 1000, json.dumps({
                        'time': {'created': t0 + 1000}, 'agent': 'build',
                        'model': {'id': 'qwen3.5-flash', 'providerID': 'alibaba'},
                        'content': [{'type': 'tool', 'name': 'shell'}, {'type': 'text', 'text': 'done'}],
                        'finish': 'tool-calls', 'cost': 0.002,
                        'tokens': {'input': 6, 'output': 26, 'reasoning': 0, 'cache': {'read': 0, 'write': 18446}}})))
        con.execute('INSERT INTO session_message VALUES (?,?,?,?,?,?,?)',
                    (oc_id(t0 + 2000), 'ses_a', 'idle', 3, t0 + 2000, t0 + 2000,
                     json.dumps({'time': {'created': t0 + 2000}, 'outcome': 'succeeded'})))
        con.execute('INSERT INTO session_message VALUES (?,?,?,?,?,?,?)',
                    (oc_id(t0 + 3000), 'ses_sub', 'assistant', 1, t0 + 3000, t0 + 3000, json.dumps({
                        'time': {'created': t0 + 3000}, 'model': {'id': 'qwen3.5-flash'}, 'content': [],
                        'tokens': {'input': 1, 'output': 2, 'cache': {}}})))
        con.commit()
        con.close()
        state = collect.State(os.path.join(tmp.name, 'st.json'))
        state.pending = {}

        def run_collect():  # run() folds pending into saved state after a good push
            events = collect.collect_opencode(state, 'gamer', db)
            for k, v in state.pending.items():
                state.set(k, v)
            state.pending = {}
            return events

        events = run_collect()
        self.assertEqual([e['kind'] for e in events], ['prompt', 'call', 'call'])
        prompt, call, sub = events
        self.assertEqual((prompt['session'], prompt['text'], prompt['project']), ('OpenCode:ses_a', 'ship the fix', 'demo'))
        self.assertEqual((call['session'], call['model'], call['tools'], call['cost_reported']),
                         ('OpenCode:ses_a', 'qwen3.5-flash', 1, 0.002))
        self.assertEqual((call['input'], call['output'], call['cache_write_5m']), (6, 26, 18446))
        self.assertEqual((sub['session'], sub['subagent']), ('OpenCode:ses_a', 1))  # a subagent folds into its parent
        self.assertEqual(state.get(db)['since'], t0 + 3000)
        # The watermark holds: an unchanged db adds nothing on the next pass.
        self.assertEqual(run_collect(), [])
        # A box that never ran V2 still reads its V1 tables, side by side.
        con = sqlite3.connect(db)
        con.executescript('''
            CREATE TABLE session AS SELECT * FROM session_v2;
            CREATE TABLE message (id TEXT, session_id TEXT, time_updated INTEGER, data TEXT);
            CREATE TABLE part (message_id TEXT, data TEXT);''')
        con.execute('INSERT INTO message VALUES (?,?,?,?)',
                    (oc_id(t0 + 5000), 'ses_a', t0 + 5000, json.dumps({'role': 'user', 'time': {'created': t0 + 5000}})))
        con.execute('INSERT INTO part VALUES (?,?)', (oc_id(t0 + 5000), json.dumps({'type': 'text', 'text': 'from the old table'})))
        con.commit()
        con.close()
        self.assertEqual([(e['kind'], e['text']) for e in run_collect()],
                         [('prompt', 'from the old table')])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, 'u.db'))
        self.start, _ = self.store.day_bounds('2026-09-24')

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, id, minute, session='Claude Code:s1', **kw):
        return {'id': id, 'agent': 'Claude Code', 'session': session, 'project': 'demo', 'kind': 'call',
                'ts': self.start + minute * 60, 'model': 'claude-opus-5-5', 'output': 1_000_000, **kw}

    def test_a_session_started_in_projects_root_belongs_to_the_repo_it_worked_in(self):
        from store import main_project
        self.assertEqual(main_project({'(projects root)': 275, 'kovo-preklad': 183, 'alarm': 87}), 'kovo-preklad')
        self.assertEqual(main_project({'(projects root)': 300, 'budget': 5}), '(projects root)')  # a peek isn't the work
        self.assertEqual(main_project({'(projects root)': 3}), '(projects root)')
        self.assertEqual(main_project({}), '(no project)')

    def test_bursts(self):
        self.assertEqual(bursts_of([0, 100, 2000]), [(0, 160), (2000, 2060)])

    def test_a_session_with_only_local_commands_is_not_a_session(self):
        p = lambda id, sess, text: {'id': id, 'agent': 'Claude Code', 'session': sess, 'project': 'demo',
                                    'kind': 'prompt', 'ts': self.start + 600, 'text': text}
        self.store.ingest('mac', [p('a', 'Claude Code:empty', '/clear'), p('b', 'Claude Code:real', 'fix it')])
        self.assertEqual([x['id'] for x in self.store.day('2026-09-24')['sessions']], ['Claude Code:real'])
        self.assertIsNone(self.store.session('mac', 'Claude Code:empty'))

    def test_a_loop_waiting_for_its_timer_is_not_active(self):
        fire = lambda id, minute: {'id': id, 'agent': 'Claude Code', 'session': 'Claude Code:s1', 'project': 'demo',
                                   'kind': 'prompt', 'ts': self.start + minute * 60, 'text': '[scheduled] /loop check'}
        events = []
        for k in range(6):  # a fire every 10 minutes, each one call a few seconds later
            events += [fire(f'f{k}', 10 * k), self.call(f'c{k}', 10 * k + 0.1)]
        self.store.ingest('mac', events)
        self.assertLessEqual(self.store.day('2026-09-24')['totals']['active_min'], 6 * 2)

    def test_ingest_is_idempotent_and_day_totals(self):
        events = [self.call('a', 60), self.call('b', 62),
                  self.call('c', 61, session='Claude Code:s2'),
                  {'id': 'p', 'agent': 'Claude Code', 'session': 'Claude Code:s1', 'project': 'demo',
                   'kind': 'prompt', 'ts': self.start + 60 * 60, 'text': 'hi'},
                  {'id': 'p0', 'agent': 'Claude Code', 'session': 'Claude Code:s1', 'project': 'demo',
                   'kind': 'prompt', 'ts': self.start + 59 * 60, 'text': '/clear'}]
        self.store.ingest('mac', events)
        self.store.ingest('mac', events)
        d = self.store.day('2026-09-24')
        t = d['totals']
        self.assertEqual((t['sessions'], t['calls'], t['prompts'], t['peak']), (2, 3, 1, 2))  # /clear isn't a prompt
        self.assertAlmostEqual(t['cost'], 60.0)  # 3M output tokens at $20/MTok
        self.assertEqual(d['sessions'][0]['first_prompt'], 'hi')

    def test_an_impossible_timestamp_is_skipped_not_fatal(self):
        bad = [self.call('ms', 10, ts=(self.start + 600) * 1000), self.call('nan', 11, ts=float('nan')),
               self.call('inf', 12, ts=float('inf')), self.call('ok', 13)]
        self.store.ingest('mac', bad)
        self.assertEqual(self.store.day('2026-09-24')['totals']['calls'], 1)

    def test_a_non_string_field_is_coerced_not_fatal(self):
        self.store.ingest('mac', [self.call('p', 10, project=42), self.call('l', 11, project=['a'], model=7)])
        d = self.store.day('2026-09-24')
        self.assertEqual(d['totals']['calls'], 2)
        self.assertEqual(self.store.session('mac', 'Claude Code:s1')['projects'], {'42': 1, "['a']": 1})

    def test_reported_cost_wins_and_unpriced_is_flagged(self):
        self.store.ingest('mac', [self.call('g', 10, model='grok-4.7', cost_reported=1.5),
                                  self.call('m', 20, model='stealth/ox-alpha')])
        t = self.store.day('2026-09-24')['totals']
        self.assertAlmostEqual(t['cost'], 1.5)
        self.assertEqual(t['unpriced_calls'], 1)

    def test_priced_minutes_leave_out_sessions_with_no_price(self):
        self.store.ingest('mac', [self.call('a', 60), self.call('b', 62),
                                  self.call('c', 200, session='Codex:x', model='gpt-9', agent='Codex'),
                                  self.call('d', 205, session='Codex:x', model='gpt-9', agent='Codex')])
        t = self.store.day('2026-09-24')['totals']
        self.assertEqual((t['active_min'], t['priced_min']), (9, 3))

    def test_reported_zero_on_a_call_with_tokens_is_not_a_price(self):
        # OpenCode logs $0 for subscription calls: unpriced, or list price when there is one.
        self.store.ingest('mac', [self.call('s', 10, model='gpt-5.4', cost_reported=0),
                                  self.call('c', 20, model='claude-opus-5', cost_reported=0),
                                  {**self.call('e', 30, model='gpt-5.4', cost_reported=0), 'output': 0}])
        t = self.store.day('2026-09-24')['totals']
        self.assertEqual(t['unpriced_calls'], 1)  # the empty call really cost nothing
        self.assertAlmostEqual(t['cost'], 25.0)   # 1M output tokens at the Opus 5 list price

    def test_cached_day_refreshes_on_ingest(self):
        self.store.ingest('mac', [self.call('a', 60)])
        self.assertEqual(self.store.day('2026-09-24')['totals']['calls'], 1)
        self.store.ingest('mac', [self.call('b', 70)])
        self.assertEqual(self.store.day('2026-09-24')['totals']['calls'], 2)

    def test_build_racing_an_ingest_is_not_cached(self):
        self.store.ingest('mac', [self.call('a', 60)])
        rows = self.store.rows

        def ingest_mid_build(*args):
            out = rows(*args)
            self.store.rows = rows
            self.store.ingest('mac', [self.call('b', 70)])  # lands after the read, before caching
            return out
        self.store.rows = ingest_mid_build
        self.assertEqual(self.store.day('2026-09-24')['totals']['calls'], 1)
        self.assertEqual(self.store.day('2026-09-24')['totals']['calls'], 2)

    def test_prefiltered_views_match_python_filtering(self):
        import store as store_mod
        self.store.ingest('mac', [
            self.call('a', 60), self.call('b', 61, project='other'),  # s1: demo is the main project
            self.call('c', 62, session='Codex:x', agent='Codex', project='other', model='gpt-5.5'),
            self.call('d', 63, session='Claude Code:s3', project=None),
            self.call('e', 64, session='Claude Code:s4', project='other')])
        for filt in [('Codex', None, None), (None, 'mac', None), (None, None, 'demo'), (None, None, 'other'),
                     (None, None, '(no project)'), ('Claude Code', None, 'other')]:
            got = self.store.day('2026-09-24', filt)['totals']
            s, e = self.store.day_bounds('2026-09-24')
            want = store_mod.finalize(store_mod.aggregate(self.store.rows(s, e), s, e, filt))['totals']
            self.assertEqual((got['sessions'], got['calls']), (want['sessions'], want['calls']), filt)

    def test_session_across_midnight_is_labelled_from_all_its_days(self):
        prompt = lambda id, minute, text: {'id': id, 'agent': 'Claude Code', 'session': 'Claude Code:s1',
                                           'project': 'demo', 'kind': 'prompt', 'ts': self.start + minute * 60,
                                           'text': text}
        self.store.ingest('mac', [prompt('p0', 23 * 60 + 50, '/clear'),
                                  self.call('a', 23 * 60 + 51, model='claude-sonnet-5', project='other'),
                                  prompt('p1', 24 * 60 + 5, 'fix the login bug'),
                                  *[self.call(f'b{i}', 24 * 60 + 6 + i) for i in range(3)]])
        s = self.store.range('2026-09-24', '2026-09-25')['sessions'][0]
        full = self.store.session('mac', 'Claude Code:s1')
        self.assertEqual((s['first_prompt'], s['model'], s['project']), ('fix the login bug', 'claude-opus-5-5', 'demo'))
        self.assertEqual((full['first_prompt'], full['model'], full['project']), (s['first_prompt'], s['model'], s['project']))

    def test_past_day_baseline_is_the_mean_of_days(self):
        # the same project and session on each of the 7 days before: 1 project, 1 session per day
        self.store.ingest('mac', [self.call(f'd{i}', -i * 24 * 60 + 600) for i in range(1, 8)] + [self.call('t', 600)])
        b = self.store.day('2026-09-24')['baseline']
        self.assertEqual((b['projects'], b['sessions']), (1, 1))

    def test_project_names_fold_scratch_workspaces_and_case(self):
        path = os.path.join(self.tmp.name, 'u.db')
        self.store.ingest('mac', [self.call('a', 1, project='llfxwatch'), self.call('b', 2, project='llfxwatch'),
                                  self.call('c', 3, project='scratch-2026-09-12-43c75d')])
        self.store.ingest('pc', [self.call('d', 4, session='Claude Code:s2', project='LLFXWatch')])
        with self.store.connect() as con:  # a row written before folding existed
            con.execute("UPDATE events SET project = 'LLFXWatch' WHERE id = 'b'")
        store2 = Store(path)
        names = {r['project'] for r in store2.rows(self.start, self.start + 86400)}
        self.assertEqual(names, {'llfxwatch', 'scratch'})
        store2.ingest('pc', [self.call('e', 5, project='LlFxWatch'), self.call('f', 6, project='scratch-2026-09-30-a1b2')])
        self.assertEqual({r['project'] for r in store2.rows(self.start, self.start + 86400)}, {'llfxwatch', 'scratch'})

    def test_side_checkouts_fold_into_their_repo(self):
        path = os.path.join(self.tmp.name, 'u.db')
        self.store.ingest('mac', [self.call(f'a{i}', i, project='linux-setup') for i in range(3)]
                          + [self.call('b', 5, project='linux-setup-rc'), self.call('c', 6, project='linux-setup-rc-gym-site'),
                             self.call('d', 7, project='budget-preview')])
        store2 = Store(path)  # history folds on start; a project with no bigger base stays
        self.assertEqual({r['project'] for r in store2.rows(self.start, self.start + 86400)}, {'linux-setup', 'budget-preview'})
        store2.ingest('pc', [self.call('e', 8, project='linux-setup-tsfix')])  # and new ones fold on arrival
        self.assertEqual({r['project'] for r in store2.rows(self.start, self.start + 86400)}, {'linux-setup', 'budget-preview'})

    def test_search_finds_any_prompt(self):
        prompt = lambda id, sess, minute, text: {'id': id, 'agent': 'Claude Code', 'session': sess, 'project': 'demo',
                                                 'kind': 'prompt', 'ts': self.start + minute * 60, 'text': text}
        self.store.ingest('mac', [prompt('a', 's1', 10, 'first thing'), prompt('b', 's1', 20, 'later: fix 100% of Login'),
                                  prompt('c', 's2', 30, 'Zkontroluj ÚČET v bance')])
        keys = lambda q, d='2026-09-24': [k for k, _ in self.store.search(q, d, d)]
        self.assertEqual(self.store.search('login', '2026-09-24', '2026-09-24'), [['mac|s1', 'later: fix 100% of Login']])
        self.assertEqual(keys('100%'), ['mac|s1'])
        self.assertEqual(keys('0% of', '2026-09-25'), [])
        self.assertEqual(keys('ucet'), ['mac|s2'])  # case and accents fold
        self.assertEqual(keys('účet'), ['mac|s2'])
        self.assertEqual(keys('%'), ['mac|s1'])

    def test_search_snippet_is_around_the_match(self):
        from store import fold, snippet
        text = 'x' * 300 + ' Účet v bance ' + 'y' * 300
        s = snippet(text, fold('ucet'))
        self.assertTrue(s.startswith('…') and s.endswith('…') and 'Účet v bance' in s)
        self.assertEqual(snippet('short Účet', fold('účet')), 'short Účet')

    def test_palette_slots_survive_a_reshuffle(self):
        first = self.store.palette(['a', 'b', 'c'])
        self.assertEqual(first, {'a': 1, 'b': 2, 'c': 3})
        self.assertEqual(self.store.palette(['c', 'a', 'b']), first)  # ranking changed, colors didn't
        swapped = self.store.palette(['c', 'a', 'd'])  # b dropped out, d is new
        self.assertEqual((swapped['c'], swapped['a']), (3, 1))
        self.assertEqual(swapped['d'], 4)  # a never-used slot first, so b's can come back
        self.assertEqual(self.store.palette(['b', 'c', 'a'])['b'], 2)

    def test_carried_over_session_says_when_it_began(self):
        self.store.ingest('mac', [self.call('a', 23 * 60 + 50), self.call('b', 24 * 60 + 5)])
        s = self.store.day('2026-09-25')['sessions'][0]
        self.assertEqual(s['began'], self.start + (23 * 60 + 50) * 60)
        self.assertNotIn('began', self.store.day('2026-09-24')['sessions'][0])

    def test_day_cost_by_hour_and_its_baseline(self):
        self.store.ingest('mac', [self.call('a', 60), self.call('b', 20 * 60), self.call('w', -24 * 60 + 60)])
        d = self.store.day('2026-09-24')
        self.assertAlmostEqual(d['cost_by_hour'][1], 20.0)
        self.assertAlmostEqual(d['cost_by_hour'][20], 20.0)
        self.assertAlmostEqual(d['baseline_cost_by_hour'][1], 20.0 / 7, places=3)

    def test_filters_keep_whole_sessions(self):
        self.store.ingest('mac', [self.call('a', 60), self.call('b', 61, session='Codex:x', agent='Codex',
                                                                 project='other', model='gpt-5.5')])
        d = self.store.day('2026-09-24', ('Codex', None, None))
        self.assertEqual([s['project'] for s in d['sessions']], ['other'])
        self.assertEqual(self.store.day('2026-09-24', (None, None, 'demo'))['totals']['calls'], 1)

    def test_range_counts_a_midnight_session_once_and_compares(self):
        # 23:50 on the 24th and 00:05 on the 25th, same session; one call a week earlier
        self.store.ingest('mac', [self.call('a', 23 * 60 + 50), self.call('b', 24 * 60 + 5),
                                  self.call('old', -6 * 24 * 60)])
        r = self.store.range('2026-09-24', '2026-09-25')
        self.assertEqual((r['totals']['sessions'], r['totals']['calls']), (1, 2))
        self.assertEqual([d['calls'] for d in r['days']], [1, 1])
        self.assertEqual([d['active_by_agent'] for d in r['days']], [{'Claude Code': 1}, {'Claude Code': 1}])
        self.assertAlmostEqual(r['days'][0]['cost_by_agent']['Claude Code'], 20.0)
        self.assertEqual([x['calls'] for x in r['sessions']], [2])
        self.assertIsNone(r['previous'])
        self.assertEqual(self.store.range('2026-09-18', '2026-09-24')['previous'], None)
        self.assertEqual(self.store.range('2026-09-25', '2026-10-01')['previous']['calls'], 2)

    def test_today_compares_against_the_same_time_of_day(self):
        import store as store_mod
        # a past day with calls at 01:00 and 20:00, compared up to 02:00
        self.store.ingest('mac', [self.call('a', 60, session='Claude Code:old'),
                                  self.call('b', 20 * 60, session='Claude Code:old2')])
        aggs = self.store.day_aggs(['2026-09-24']).values()
        b = store_mod.average_until(aggs, 2)
        self.assertAlmostEqual(b['cost'], 20.0)  # only the 01:00 call counts
        self.assertEqual(b['sessions'], 1)
        self.assertEqual(store_mod.average_until(aggs, 24)['calls'], 2)

    def test_session_turns_own_the_calls_after_them(self):
        prompt = lambda id, minute, text: {'id': id, 'agent': 'Claude Code', 'session': 'Claude Code:s1',
                                           'project': 'demo', 'kind': 'prompt', 'ts': self.start + minute * 60,
                                           'text': text}
        self.store.ingest('mac', [self.call('pre', 9), prompt('p1', 10, 'one'), self.call('a', 11),
                                  self.call('b', 12), prompt('p2', 20, 'two'), self.call('c', 21, output=0)])
        turns = self.store.session('mac', 'Claude Code:s1')['turns']
        self.assertEqual([(t['text'], t['calls']) for t in turns], [(None, 1), ('one', 2), ('two', 1)])
        self.assertAlmostEqual(turns[1]['cost'], 40.0)
        self.assertEqual(turns[2]['cost'], 0)


    def test_text_no_human_typed_is_dropped_and_purged(self):
        prompt = lambda id, text, agent='Claude Code': {'id': id, 'agent': agent, 'session': f'{agent}:s1', 'project': 'demo',
                                                        'kind': 'prompt', 'ts': self.start + 600, 'text': text}
        self.store.ingest('mac', [prompt('a', '<bash-input> security add-generic-password -w secret</bash-input>'),
                                  prompt('b', 'The following is the Codex agent history whose…', 'Codex'),
                                  prompt('d', '3 background agents were stopped by the user: "a", "b"'),
                                  prompt('e', 'Background agent "ASR" was stopped by the user.'),
                                  prompt('c', 'a real request')])
        self.assertEqual(self.store.day('2026-09-24')['totals']['prompts'], 1)
        # Rows an older hub stored are removed when the hub starts.
        with self.store.connect() as con:
            con.execute("INSERT INTO events(id, machine, agent, session, project, ts, kind, text) "
                        "VALUES ('old', 'mac', 'Codex', 'Codex:s9', 'demo', ?, 'prompt', "
                        "'The following is the Codex agent history x')", (self.start + 700,))
        store = Store(self.store.path)
        self.assertEqual(store.day('2026-09-24')['totals']['prompts'], 1)

    def test_local_commands_are_activity_not_prompts_or_turns(self):
        prompt = lambda id, minute, text: {'id': id, 'agent': 'Claude Code', 'session': 'Claude Code:s1',
                                           'project': 'demo', 'kind': 'prompt', 'ts': self.start + minute * 60,
                                           'text': text}
        self.store.ingest('mac', [prompt('p1', 10, 'fix it'), self.call('a', 11), prompt('m', 12, '/model opus'),
                                  self.call('b', 13), prompt('g', 14, '/goal ship it'), self.call('c', 15)])
        d = self.store.day('2026-09-24')
        self.assertEqual(d['totals']['prompts'], 2)  # /goal runs the model; /model doesn't
        turns = self.store.session('mac', 'Claude Code:s1')['turns']
        self.assertEqual([(t['text'], t['calls']) for t in turns], [('fix it', 2), ('/goal ship it', 1)])
        self.store.ingest('mac', [prompt('f', 30, '[scheduled] /loop check the site'), self.call('d', 31)])
        self.assertEqual(self.store.day('2026-09-24')['totals']['prompts'], 2)  # a re-fire nobody typed…
        self.assertEqual(len(self.store.session('mac', 'Claude Code:s1')['turns']), 3)  # …but it is a turn

    def test_a_typed_request_labels_a_session_a_scheduled_fire_opened(self):
        prompt = lambda id, minute, text: {'id': id, 'agent': 'Claude Code', 'session': 'Claude Code:s2',
                                           'project': 'demo', 'kind': 'prompt', 'ts': self.start + minute * 60,
                                           'text': text}
        self.store.ingest('mac', [prompt('s', 10, '[scheduled] /loop check the site'), self.call('a', 11, session='Claude Code:s2'),
                                  prompt('t', 20, 'why is it down?'), self.call('b', 21, session='Claude Code:s2')])
        self.assertEqual(self.store.day('2026-09-24')['sessions'][0]['first_prompt'], 'why is it down?')
        self.assertLess(label_rank('why?', 9), label_rank('[scheduled] check', 1))

    def test_resent_event_can_move_to_its_parent_session(self):
        self.store.ingest('mac', [self.call('a', 10, session='Codex:P'), self.call('g', 11, session='Codex:G')])
        self.assertEqual(self.store.day('2026-09-24')['totals']['sessions'], 2)
        self.store.ingest('mac', [self.call('g', 11, session='Codex:P', subagent=1, model='codex-auto-review'),
                                  self.call('g2', 12, session='Codex:P', subagent=1, model='codex-auto-review')])
        d = self.store.day('2026-09-24')
        self.assertEqual((d['totals']['sessions'], d['totals']['calls']), (1, 3))
        self.assertEqual(d['sessions'][0]['model'], 'claude-opus-5-5')  # its own calls, not the reviewer's

    def test_every_breakdown_adds_up_to_the_totals(self):
        import random
        rnd = random.Random(7)
        events = []
        for i in range(300):
            agent = rnd.choice(['Claude Code', 'Codex', 'Grok'])
            sess = f'{agent}:s{rnd.randrange(12)}'
            ts = self.start - 3 * 3600 + rnd.randrange(2 * 86400)  # across two midnights
            e = {'id': f'e{i}', 'agent': agent, 'session': sess, 'project': rnd.choice(['a', 'b', 'c', None]),
                 'ts': ts, 'kind': rnd.choice(['call', 'call', 'prompt'])}
            if e['kind'] == 'call':
                e.update(model=rnd.choice(['claude-opus-5-5', 'claude-sonnet-5', 'mystery-1']), output=rnd.randrange(10**5),
                         cache_read=rnd.randrange(10**6), subagent=rnd.random() < 0.2)
            else:
                e['text'] = rnd.choice(['do it', '/model x', 'fix'])
            events.append(e)
        self.store.ingest(rnd.choice(['mac']), events)
        for filt in [(None, None, None), ('Codex', None, None), (None, None, 'a')]:
            for d in (self.store.range('2026-09-23', '2026-09-25', filt), self.store.day('2026-09-24', filt)):
                t = d['totals']
                for tab in ('by_project', 'by_agent', 'by_machine', 'by_model'):
                    self.assertAlmostEqual(sum(x['cost'] for x in d[tab]), t['cost'], places=2)
                    self.assertEqual(sum(x['calls'] for x in d[tab]), t['calls'])
                self.assertEqual(len(d['sessions']), t['sessions'])
                self.assertAlmostEqual(sum(x['cost'] for x in d['sessions']), t['cost'], places=2)
                self.assertEqual(sum(x['prompts'] for x in d['sessions']), t['prompts'])
                if 'days' in d:
                    self.assertAlmostEqual(sum(sum(x['cost_by_project'].values()) for x in d['days']), t['cost'], places=2)
                    self.assertEqual(sum(map(sum, d['hours'])), sum(x['active_min'] for x in d['days']))
                else:
                    self.assertAlmostEqual(sum(d['cost_by_hour']), t['cost'], places=2)

    def test_odd_text_does_not_reject_the_batch(self):
        n = self.store.ingest('mac', [{'id': 'x', 'agent': 'Claude Code', 'session': 'Claude Code:s1', 'project': 'demo',
                                       'kind': 'prompt', 'ts': self.start + 60, 'text': ['a', 'list']},
                                      self.call('c', 2, output='lots', cache_read=None)])
        self.assertEqual(n, 2)
        self.assertEqual(self.store.day('2026-09-24')['totals']['calls'], 1)

    def test_a_forked_log_does_not_steal_the_calls_it_copied(self):
        self.store.ingest('mac', [self.call('r1', 10, session='Claude Code:orig'), self.call('r2', 11, session='Claude Code:orig')])
        self.store.ingest('mac', [self.call('r1', 10, session='Claude Code:fork'), self.call('r3', 20, session='Claude Code:fork')])
        sessions = {x['id']: x['calls'] for x in self.store.day('2026-09-24')['sessions']}
        self.assertEqual(sessions, {'Claude Code:orig': 2, 'Claude Code:fork': 1})

    def test_opencode_fork_copies_are_recognised(self):
        created = 1782822677.562
        self.assertFalse(collect.opencode_copy('msg_f18832c3a001H5HQKjjMNvSNW1', created))  # the original
        self.assertTrue(collect.opencode_copy('msg_f18f9be3e001HN1AV0rcDlLBee', created))   # minted 2h later
        from store import opencode_copy
        self.assertTrue(opencode_copy('msg_f190035e5001twy1OClwX7qRu1', created))

    def test_codex_prompt_keeps_what_was_typed(self):
        wrapped = ('# Files mentioned by the user:\n## shot.png: C:/tmp/shot.png\n## My request for Codex:\n'
                   'the problem must be on your side\n<image name=[Image #1] path="C:\\tmp\\shot.png">\n</image>')
        self.assertEqual(collect.codex_request(wrapped), 'the problem must be on your side')
        self.assertEqual(collect.codex_request('plain request'), 'plain request')
        self.store.ingest('pc', [{'id': 'c', 'agent': 'Codex', 'session': 'Codex:s', 'project': 'demo', 'kind': 'prompt',
                                  'ts': self.start + 60, 'text': '# Response annotations:\nx\n## My request:\nwhy?'}])
        self.assertEqual(self.store.day('2026-09-24')['sessions'][0]['first_prompt'], 'why?')

    def test_pasted_keys_are_redacted(self):
        key = 'sk-proj-' + 'Ab3' * 12
        self.store.ingest('mac', [{'id': 'k', 'agent': 'Claude Code', 'session': 'Claude Code:s1', 'project': 'demo',
                                   'kind': 'prompt', 'ts': self.start + 600, 'text': f'use {key} for the task-runner'}])
        text = self.store.day('2026-09-24')['sessions'][0]['first_prompt']
        self.assertEqual(text, 'use [redacted key] for the task-runner')
        self.assertEqual(collect.clip(f'token ghp_{"x1" * 18} ok'), 'token [redacted key] ok')

    def test_collector_marks_scheduled_refires(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        write_jsonl(os.path.join(tmp.name, 'p', 's.jsonl'), [
            {'type': 'user', 'timestamp': '2026-09-24T10:00:00Z', 'uuid': 'a', 'message': {'content': 'check the site'}},
            {'type': 'user', 'timestamp': '2026-09-24T10:10:00Z', 'uuid': 'b', 'scheduledFireId': 'x',
             'message': {'content': 'check the site'}}])
        state = collect.State(os.path.join(tmp.name, 'st.json'))
        state.pending = {}
        texts = [e['text'] for e in collect.collect_claude(state, 'mac', [tmp.name]) if e['kind'] == 'prompt']
        self.assertEqual(texts, ['check the site', '[scheduled] check the site'])

    def test_collector_reads_session_names(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        write_jsonl(os.path.join(tmp.name, 'p', 's.jsonl'), [
            {'type': 'user', 'timestamp': '2026-09-24T10:00:00Z', 'uuid': 'a', 'message': {'content': 'check the site'}},
            {'type': 'ai-title', 'aiTitle': 'Site check', 'sessionId': 's'},
            {'type': 'custom-title', 'customTitle': 'Weekly site check', 'sessionId': 's'},
            {'type': 'ai-title', 'aiTitle': 'Later guess', 'sessionId': 's'}])
        write_jsonl(os.path.join(tmp.name, 'p', 't.jsonl'), [{'type': 'ai-title', 'aiTitle': 'Only ai', 'sessionId': 't'}])
        state = collect.State(os.path.join(tmp.name, 'st.json'))
        events = collect.collect_claude(state, 'mac', [tmp.name])
        self.assertEqual([e['kind'] for e in events], ['prompt'])  # a name is never an event
        self.assertEqual(state.names, {
            'Claude Code:s': {'session': 'Claude Code:s', 'title': 'Weekly site check', 'source': 'custom'},
            'Claude Code:t': {'session': 'Claude Code:t', 'title': 'Only ai', 'source': 'ai'}})

    def test_headless_runs_are_automated_turns_and_notices_are_dropped(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        u = lambda uid, text, **kw: {'type': 'user', 'timestamp': '2026-09-24T10:0%s:00Z' % uid, 'uuid': uid,
                                     'message': {'content': text}, **kw}
        path = os.path.join(tmp.name, 'p', 's.jsonl')
        write_jsonl(path, [
            u('1', 'Reply with just: ready', entrypoint='sdk-cli', promptSource='sdk'),          # a script
            u('2', 'continue', entrypoint='sdk-cli', promptSource='sdk', turnOrigin='human'),   # remote control
            u('3', 'ok do the demo GIF', entrypoint='claude-desktop', promptSource='sdk'),       # the desktop app
            u('4', 'Goal set: ship it', promptSource='system')])
        state = collect.State(os.path.join(tmp.name, 'st.json'))
        state.pending = {}
        texts = [e['text'] for e in collect.collect_claude(state, 'mac', [tmp.name]) if e['kind'] == 'prompt']
        self.assertEqual(texts, ['[scheduled] Reply with just: ready', 'continue', 'ok do the demo GIF'])
        # A log read before this rule is read once more so the hub gets the marked text.
        state.data.update(state.pending, **{path: {'offset': os.path.getsize(path), 'ctx': {}}})
        state.data.pop('claude-sdk-v1')
        state.pending = {}
        self.assertEqual(len([e for e in collect.collect_claude(state, 'mac', [tmp.name]) if e['kind'] == 'prompt']), 3)
        self.store.ingest('mac', [{'id': 'g', 'agent': 'Claude Code', 'session': 'Claude Code:s', 'project': 'demo',
                                   'kind': 'prompt', 'ts': self.start + 60, 'text': 'Goal set: ship it'}])
        self.assertEqual(self.store.day('2026-09-24')['totals']['prompts'], 0)

    def test_collector_skips_shell_commands_typed_with_bang(self):
        o = {'type': 'user', 'message': {'content': '<bash-input>git push</bash-input>'}}
        self.assertIsNone(collect.claude_prompt_text(o))

    def test_collector_keeps_a_prompt_behind_the_desktop_reminder(self):
        reminder = {'type': 'text', 'text': '<system-reminder>\nThe user started this session without choosing a project folder…\n</system-reminder>\n\n'}
        o = {'type': 'user', 'message': {'content': [reminder, {'type': 'text', 'text': 'where did we finish?'}]}}
        self.assertEqual(collect.claude_prompt_text(o), 'where did we finish?')
        self.assertIsNone(collect.claude_prompt_text({'type': 'user', 'message': {'content': [reminder]}}))

    def test_collector_reads_messages_typed_mid_run(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        q = lambda did, prompt, origin, ts='2026-09-24T10:01:00Z': {
            'type': 'attachment', 'timestamp': ts, 'uuid': 'u' + did, 'attachment': {
                'type': 'queued_command', 'prompt': prompt, 'delivery_id': did, 'commandMode': 'prompt',
                'origin': {'kind': origin}, 'humanTurn': origin == 'human'}}
        path = os.path.join(tmp.name, 'p', 's.jsonl')
        write_jsonl(path, [
            {'type': 'user', 'timestamp': '2026-09-24T10:00:00Z', 'uuid': 'a', 'message': {'content': 'build it'}},
            q('d1', 'also fix the logo', 'human'),
            q('d2', '<task-notification>\n<status>completed</status>', 'task-notification'),
            q('d3', [{'type': 'text', 'text': 'and the footer'}], 'human'),
            {'type': 'queue-operation', 'operation': 'remove', 'timestamp': '2026-09-24T10:01:01Z', 'content': 'also fix the logo'}])
        state = collect.State(os.path.join(tmp.name, 'st.json'))
        state.pending = {}
        prompts = [(e['id'], e['text']) for e in collect.collect_claude(state, 'mac', [tmp.name]) if e['kind'] == 'prompt']
        self.assertEqual(prompts, [('claude-u:a', 'build it'), ('claude-q:d1', '[mid-run] also fix the logo'),
                                   ('claude-q:d3', '[mid-run] and the footer')])
        # A log read before this rule is read once more, so history gets them too.
        state.data.update(state.pending, **{path: {'offset': os.path.getsize(path), 'ctx': {}}})
        state.data.pop('claude-queued-v1')
        state.pending = {}
        self.assertEqual(len([e for e in collect.collect_claude(state, 'mac', [tmp.name]) if e['kind'] == 'prompt']), 3)

    def prompt(self, id, minute, text, session='Claude Code:s1'):
        return {'id': id, 'agent': 'Claude Code', 'session': session, 'project': 'demo', 'kind': 'prompt',
                'ts': self.start + minute * 60, 'text': text}

    def layers(self, date='2026-09-24'):
        t = self.store.day(date)['totals']
        return {k: t[f'tokens_{k}'] // 1_000_000 for k in ('driven', 'solo', 'subagent', 'unattended')}

    def test_tokens_split_by_who_drove_them(self):
        import store as store_mod
        n = store_mod.DRIVEN_CALLS
        calls = lambda tag, at, k, **kw: [self.call(f'{tag}{i}', at + i / 100, **kw) for i in range(k)]
        self.store.ingest('mac', [
            self.prompt('p1', 10, 'build the page'), *calls('a', 10, n + 5),  # n driven, then 5 alone
            *calls('s', 11, 3, subagent=1),
            self.prompt('p2', 20, '[mid-run] smaller logo'), *calls('b', 20, 2),  # his message resets the count
            self.prompt('p3', 30, '[scheduled] check the site'), *calls('c', 30, 4), *calls('cs', 31, 1, subagent=1),
            self.prompt('p4', 40, '/model sonnet'), *calls('d', 40, 1),  # a local command changes nothing
        ])
        self.assertEqual(self.layers(), {'driven': n + 2, 'solo': 5, 'subagent': 3, 'unattended': 6})
        t = self.store.day('2026-09-24')['totals']
        self.assertEqual((t['asks'], t['nudges'], t['steers'], t['prompts']), (1, 0, 1, 2))
        self.assertAlmostEqual(t['hands_off'], round(14 / (n + 16), 4))

    def test_a_turn_carried_past_midnight_keeps_its_count(self):
        import store as store_mod
        n = store_mod.DRIVEN_CALLS
        self.store.ingest('mac', [
            self.prompt('p1', 23 * 60, 'run the whole migration'),
            *[self.call(f'a{i}', 23 * 60 + i / 100) for i in range(n - 2)],
            *[self.call(f'b{i}', 24 * 60 + 5 + i / 100) for i in range(4)],  # the next day: 2 more driven, 2 alone
            self.prompt('q1', 23 * 60, '[scheduled] nightly check', session='Claude Code:s2'),
            self.call('c', 24 * 60 + 5, session='Claude Code:s2'),
            self.call('e', 24 * 60 + 5, session='Claude Code:s3'),  # no prompt ever: nobody typed it
        ])
        self.assertEqual(self.layers('2026-09-25'), {'driven': 2, 'solo': 2, 'subagent': 0, 'unattended': 2})

    def test_nudges_are_prompts_that_only_keep_it_going(self):
        from store import prompt_kind
        for t in ('continue', 'Continue.', 'go ahead', 'pokračuj', 'OK, continue', 'yes'):
            self.assertEqual(prompt_kind(t), 'nudge', t)
        for t in ('merge', 'continue with the footer', 'yes, but keep the old logo', 'drop it'):
            self.assertEqual(prompt_kind(t), 'ask', t)
        self.assertEqual(prompt_kind('[mid-run] continue'), 'steer')

    def test_turns_carry_their_exact_tokens(self):
        self.store.ingest('mac', [self.prompt('p1', 10, 'one'), self.call('a', 11), self.call('b', 12, cache_read=500)])
        turns = self.store.session('mac', 'Claude Code:s1')['turns']
        self.assertEqual(turns[0]['tokens'], 2_000_500)



class ServerTest(unittest.TestCase):
    """The HTTP layer: auth on writes, static caching, errors as JSON."""

    def setUp(self):
        import threading
        import server
        from http.server import ThreadingHTTPServer
        self.tmp = tempfile.TemporaryDirectory()
        server.TOKEN = 'secret'
        server.Handler.store = Store(os.path.join(self.tmp.name, 'u.db'))
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def req(self, path, data=None, headers=None):
        import urllib.error
        import urllib.request
        r = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_ingest_needs_the_token(self):
        body = json.dumps({'machine': 'm', 'events': []}).encode()
        self.assertEqual(self.req('/api/ingest', body)[0], 401)
        self.assertEqual(self.req('/api/ingest', body, {'Authorization': 'Bearer secret'})[0], 200)
        self.assertEqual(self.req('/api/ingest', body, {'Authorization': 'Bearer secret', 'Content-Length': 'x'})[0], 413)

    def test_static_files_gzip_and_revalidate(self):
        code, headers, body = self.req('/app.js', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual((code, headers.get('Content-Encoding')), (200, 'gzip'))
        self.assertEqual(self.req('/app.js', headers={'If-None-Match': headers['ETag']})[0], 304)
        self.assertEqual(self.req('/../hub/store.py')[0], 404)

    def test_pages_version_their_files_so_they_cache_for_good(self):
        code, headers, body = self.req('/tasks')
        self.assertEqual((code, headers.get('Cache-Control')), (200, 'no-cache'))
        m = re.search(r'href="app\.css\?v=(\w+)"', body.decode())
        self.assertIsNotNone(m, 'the page links app.css with its version')
        code, headers, _ = self.req('/app.css?v=' + m.group(1))
        self.assertEqual((code, headers.get('Cache-Control')), (200, 'public, max-age=31536000, immutable'))

    def test_head_gets_the_headers_without_a_body(self):
        import urllib.request
        r = urllib.request.Request(self.base + '/app.js', method='HEAD')
        with urllib.request.urlopen(r) as resp:
            self.assertEqual((resp.status, resp.read()), (200, b''))
            self.assertGreater(int(resp.headers['Content-Length']), 0)
        r = urllib.request.Request(self.base + '/api/meta', method='HEAD')
        with urllib.request.urlopen(r) as resp:
            self.assertEqual((resp.status, resp.read()), (200, b''))
        self.assertEqual(self.req('/app.js')[0], 200)
        self.assertGreater(len(self.req('/app.js')[2]), 0)

    def test_bad_range_is_a_400(self):
        code, _, body = self.req('/api/range?from=2026-01-01&to=2020-01-01')
        self.assertEqual(code, 400)
        self.assertIn('error', json.loads(body))
        self.assertEqual(self.req('/api/range?from=2026-01-01')[0], 400)  # no "to"

    def test_a_bug_in_the_store_is_a_500_not_a_bad_request(self):
        import contextlib
        import io
        import server
        server.Handler.store.range = lambda *a: {}['boom']
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.req('/api/range?from=2026-01-01&to=2026-01-02')[0], 500)


if __name__ == '__main__':
    unittest.main()
