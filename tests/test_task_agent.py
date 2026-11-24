"""The task agent (client/task-agent) against a real hub, with a stub in place of Claude Code."""
import importlib.machinery
import json
import importlib.util
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tasks import ROOT, LiveHub  # noqa: E402

STUB = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
if '--session-id' not in sys.argv and '--resume' not in sys.argv:  # the router's call
    with open(os.environ['STUB_SEEN'] + '.route', 'a') as f:
        f.write(sys.argv[sys.argv.index('-p') + 1] + '\n---\n')
    route = json.loads(os.environ.get('STUB_ROUTE') or '{"route": "human", "why": "needs his card"}')
    print(json.dumps({'result': json.dumps(route), 'structured_output': route, 'is_error': False}))
    sys.exit(0)
sid = sys.argv[sys.argv.index('--session-id' if '--session-id' in sys.argv else '--resume') + 1]
os.environ['CLAUDE_CODE_SESSION_ID'] = sid
mode, tid, cli = os.environ['STUB_MODE'], os.environ['STUB_TASK'], os.environ['STUB_CLI']
with open(os.environ['STUB_SEEN'], 'w') as f:
    json.dump({'cwd': os.getcwd(), 'argv': sys.argv[1:]}, f)
if mode == 'pr':
    subprocess.run([cli, 'note', tid, '👉 PR https://github.com/o/r/pull/7 (feat/x → main): the fix'], check=True, stdout=sys.stderr)
if mode == 'ask':
    subprocess.run([cli, 'note', tid, '⏳ Needs you: which plan, monthly or yearly?'], check=True, stdout=sys.stderr)
if mode == 'progress':
    subprocess.run([cli, 'note', tid, 'Looked into it, half way there.'], check=True, stdout=sys.stderr)
if mode == 'close':
    subprocess.run([cli, 'done', tid, '-m', '✅ both backups are fresh'], check=True, stdout=sys.stderr)
if os.environ.get('STUB_WRITES'):  # a Studio write the gate logged during the run
    import datetime
    with open(os.environ['STUB_WRITES'], 'a') as f:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
        f.write(now + '\tstudio-gate\texecute_luau(Server)\n')  # the repro's ClaudeAgentPlay mark: gone with the Play
        f.write(now + '\tstudio-gate\tgame.ServerScriptService.Digger\n')
if mode == 'hang':
    import time
    time.sleep(30)
if mode == 'crash':
    sys.exit(3)
if os.environ.get('STUB_NOTE'):
    subprocess.run([cli, 'note', tid, os.environ['STUB_NOTE']], check=True, stdout=sys.stderr)
out = {'result': 'Opened PR 7: https://github.com/o/r/pull/7\n\n✅ All done.', 'is_error': False}
if os.environ.get('STUB_PHONE'):  # the structured answer --json-schema asks for
    out['structured_output'] = {'phone': os.environ['STUB_PHONE']}
if os.environ.get('STUB_RETRY'):
    out['structured_output'] = {'phone': 'Studio je zaneprázdněné.', 'retry': True}
print(json.dumps(out))
'''

PROVEN = {'reproduced_without_fix': True, 'criteria': [{'line': 'Klíč po resetu zůstane v ruce.', 'met': True}]}

GH_STUB = r'''#!/bin/sh
echo "$@" >> "$STUB_GH_LOG"
[ "$1 $2" = "pr view" ] && [ -n "$STUB_GH_STATE" ] && echo "$STUB_GH_STATE"
exit "${STUB_GH_EXIT:-0}"
'''


class TaskAgentTest(LiveHub, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        stub = os.path.join(self.dir.name, 'claude')
        with open(stub, 'w') as f:
            f.write(STUB)
        os.chmod(stub, 0o755)
        self.seen = os.path.join(self.dir.name, 'seen.json')
        gh = os.path.join(self.dir.name, 'gh')
        with open(gh, 'w') as f:
            f.write(GH_STUB)
        os.chmod(gh, 0o755)
        self.gh_log = os.path.join(self.dir.name, 'gh.log')
        self.told = os.path.join(self.dir.name, 'told')
        tell = os.path.join(self.dir.name, 'tell')
        with open(tell, 'w') as f:
            f.write('#!/bin/sh\nfor last; do :; done\nprintf "%s\\n===\\n" "$last" >> "$STUB_TOLD"\n')
        os.chmod(tell, 0o755)
        self.env = {k: os.environ.get(k) for k in (
            'TASKS_URL', 'TASKS_TOKEN', 'CLAUDE_CODE_SESSION_ID', 'GH_TOKEN', 'TASK_AGENT_CLAUDE', 'TASK_AGENT_TELL',
            'TASK_AGENT_PROJECTS', 'TASK_AGENT_WORK', 'STUB_MODE', 'STUB_TASK', 'STUB_CLI', 'STUB_SEEN',
            'TASK_AGENT_GH', 'TASK_AGENT_STATE', 'TASK_AGENT_ROUTE_SINCE', 'TASK_AGENT_MAX_RUNS',
            'STUB_ROUTE', 'STUB_GH_LOG', 'STUB_GH_EXIT', 'STUB_TOLD', 'TASK_AGENT_ROUTE_GRACE', 'STUB_PHONE', 'STUB_GH_STATE',
            'TASK_AGENT_SYSTEMCTL', 'STUB_SYSTEMCTL_LOG', 'STUB_NOTE', 'STUB_RETRY', 'STUB_WRITES', 'TASK_AGENT_SOCK_DIR')}
        os.environ.update(TASKS_URL=self.base, TASKS_TOKEN='tok', GH_TOKEN='x', TASK_AGENT_CLAUDE=stub,
                          TASK_AGENT_TELL=tell, STUB_TOLD=self.told, TASK_AGENT_GH=gh, STUB_GH_LOG=self.gh_log,
                          TASK_AGENT_STATE=os.path.join(self.dir.name, 'state'), TASK_AGENT_ROUTE_SINCE='0',
                          TASK_AGENT_ROUTE_GRACE='0',
                          TASK_AGENT_PROJECTS=self.dir.name,
                          TASK_AGENT_WORK=os.path.join(self.dir.name, 'work'),
                          STUB_CLI=os.path.join(ROOT, 'client', 'tasks'), STUB_SEEN=self.seen)
        systemctl = os.path.join(self.dir.name, 'systemctl')  # never the real one: it would stop a live jailed run
        with open(systemctl, 'w') as f:
            f.write('#!/bin/sh\necho "$@" >> "$STUB_SYSTEMCTL_LOG"\n')
        os.chmod(systemctl, 0o755)
        self.systemctl_log = os.path.join(self.dir.name, 'systemctl.log')
        os.environ.update(TASK_AGENT_SYSTEMCTL=systemctl, STUB_SYSTEMCTL_LOG=self.systemctl_log,
                          TASK_AGENT_SOCK_DIR=os.path.join(self.dir.name, 'socks'))
        for k in ('CLAUDE_CODE_SESSION_ID', 'STUB_ROUTE', 'STUB_GH_EXIT', 'TASK_AGENT_MAX_RUNS', 'STUB_PHONE', 'STUB_GH_STATE',
                  'STUB_NOTE', 'STUB_RETRY', 'STUB_WRITES'):
            os.environ.pop(k, None)
        loader = importlib.machinery.SourceFileLoader('task_agent', os.path.join(ROOT, 'client', 'task-agent'))
        spec = importlib.util.spec_from_loader('task_agent', loader)
        self.agent = importlib.util.module_from_spec(spec)
        loader.exec_module(self.agent)
        self.agent.login_lasts = lambda seconds: True  # never the real login file of the box running the tests
        self.agent.NOW = lambda: self.prague(12)  # daytime, whenever the suite runs: nothing held for the night
        self.tasks = self.server.Handler.tasks

    @staticmethod
    def prague(hour, minute=0, days=0):
        import datetime, zoneinfo
        day = datetime.datetime.now(zoneinfo.ZoneInfo('Europe/Prague')) + datetime.timedelta(days=days)
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.dir.cleanup()
        super().tearDown()

    def hand(self, title, mode, **fields):
        t = self.tasks.create({'title': title, 'tags': ['claude'], **fields})
        os.environ.update(STUB_MODE=mode, STUB_TASK=str(t['id']))
        return t

    def test_the_queue_is_handed_todo_tasks_most_urgent_first(self):
        self.tasks.create({'title': 'mine'})
        self.tasks.create({'title': 'parked', 'tags': ['claude', 'parked']})
        self.tasks.create({'title': 'normal', 'tags': ['claude'], 'priority': 3})
        self.tasks.create({'title': 'urgent', 'tags': ['claude'], 'priority': 1})
        self.tasks.create({'title': 'started', 'tags': ['claude'], 'status': 'doing'})
        self.assertEqual([t['title'] for t in self.agent.queue()], ['urgent', 'normal'])

    def test_a_task_marked_not_before_a_later_day_waits_for_it(self):
        self.tasks.create({'title': 'later', 'tags': ['claude'], 'body': '**Not before 2999-01-01**: needs data.'})
        self.tasks.create({'title': 'now', 'tags': ['claude'], 'body': 'Not before 2000-01-01.'})
        self.assertEqual([t['title'] for t in self.agent.queue()], ['now'])

    def test_an_empty_queue_does_nothing(self):
        self.tasks.create({'title': 'mine'})
        self.assertEqual(self.agent.main([]), 0)
        self.assertFalse(os.path.exists(self.seen))

    def test_a_pr_leaves_the_task_doing_with_its_session_linked(self):
        t = self.hand('Fix login', 'pr')
        self.assertEqual(self.agent.main([]), 0)
        t = self.tasks.get(t['id'])
        self.assertEqual(t['status'], 'doing')
        self.assertIn('claude', t['tags'])
        self.assertEqual(len(t['sessions']), 1)
        sid = t['sessions'][0]['session_id']
        self.assertTrue(sid.startswith('Claude Code:'))
        self.assertTrue(t['comments'][0]['text'].startswith('🤖 Picked up by the agent'))
        self.assertIn('pull/7', t['comments'][-1]['text'])
        import json
        seen = json.load(open(self.seen))
        self.assertIn(sid.split(':', 1)[1], seen['argv'])
        self.assertIn('auto', seen['argv'])
        self.assertEqual(os.path.realpath(seen['cwd']), os.path.realpath(self.dir.name))  # no repo for it: the projects folder

    def test_a_question_goes_back_to_him(self):
        t = self.hand('Pick a plan', 'ask')
        self.agent.main([])
        t = self.tasks.get(t['id'])
        self.assertEqual((t['status'], t['tags']), ('todo', []))
        self.assertEqual(self.agent.queue(), [])  # not picked up again until he hands it back

    def test_work_with_no_code_closes_the_task(self):
        t = self.hand('Check backups', 'close')
        self.assertEqual(self.agent.main([]), 0)
        self.assertEqual(self.tasks.get(t['id'])['status'], 'done')

    def test_a_run_that_says_nothing_is_given_back(self):
        t = self.hand('Vague thing', 'silent')
        self.assertEqual(self.agent.main([]), 1)
        t = self.tasks.get(t['id'])
        self.assertEqual((t['status'], t['tags']), ('todo', []))
        self.assertIn('without a note on the task', t['comments'][-1]['text'])

    def test_a_crash_is_given_back(self):
        t = self.hand('Crashy', 'crash', priority=1)
        self.assertEqual(self.agent.main([]), 1)
        t = self.tasks.get(t['id'])
        self.assertEqual(t['status'], 'todo')
        self.assertIn('exit 3', t['comments'][-1]['text'])

    def test_a_repo_task_runs_in_a_fresh_worktree(self):
        import subprocess
        repo = os.path.join(self.dir.name, 'origin.git')
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', repo], check=True, stdout=sys.stderr)
        work = os.path.join(self.dir.name, 'seed')
        subprocess.run(['git', 'clone', '-q', repo, work], check=True, capture_output=True)
        subprocess.run(['git', '-C', work, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'x'], check=True, stdout=sys.stderr)
        subprocess.run(['git', '-C', work, 'push', '-q', 'origin', 'main'], check=True, capture_output=True)
        subprocess.run(['git', 'clone', '-q', repo, os.path.join(self.dir.name, 'app')], check=True, capture_output=True)
        self.hand('Fix app', 'pr', project='app')
        self.agent.main([])
        import json
        cwd = json.load(open(self.seen))['cwd']
        self.assertTrue(os.path.realpath(cwd).startswith(os.path.join(os.path.realpath(self.dir.name), 'work', 'task-1-')), cwd)
        self.assertFalse(os.path.exists(cwd))  # clean afterwards: removed

    def app_repo(self, conf=None, files=()):
        import json, subprocess
        repo = os.path.join(self.dir.name, 'origin.git')
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', repo], check=True, stdout=sys.stderr)
        work = os.path.join(self.dir.name, 'seed')
        subprocess.run(['git', 'clone', '-q', repo, work], check=True, capture_output=True)
        if conf is not None:
            os.makedirs(os.path.join(work, '.claude', 'agent'))
            with open(os.path.join(work, '.claude', 'agent', 'task-agent.json'), 'w') as f:
                json.dump(conf, f)
        for name, text in files:
            with open(os.path.join(work, name), 'w') as f:
                f.write(text)
        subprocess.run(['git', '-C', work, 'add', '-A'], check=True)
        subprocess.run(['git', '-C', work, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'x'], check=True, stdout=sys.stderr)
        subprocess.run(['git', '-C', work, 'push', '-q', 'origin', 'main'], check=True, capture_output=True)
        subprocess.run(['git', 'clone', '-q', repo, os.path.join(self.dir.name, 'app')], check=True, capture_output=True)

    def test_scoped_mode_works_only_its_project_and_tag_and_routes_nothing(self):
        self.agent.ONLY = [('app', 'discord')]
        self.tasks.create({'title': 'other project', 'tags': ['claude', 'discord'], 'project': 'web'})
        self.tasks.create({'title': 'app, not from discord', 'tags': ['claude'], 'project': 'app'})
        self.tasks.create({'title': 'app bug', 'tags': ['claude', 'discord'], 'project': 'app'})
        self.tasks.create({'title': 'unrouted', 'project': 'app'})
        self.assertEqual([t['title'] for t in self.agent.queue()], ['app bug'])
        self.agent.work = lambda t, dry=False: 0
        self.agent.main([])
        self.assertFalse(os.path.exists(self.seen + '.route'))  # no router call

    def test_a_failed_preflight_leaves_the_task_queued_and_unclaimed(self):
        self.app_repo({'preflight': ['sh', 'pre.sh'], 'brief': 'brief.md'},
                      [('pre.sh', 'echo studio busy >&2; exit 1\n'), ('brief.md', 'Fix it.')])
        t = self.hand('Bug', 'ask', project='app')
        self.assertEqual(self.agent.main([]), 0)
        t = self.tasks.get(t['id'])
        self.assertEqual((t['status'], t['tags'], t['comments']), ('todo', ['claude'], []))
        self.assertFalse(os.path.exists(self.seen))
        self.assertEqual(os.listdir(os.path.join(self.dir.name, 'work')), [])  # its worktree is gone again

    def test_a_repo_run_gets_its_brief_settings_and_proof_photos(self):
        self.app_repo({'preflight': ['true'], 'brief': 'brief.md', 'settings': 's.json', 'mcp_config': 'm.json',
                       'env': {'CTS_AGENT': '1'}, 'proof_env': 'PROOF', 'from': 'X Game'},
                      [('brief.md', 'Repro task {id}, shots in {proof}.'), ('s.json', '{}'), ('m.json', '{}')])
        t = self.hand('Bug', 'ask', project='app')
        real = self.agent.run_claude
        def shoot(task, sid, where, text, conf=None, proof=None):
            os.makedirs(proof)
            open(os.path.join(proof, '1795272227-before-pole-tree.png'), 'w').close()
            open(os.path.join(proof, 'junk-x.png'), 'w').close()
            return real(task, sid, where, text, conf, proof)
        self.agent.run_claude = shoot
        self.assertEqual(self.agent.main([]), 0)
        import json
        argv = json.load(open(self.seen))['argv']
        self.assertIn(f"Repro task {t['id']}, shots in", argv[argv.index('-p') + 1])
        self.assertNotIn('ship-task', argv[argv.index('-p') + 1])
        for flag in ('--settings', '--mcp-config', '--strict-mcp-config', '--no-chrome'):
            self.assertIn(flag, argv)
        told = open(self.told).read()
        self.assertIn(f"📸 před: pole-tree (#{t['id']})", told)  # the photo's caption: what it shows
        self.assertNotIn('junk', told)
        self.assertIn('which plan', told)
        self.assertEqual(self.tasks.get(t['id'])['status'], 'todo')

    def test_the_list_carries_sessions_and_the_latest_note(self):
        t = self.tasks.create({'title': 'x'})
        self.tasks.link(t['id'], 'Claude Code:s1')
        self.tasks.comment(t['id'], 'first', 'owner', at=1)
        self.tasks.comment(t['id'], '👉 PR https://github.com/o/r/pull/7', 'agent', at=2)
        row = self.tasks.list()[0]
        self.assertEqual(row['sessions'], ['Claude Code:s1'])
        self.assertEqual((row['last_note']['author'], row['last_note']['text'][:4]), ('agent', '👉 PR'))
        self.assertEqual(self.tasks.list(status='open')[0]['last_note']['at'], 2)

    def said(self, session, *texts, machine='laptop'):
        """A session on another machine in which he typed these prompts."""
        import time as _t
        now = _t.time() - 3600
        self.server.Handler.store.ingest(machine, [
            {'id': f'{session}-{i}', 'agent': 'Claude Code', 'session': session, 'project': 'budget',
             'kind': 'prompt', 'ts': now + i * 60, 'text': text} for i, text in enumerate(texts)])

    def prompt_seen(self):
        import json
        argv = json.load(open(self.seen))['argv']
        return argv[argv.index('-p') + 1]

    def test_a_session_is_found_by_id_alone(self):
        self.said('Claude Code:filed', 'make the export a CSV')
        s = self.server.Handler.store.session(None, 'Claude Code:filed')
        self.assertEqual((s['machine'], s['turns'][0]['text']), ('laptop', 'make the export a CSV'))
        self.assertIsNone(self.server.Handler.store.session(None, 'Claude Code:nope'))

    def test_what_he_said_where_it_was_filed_reaches_the_agent(self):
        self.said('Claude Code:filed', 'the budget app crashes on an empty month',
                  '[scheduled] /loop check', 'file a task for it, keep the old totals')
        t = self.hand('Fix empty month', 'pr', body='## Done-test\nOctober opens.')
        self.tasks.link(t['id'], 'Claude Code:filed')
        self.agent.main([])
        p = self.prompt_seen()
        self.assertIn('> the budget app crashes on an empty month', p)
        self.assertIn('> file a task for it, keep the old totals', p)
        self.assertNotIn('/loop check', p)
        self.assertIn('on laptop', p)
        self.assertNotIn('has no Done-test', p)
        self.assertIn('code-review skill at\n`low`', p)
        self.assertIn('with what he said', self.tasks.get(t['id'])['comments'][0]['text'])

    def test_a_task_without_a_done_test_gets_one_first(self):
        self.hand('Make it nicer', 'pr')
        self.agent.main([])
        p = self.prompt_seen()
        self.assertIn('has no Done-test yet', p)
        self.assertNotIn('What he typed', p)  # no linked session: nothing to quote

    # ---------------------------------------------------------------- routing

    def messages(self):
        try:
            return [m.strip() for m in open(self.told).read().split('===') if m.strip()]
        except OSError:
            return []

    def test_a_new_task_the_agent_can_do_is_routed_to_it_and_worked(self):
        import json
        os.environ['STUB_ROUTE'] = json.dumps({'route': 'agent', 'why': 'a README change'})
        t = self.tasks.create({'title': 'Fix the README typo', 'body': '## Done-test\nNo typo.'})
        os.environ.update(STUB_MODE='pr', STUB_TASK=str(t['id']))
        self.agent.main([])
        t = self.tasks.get(t['id'])
        self.assertEqual(t['comments'][0]['author'], 'task-router')
        self.assertTrue(t['comments'][0]['text'].startswith('🧭 To the agent'))
        self.assertEqual((t['status'], 'claude' in t['tags']), ('doing', True))  # worked in the same run
        self.assertIn('Fix the README typo', open(self.seen + '.route').read())
        self.assertIn("Merge it, or tell me what to change.", self.messages()[-1])
        self.assertNotIn('https://', self.messages()[-1])  # a PR by number, never its URL
        self.assertIn('PR #7', self.messages()[-1])
        self.assertTrue(self.messages()[-1].startswith("🤖 "))
        self.assertIn(f" (#{t['id']})\n", self.messages()[-1])  # line 1 ends with the ref Hermes routes a reply by
        self.assertNotIn('http://', self.messages()[-1])

    def test_a_task_that_needs_him_stays_his_quietly(self):
        t = self.tasks.create({'title': 'Pay the hosting invoice'})
        self.agent.main([])
        t = self.tasks.get(t['id'])
        self.assertEqual((t['status'], t['tags'], t['comments'][0]['text']), ('todo', [], '🧭 Yours: needs his card'))
        self.assertFalse(os.path.exists(self.seen))
        self.assertEqual(self.messages(), [])
        self.agent.main([])  # routed once, never again
        self.assertEqual(open(self.seen + '.route').read().count('---'), 1)

    def test_an_unclear_task_asks_him_on_telegram(self):
        import json
        os.environ['STUB_ROUTE'] = json.dumps({'route': 'ask', 'why': 'which app?', 'question': 'Which app, budget or Rig?'})
        t = self.tasks.create({'title': 'Make it faster'})
        self.agent.main([])
        t = self.tasks.get(t['id'])
        self.assertEqual(t['comments'][-1]['text'], '⏳ Needs you: Which app, budget or Rig?')
        self.assertNotIn('claude', t['tags'])
        self.assertTrue(self.messages()[0].startswith(f"🧭 Make it faster (#{t['id']})\nBefore I hand this over, one question: Which app, budget or Rig?"))

    def test_the_backlog_is_never_routed(self):
        os.environ['TASK_AGENT_ROUTE_SINCE'] = str(2e10)
        self.agent.ROUTE_SINCE = 2e10
        self.tasks.create({'title': 'old ClickUp thing'})
        self.agent.main([])
        self.assertFalse(os.path.exists(self.seen + '.route'))

    def test_the_day_has_a_run_limit(self):
        self.agent.MAX_RUNS_A_DAY = 1
        self.hand('first', 'close', priority=1)
        self.agent.main([])
        t2 = self.hand('second', 'close')
        self.agent.main([])
        self.assertEqual(self.tasks.get(t2['id'])['status'], 'todo')  # waits for tomorrow

    def test_a_question_from_the_agent_reaches_telegram_with_how_to_answer(self):
        self.hand('Pick a plan', 'ask')
        self.agent.main([])
        self.assertIn('One question first: which plan, monthly or yearly?', self.messages()[-1])
        self.assertIn('Answer here and the work carries on', self.messages()[-1])

    # ---------------------------------------------------------------- his replies

    def test_a_task_just_filed_waits_for_its_filer_to_claim_it(self):
        self.agent.ROUTE_GRACE = 300
        t = self.tasks.create({'title': 'Fix the sync'})
        self.agent.main([])
        self.assertEqual(self.tasks.get(t['id'])['comments'], [])  # not routed yet
        self.tasks.update(t['id'], {'status': 'doing'})  # its session took it
        self.agent.ROUTE_GRACE = 0
        self.agent.main([])
        self.assertEqual(self.tasks.get(t['id'])['comments'], [])  # never routed: not todo

    def test_a_routed_task_is_remembered(self):
        t = self.tasks.create({'title': 'Renew the domain'})
        self.agent.main([])
        self.tasks.comment(t['id'], 'a later note by someone')
        self.agent.main([])
        self.assertEqual(open(self.seen + '.route').read().count('---'), 1)
        self.assertIn(str(t['id']), open(os.path.join(os.environ['TASK_AGENT_STATE'], 'routed')).read().split())

    def orphan(self, title, at=None):
        t = self.tasks.create({'title': title, 'tags': ['claude'], 'status': 'doing'})
        self.tasks.comment(t['id'], f'🤖 Picked up by the agent on {self.agent.MACHINE} (session 1234abcd).', 'task-agent', at=at)
        return t['id']

    def test_a_run_that_died_is_queued_once_more_then_given_back(self):
        tid = self.orphan('Fix the cron')
        self.agent.sweep_orphans()
        t = self.tasks.get(tid)
        self.assertEqual((t['status'], t['tags']), ('todo', ['claude']))
        self.assertIn('one more try', t['comments'][-1]['text'])
        self.tasks.update(tid, {'status': 'doing'})
        self.tasks.comment(tid, f'🤖 Picked up by the agent on {self.agent.MACHINE} (session 5678abcd).', 'task-agent')
        self.agent.sweep_orphans()
        t = self.tasks.get(tid)
        self.assertEqual((t['status'], t['tags']), ('todo', []))
        self.assertIn('Back with you', t['comments'][-1]['text'])

    def test_an_old_dead_run_goes_straight_back_to_him(self):
        import time
        tid = self.orphan('Fix the cron', at=time.time() - 3 * 86400)
        live = self.orphan('Being worked')
        self.tasks.comment(live, '👉 PR https://github.com/o/r/pull/9 (fix/x → main): the fix', 'Claude Code 1234abcd')
        self.agent.sweep_orphans()
        self.assertEqual(self.tasks.get(tid)['tags'], [])
        self.assertEqual(self.tasks.get(live)['status'], 'doing')  # it said something since: not an orphan

    def test_a_run_that_breaks_off_is_given_back_not_left_doing(self):
        t = self.hand('Fix login', 'pr')
        def gone(task):
            raise SystemExit('tasks: hub unreachable at http://beacon (timed out)')
        self.agent.workdir = gone
        self.assertEqual(self.agent.main([]), 1)
        t = self.tasks.get(t['id'])
        self.assertEqual((t['status'], t['tags']), ('todo', []))
        self.assertIn('broke off', t['comments'][-1]['text'])

    def test_a_pr_stands_even_if_the_run_errors_after_it(self):
        t = self.hand('Fix login', 'pr')
        real = self.agent.run_claude
        def errs(*a):
            out, _ = real(*a)
            return out, 'error_max_structured_output_retries'
        self.agent.run_claude = errs
        self.assertEqual(self.agent.main([]), 0)
        t = self.tasks.get(t['id'])
        self.assertEqual((t['status'], t['tags']), ('doing', ['claude']))
        self.assertIn('Merge it', open(self.told).read())

    def test_a_run_that_only_reports_progress_is_given_back(self):
        t = self.hand('Fix login', 'pr')
        os.environ['STUB_MODE'] = 'progress'
        self.assertEqual(self.agent.main([]), 1)
        t = self.tasks.get(t['id'])
        self.assertEqual((t['status'], t['tags']), ('todo', []))
        self.assertIn('without a PR, a question or closing the task', t['comments'][-1]['text'])

    def pr_task(self, *notes, status='doing'):
        t = self.tasks.create({'title': 'Fix login', 'tags': ['claude'], 'status': status})
        for n in notes:
            self.tasks.comment(t['id'], n, 'Claude Code 1234abcd')
        return t['id']

    def test_a_pr_merged_on_github_closes_its_task(self):
        merged = self.pr_task('👉 PR https://github.com/o/r/pull/7 (fix/x → main): the fix')
        moved_on = self.pr_task('👉 PR https://github.com/o/r/pull/8 (fix/y → main): part one', '🤖 to review: play it')
        os.environ['STUB_GH_STATE'] = 'MERGED'
        self.agent.sync_prs(force=True)
        t = self.tasks.get(merged)
        self.assertEqual((t['status'], t['tags']), ('done', []))
        self.assertEqual(t['comments'][-1]['text'], '✅ PR #7 merged on GitHub, so the task is done.')
        self.assertEqual(self.tasks.get(moved_on)['status'], 'doing')  # past its PR: left alone
        self.assertNotIn('pull/8', open(self.gh_log).read())

    def test_a_pr_closed_unmerged_puts_the_task_back_on_his_list(self):
        tid = self.pr_task('👉 PR https://github.com/o/r/pull/7 (fix/x → main): the fix')
        os.environ['STUB_GH_STATE'] = 'CLOSED'
        self.agent.sync_prs(force=True)
        t = self.tasks.get(tid)
        self.assertEqual((t['status'], t['tags']), ('todo', []))
        self.assertIn('closed without merging', t['comments'][-1]['text'])

    def test_an_open_pr_or_a_github_outage_changes_nothing(self):
        tid = self.pr_task('👉 PR https://github.com/o/r/pull/7 (fix/x → main): the fix')
        for state in ('OPEN', ''):
            os.environ['STUB_GH_STATE'] = state
            self.agent.sync_prs(force=True)
            self.assertEqual(self.tasks.get(tid)['status'], 'doing')

    def test_the_pr_check_runs_every_few_minutes_not_every_minute(self):
        self.pr_task('👉 PR https://github.com/o/r/pull/7 (fix/x → main): the fix')
        os.environ['STUB_GH_STATE'] = 'OPEN'
        self.agent.sync_prs()
        self.agent.sync_prs()
        self.assertEqual(open(self.gh_log).read().count('pr view'), 1)

    def test_the_phone_gets_the_structured_line_never_the_sessions_ending(self):
        os.environ['STUB_PHONE'] = 'Ozpin: The login holds now. PR #7 is ready.'
        self.hand('Fix login', 'pr')
        self.agent.main([])
        told = open(self.told).read()
        self.assertIn('The login holds now. PR #7 is ready.', told)
        self.assertNotIn('Ozpin:', told)
        ending = 'Fixed it.\n\n⏳ Needs you:\n- 👀 **Merge** → `merge`\n  *fix/x → main · the fix · preview: —*'
        self.assertEqual(self.agent.phone({'result': ending}), 'Fixed it.')

    def test_paused_it_neither_routes_nor_works_but_still_tidies(self):
        self.assertEqual(self.agent.main(['pause']), 0)
        new = self.tasks.create({'title': 'Renew the domain'})
        handed = self.hand('Fix login', 'pr')
        merged = self.tasks.create({'title': 'Fix cron', 'tags': ['claude'], 'status': 'doing'})
        self.tasks.comment(merged['id'], '👉 PR https://github.com/o/r/pull/7 (fix/x → main): the fix', 'Claude Code 1234abcd')
        os.environ['STUB_GH_STATE'] = 'MERGED'
        self.assertEqual(self.agent.main([]), 0)
        self.assertFalse(os.path.exists(self.seen + '.route'))  # no router call
        self.assertFalse(os.path.exists(self.seen))  # no run
        self.assertEqual(self.tasks.get(new['id'])['comments'], [])
        self.assertEqual(self.tasks.get(handed['id'])['status'], 'todo')
        self.assertEqual(self.tasks.get(merged['id'])['status'], 'done')  # the PR check still runs
        self.agent.main(['resume'])
        self.assertEqual(self.agent.main([]), 0)
        self.assertEqual(self.tasks.get(handed['id'])['status'], 'doing')

    def with_pr(self):
        t = self.hand('Fix login', 'pr')
        self.agent.main([])
        return t['id']

    def test_merge_merges_the_agents_pr_and_closes_the_task(self):
        tid = self.with_pr()
        self.assertEqual(self.agent.main(['reply', f'#{tid}', 'Merge!']), 0)
        log = open(self.gh_log).read()
        self.assertIn('pr merge https://github.com/o/r/pull/7 --squash --delete-branch', log)
        t = self.tasks.get(tid)
        self.assertEqual((t['status'], t['comments'][-1]['author']), ('done', 'owner'))

    def test_a_failed_merge_leaves_the_task_open(self):
        tid = self.with_pr()
        os.environ['STUB_GH_EXIT'] = '1'
        self.assertEqual(self.agent.main(['reply', str(tid), 'merge']), 1)
        self.assertEqual(self.tasks.get(tid)['status'], 'doing')

    def test_merge_without_a_pr_says_so(self):
        t = self.tasks.create({'title': 'x'})
        self.assertEqual(self.agent.main(['reply', str(t['id']), 'merge']), 1)
        self.assertFalse(os.path.exists(self.gh_log))

    def test_drop_drops_and_closes_the_pr(self):
        tid = self.with_pr()
        self.agent.main(['reply', str(tid), 'drop'])
        self.assertIn('pr close', open(self.gh_log).read())
        self.assertEqual(self.tasks.get(tid)['status'], 'dropped')

    def test_drop_of_a_repo_agents_edit_goes_back_to_revert_it(self):
        t = self.tasks.create({'title': 'x', 'tags': ['claude']})
        self.agent.note(t['id'], f"multi_edit Prompts: NEW ... -- AGENT #{t['id']}", 'agent')
        self.agent.main(['reply', str(t['id']), 'zahoď'])
        t = self.tasks.get(t['id'])
        self.assertEqual((t['status'], 'claude' in t['tags']), ('todo', True))
        self.assertIn('vrať změny agenta', t['comments'][-1]['text'])

    def test_mine_keeps_it_his(self):
        t = self.hand('Pick a plan', 'ask')
        self.agent.main([])
        self.agent.main(['reply', str(t['id']), 'mine'])
        t = self.tasks.get(t['id'])
        self.assertEqual((t['status'], t['tags']), ('todo', []))
        self.assertEqual(self.agent.queue(), [])

    def test_an_answer_goes_back_to_the_agent(self):
        t = self.hand('Pick a plan', 'ask')
        self.agent.main([])
        self.agent.main(['reply', str(t['id']), 'yearly,', 'and', 'no', 'trial'])
        t = self.tasks.get(t['id'])
        self.assertEqual((t['comments'][-1]['text'], t['comments'][-1]['author']), ('yearly, and no trial', 'owner'))
        self.assertEqual([x['id'] for x in self.agent.queue()], [t['id']])


    def test_a_question_asked_on_a_task_is_on_the_questions_page(self):
        t = self.hand('Pick a plan', 'ask')
        self.agent.main([])
        qs = self.server.Handler.questions.list('open')
        self.assertEqual([(q['task_id'], q['question'], q['blocking']) for q in qs],
                         [(t['id'], 'which plan, monthly or yearly?', True)])

    def test_a_telegram_reply_answers_the_tasks_question(self):
        t = self.hand('Pick a plan', 'ask')
        self.agent.main([])
        self.agent.main(['reply', str(t['id']), 'yearly'])
        q = self.server.Handler.questions.list('all')[0]
        self.assertEqual((q['status'], q['answer'], q['answered_via'], q['taken_by']), ('answered', 'yearly', 'task', 'task-agent'))
        self.assertEqual(sum(c['text'] == 'yearly' for c in self.tasks.get(t['id'])['comments']), 1)

    def test_drop_on_telegram_answers_the_question_before_closing(self):
        t = self.hand('Pick a plan', 'ask')
        self.agent.main([])
        self.agent.main(['reply', str(t['id']), 'drop'])
        q = self.server.Handler.questions.list('all')[0]
        self.assertEqual((q['status'], q['answer'][:10]), ('answered', '🗑️ Dropped'))
        self.assertEqual(self.tasks.get(t['id'])['status'], 'dropped')

    def test_long_words_are_cut_for_the_phone(self):
        long = 'because ' * 60
        self.agent.tell({'id': 9, 'title': 'T'}, '❓ go ahead? ' + long, 'Reply `mine`.')
        with open(self.told) as f:
            msg = f.read().split('===')[0].strip()
        self.assertIn('… (the rest is on the task)', msg)
        self.assertLess(len(msg), 300)
        self.assertTrue(msg.rstrip().endswith('Reply `mine`.'))

    # ---------------------------------------------------------------- a repo's own (jailed) run

    def conf_run(self, title='Bug', mode='silent', **conf):
        base = {'brief': 'brief.md', 'from': '❄️ Game'}
        base.update(conf)
        self.app_repo(base, [('brief.md', 'Fix task {id}.')])
        return self.hand(title, mode, project='app', tags=['claude', 'discord'])

    def test_a_jailed_run_has_a_fixed_unit_and_no_hub_token(self):
        home = os.path.join(self.dir.name, 'home')
        sock = os.path.join(self.dir.name, 'x.sock')
        open(sock, 'w').close()
        env = {'TASKS_TOKEN': 'secret', 'PATH': '/bin', 'HOME': '/root', 'CTS_AGENT': '1', 'GH_TOKEN': 'g'}
        run = self.agent.jailed(['claude', '-p', 'x'], env, self.dir.name,
                                {'config_dir': home, 'env_keep': ['CTS_AGENT', 'TASKS_TOKEN']}, 60, 'cts-agent-5', sock)
        self.assertIn('--unit=cts-agent-5', run)
        self.assertFalse([a for a in run if 'secret' in a or 'TASKS_TOKEN' in a or 'GH_TOKEN' in a], run)
        self.assertIn('CTS_AGENT=1', run)
        binds = ' '.join(a for a in run if a.startswith('BindReadOnlyPaths='))
        self.assertIn(f'{sock}:/run/cts-tasks.sock', binds)
        self.assertIn(self.agent.JAIL_TASKS + ':', binds)

    def test_a_jailed_run_that_times_out_has_its_unit_stopped(self):
        t = self.hand('Bug', 'hang')
        conf = {'jail': {'config_dir': os.path.join(self.dir.name, 'home')}}
        seen = {}
        def jailed(cmd, env, where, jail, timeout, unit, sock=None):
            seen.update(unit=unit, sock=sock, live=os.path.exists(sock))
            return cmd  # the stub claude itself, unjailed
        self.agent.jailed = jailed
        self.agent.TIMEOUT = 1
        out, err = self.agent.run_claude(t, 'abcdef12-0000', self.dir.name, 'go', conf, None)
        self.assertEqual((out, err), (None, 'stopped after 0 min'))
        unit = f"cts-agent-{t['id']}"
        self.assertEqual(seen['unit'], unit)
        self.assertTrue(seen['live'])  # the proxy listened while it ran...
        self.assertFalse(os.path.exists(seen['sock']))  # ...and is gone after
        log = open(self.systemctl_log).read().splitlines()
        self.assertEqual(log.count(f'stop {unit}.service'), 2)  # a leftover before, the timed-out one after

    def test_time_the_usage_brake_froze_a_run_does_not_count(self):
        brake = os.path.join(self.dir.name, 'brake.json')
        self.agent.BRAKE_STATE = brake
        with open(brake, 'w') as f:
            json.dump({'units': {'cts-agent-9.service': 'soft'}}, f)
        r = self.agent.run_timed(['sleep', '2'], 1, 'cts-agent-9')  # held the whole time: runs to its end
        self.assertEqual(r.returncode, 0)
        with self.assertRaises(self.agent.subprocess.TimeoutExpired):  # another unit is held, not this one
            self.agent.run_timed(['sleep', '5'], 1, 'cts-agent-10')

    def test_leftover_jails_are_stopped_before_a_run(self):
        with open(self.agent.SYSTEMCTL, 'w') as f:
            f.write('#!/bin/sh\necho "$@" >> "$STUB_SYSTEMCTL_LOG"\n'
                    '[ "$1" = list-units ] && echo "cts-agent-17.service loaded active running x" && echo "cts-studio@1.service x"'
                    ' && echo "cts-check-18.service loaded active running x"\n'
                    'exit 0\n')
        self.agent.main([])
        log = open(self.systemctl_log).read()
        self.assertIn('stop cts-agent-17.service', log)
        self.assertIn('stop cts-check-18.service', log)  # a checker's run too
        self.assertNotIn('cts-studio', log.replace('list-units', ''))

    def jail_tasks(self, *args, stdin='', sock=None):
        import subprocess
        return subprocess.run([sys.executable, os.path.join(ROOT, 'client', 'tasks-jail'), *args], input=stdin,
                              capture_output=True, text=True, env=dict(os.environ, TASKS_JAIL_SOCK=sock or self.sock))

    def test_the_jails_tasks_command_reaches_only_its_own_task(self):
        mine = self.tasks.create({'title': 'Bug', 'body': 'the tree'})
        other = self.tasks.create({'title': 'Other'})
        self.tasks.comment(mine['id'], 'from Discord', 'discord')
        proxy = self.agent.TaskProxy(mine['id'], 'abcdef12-3456', os.path.join(self.dir.name, 'socks', 'p.sock')).start()
        self.sock = proxy.path
        try:
            self.assertEqual(oct(os.stat(proxy.path).st_mode & 0o777), '0o600')
            r = self.jail_tasks('show', f"#{mine['id']}")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('the tree', r.stdout)
            self.assertIn('· discord: from Discord', r.stdout)
            self.assertEqual(self.jail_tasks('note', str(mine['id']), 'looked at it').returncode, 0)
            self.assertEqual(self.jail_tasks('note', str(mine['id']), '-', stdin='**Co se děje:** x\n').returncode, 0)
            for bad in (('note', str(other['id']), 'hi'), ('done', str(other['id'])), ('ls',), ('drop', str(mine['id'])),
                        ('set', str(mine['id']), 'tags=claude'), ('add', 'new task')):
                r = self.jail_tasks(*bad)
                self.assertNotEqual(r.returncode, 0, bad)
            import json, socket
            for req in ({'cmd': 'note', 'id': other['id'], 'text': 'raw'}, {'cmd': 'drop', 'id': mine['id']},
                        {'cmd': 'note', 'id': str(mine['id']), 'text': 'x'}):  # past the wrapper, straight to the socket
                s = socket.socket(socket.AF_UNIX)
                s.connect(proxy.path)
                s.sendall((json.dumps(req) + '\n').encode())
                self.assertIn('error', json.loads(s.makefile().readline()))
                s.close()
            self.assertEqual(self.jail_tasks('done', str(mine['id']), '-m', '✅ fixed').returncode, 0)
        finally:
            proxy.stop()
        t = self.tasks.get(mine['id'])
        self.assertEqual(t['status'], 'done')
        self.assertEqual([(c['author'], c['text']) for c in t['comments'][1:]],
                         [('Claude Code abcdef12', 'looked at it'), ('Claude Code abcdef12', '**Co se děje:** x'),
                          ('Claude Code abcdef12', '✅ fixed')])
        self.assertEqual(self.tasks.get(other['id'])['comments'], [])
        self.assertNotEqual(self.jail_tasks('show', str(mine['id'])).returncode, 0)  # the run is over: no proxy

    def test_a_repo_runs_question_keeps_the_whole_ask_and_a_czech_hint(self):
        ask = 'Otestuj v Play: ' + 'stiskni F8 Castle Key a dojdi ke dveřím, ' * 12
        self.conf_run()
        os.environ.update(STUB_NOTE='⏳ Needs you: ' + ask.strip(), STUB_PHONE='Strom na náměstí blikal, ' * 20)
        self.agent.main([])
        msg = self.messages()[-1]
        self.assertIn('👉 ' + ask.strip(), msg)  # never cut
        self.assertIn('(zbytek je na úkolu)', msg.split('\n👉')[0])  # only the phone line is
        self.assertTrue(msg.endswith('Napiš ok / co je špatně / zahoď.'), msg)
        self.assertNotIn('mine', msg)
        self.assertIn('Změny v testovací kopii: žádné', msg)

    def test_a_handoff_to_steps_says_to_forward_it(self):
        self.conf_run()
        os.environ['STUB_NOTE'] = '⏳ Needs you: Pošli Kovovi: v NorthPoleFx chybí kontrola'
        self.agent.main([])
        self.assertTrue(self.messages()[-1].endswith('Přepošli Kovovi, napiš ok až to opraví.'))

    def test_photos_are_the_newest_before_and_after_shots_only(self):
        import time
        proof = os.path.join(self.dir.name, 'proof', 'task-9-123')
        os.makedirs(proof)
        for i, f in enumerate(['1795000000-before-old.png', '1795000100-before-pole-tree.png', 'after-pole-tree.png',
                               'junk-x.png', '1795000200-junk-y.png', 'notes.txt', '1795000300-other.png']):
            open(os.path.join(proof, f), 'w').close()
            os.utime(os.path.join(proof, f), (time.time() - 100 + i, time.time() - 100 + i))
        self.assertEqual([(os.path.basename(p), cap) for p, cap in self.agent.photos(proof)],
                         [('1795000100-before-pole-tree.png', '#9 před: pole-tree'), ('after-pole-tree.png', '#9 po: pole-tree')])

    def test_a_run_that_could_not_start_is_queued_again_quietly(self):
        t = self.conf_run()
        os.environ['STUB_RETRY'] = '1'
        gate = os.path.join(self.dir.name, 'gate')
        os.makedirs(gate)
        self.agent.agent_conf = (lambda real: lambda where: dict(real(where), writes_from=gate))(self.agent.agent_conf)
        with open(os.path.join(gate, 'studio-edits.log'), 'w') as f:  # a Play-only call this run: no lasting change
            import datetime
            later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)  # inside every try
            f.write(later.isoformat().replace('+00:00', 'Z') + '\tstudio-gate\texecute_luau(Server)\n')
        stamp = os.path.join(os.environ['TASK_AGENT_STATE'], 'preflight-failed-app')
        for _ in range(self.agent.RETRIES_A_DAY):
            self.assertEqual(self.agent.main([]), 0)
            got = self.tasks.get(t['id'])
            self.assertEqual((got['status'], sorted(got['tags'])), ('todo', ['claude', 'discord']))
            self.assertEqual(self.agent.runs_today(), [])  # not one of the day's runs
            self.assertTrue(os.path.exists(stamp))  # the next try waits the preflight's backoff
            os.remove(stamp)
        self.assertEqual(self.messages(), [])  # no Telegram
        self.assertEqual(self.agent.main([]), 1)  # the fifth time today: back to him
        got = self.tasks.get(t['id'])
        self.assertEqual((got['status'], got['tags']), ('todo', ['discord']))
        self.assertIn('nemohl začít', got['comments'][-1]['text'])
        self.assertEqual(len(self.messages()), 1)

    def test_a_crash_lists_what_it_changed_in_studio_in_czech(self):
        gate = os.path.join(self.dir.name, 'gate')
        os.makedirs(gate)
        with open(os.path.join(gate, 'studio-edits.log'), 'w') as f:  # an earlier run's write: not this one's
            f.write('2026-01-01T10:00:00.000Z\tstudio-gate\tgame.ServerScriptService.Old\n')
        t = self.conf_run(mode='crash', writes_from=gate)
        os.environ['STUB_WRITES'] = os.path.join(gate, 'studio-edits.log')
        self.assertEqual(self.agent.main([]), 1)
        got = self.tasks.get(t['id'])
        self.assertTrue(got['comments'][-1]['text'].startswith(
            '⚠️ Agent skončil předčasně (exit 3). Změny v testovací kopii: Digger: 1 úprava (+1 v Play, nic trvalého).'),
            got['comments'][-1]['text'])
        self.assertIn('Změny v testovací kopii: Digger: 1 úprava (+1 v Play, nic trvalého).', self.messages()[-1])
        self.assertNotIn('Old', self.messages()[-1])

    def test_a_used_up_day_says_so_once(self):
        self.agent.ONLY = [('app', 'discord')]
        self.agent.MAX_RUNS_A_DAY = 1
        self.agent.log_run()
        t = self.tasks.create({'title': 'Bug', 'project': 'app', 'tags': ['claude', 'discord']})
        self.agent.main([])
        self.agent.main([])
        self.assertEqual(len(self.messages()), 1)
        self.assertIn(f"Dnešní limit agenta vyčerpán (1 běh), #{t['id']} počká do zítřka.", self.messages()[0])
        self.assertEqual(self.tasks.get(t['id'])['status'], 'todo')

    def test_at_night_messages_wait_for_the_morning_in_order(self):
        self.agent.ONLY = [('app', 'discord')]
        self.agent.NOW = lambda: self.prague(23, 10)
        self.agent.tell({'id': 5, 'title': 'Bug'}, 'first', sender='❄️ Game')
        self.agent.tell({'id': 6, 'title': 'Bug'}, 'second')
        self.agent.NOW = lambda: self.prague(7, 59, days=1)
        self.agent.main([])
        self.assertEqual(self.messages(), [])  # held all night
        self.agent.NOW = lambda: self.prague(8, 1, days=1)
        self.agent.main([])
        self.assertEqual([m.split('\n')[1] for m in self.messages()], ['first', 'second'])
        self.agent.main([])
        self.assertEqual(len(self.messages()), 2)  # sent once

    def test_outside_scoped_mode_nothing_waits_for_the_morning(self):
        self.agent.NOW = lambda: self.prague(23, 10)
        self.agent.tell({'id': 5, 'title': 'Fix login'}, 'PR #7 is ready.')
        self.assertEqual(len(self.messages()), 1)

    # ---------------------------------------------------------------- the independent checker (task 1744)

    def checker_run(self, ask='⏳ Needs you: Otestuj: Play → F8 → Castle → Key → dveře se otevřou', write=True):
        gate = os.path.join(self.dir.name, 'gate')
        os.makedirs(gate, exist_ok=True)
        t = self.conf_run(writes_from=gate, checker={'brief': 'brief.md', 'mcp_config': 'brief.md',
                                                     'writes_log': os.path.join(gate, 'gate-writes.jsonl'),
                                                     'jail': {'config_dir': os.path.join(self.dir.name, 'chome')}})
        os.environ['STUB_NOTE'] = ask
        os.environ['STUB_PHONE'] = 'Klíč mizel, opravil jsem to.'
        if write:
            os.environ['STUB_WRITES'] = os.path.join(gate, 'studio-edits.log')
        self.verdicts, self.fixes = [], 0
        real = self.agent.run_claude
        def fixer(*a, **k):
            self.fixes += 1
            return real(*a, **k)
        self.agent.run_claude = fixer
        return t

    def fake_verify(self, *verdicts):
        def verify(task, conf, claimed, proof, round_no):
            v = dict({'saw': 'Klíč po resetu zůstal v ruce.', 'why': 'bez opravy zmizel, s ní ne', 'photos': []},
                     verdict=verdicts[round_no - 1], round=round_no)
            self.verdicts.append(v)
            return v
        self.agent.verify = verify

    def test_a_claimed_fix_is_checked_and_the_pass_reaches_his_phone(self):
        t = self.checker_run()
        self.fake_verify('pass')
        self.agent.main([])
        self.assertEqual((self.fixes, len(self.verdicts)), (1, 1))
        msg = self.messages()[-1]
        self.assertIn('🔎 Nezávislá kontrola: ✅ ověřeno – Klíč po resetu zůstal v ruce.', msg)
        self.assertNotIn('Neověřeno', msg)
        self.assertIn('👉 Otestuj', msg)  # his test stays: the checker doesn't replace his OK
        got = self.tasks.get(t['id'])
        self.assertEqual(got['status'], 'todo')
        self.assertTrue(any(c['text'].startswith('🔎 Nezávislá kontrola: ✅') for c in got['comments']))

    def test_a_reject_goes_back_to_the_fixer_once_then_says_neovereno(self):
        t = self.checker_run()
        self.fake_verify('reject', 'reject')
        self.agent.main([])
        self.assertEqual((self.fixes, len(self.verdicts)), (2, 2))
        texts = [c['text'] for c in self.tasks.get(t['id'])['comments']]
        self.assertTrue(any(x.startswith('🔎 Kontrola zamítla opravu (kolo 1') for x in texts), texts)
        self.assertTrue(any(x.startswith('🔁 Kontrola opravu zamítla') for x in texts), texts)
        msgs = self.messages()
        self.assertEqual(len(msgs), 1)  # one message per bug, at the end
        self.assertIn('⚠️ Neověřeno: Klíč mizel', msgs[-1])
        self.assertIn('❌ neověřeno', msgs[-1])

    def test_a_reject_then_a_pass_is_verified(self):
        self.checker_run()
        self.fake_verify('reject', 'pass')
        self.agent.main([])
        self.assertEqual((self.fixes, len(self.verdicts)), (2, 2))
        self.assertIn('✅ ověřeno', self.messages()[-1])
        self.assertNotIn('Neověřeno', self.messages()[-1])

    def test_a_reject_whose_retry_cannot_start_says_so(self):
        self.checker_run()
        self.fake_verify('reject')
        self.agent.refix = lambda task, conf, claimed: None
        self.agent.main([])
        msg = self.messages()[-1]
        self.assertIn('❌ opravu zamítla', msg)
        self.assertIn('agent ji nestihl opravit znovu', msg)
        self.assertNotIn('zamítla i opravenou verzi', msg)

    def test_a_retry_that_ends_without_a_claim_is_not_called_a_second_reject(self):
        self.checker_run()
        self.fake_verify('reject')
        real = self.agent.refix
        def refix(task, conf, claimed):
            os.environ.update(STUB_NOTE='⏳ Needs you: Nepodařilo se zopakovat: jaký nástroj?', STUB_PHONE='Nešlo to zopakovat.')
            return real(task, conf, claimed)
        self.agent.refix = refix
        self.agent.main([])
        self.assertEqual(len(self.verdicts), 1)
        msg = self.messages()[-1]
        self.assertIn('pak: Nešlo to zopakovat.', msg)
        self.assertIn('👉 Nepodařilo se zopakovat', msg)

    def test_the_question_waits_for_the_verdict(self):
        t = self.checker_run()
        seen = {}
        def verify(task, conf, claimed, proof, round_no):
            seen['open'] = [q['question'][:8] for q in self.server.Handler.questions.list('open', task=t['id'])]
            return {'verdict': 'pass', 'saw': 'ok', 'why': '', 'photos': [], 'round': round_no}
        self.agent.verify = verify
        self.agent.main([])
        self.assertEqual(seen['open'], [])  # withdrawn while the check ran
        self.assertEqual([q['question'][:8] for q in self.server.Handler.questions.list('open', task=t['id'])], ['Otestuj:'])
        self.assertTrue(self.tasks.get(t['id'])['comments'][-1]['text'].startswith('⏳ Needs you: Otestuj'))

    def test_no_retry_when_he_answered_during_the_check(self):
        t = self.checker_run()
        def verify(task, conf, claimed, proof, round_no):
            self.tasks.comment(t['id'], 'ok funguje', 'owner')
            return {'verdict': 'reject', 'saw': 'pořád to padá', 'why': 'x', 'photos': [], 'round': round_no}
        self.agent.verify = verify
        self.agent.main([])
        self.assertEqual(self.fixes, 1)

    def test_cant_verify_uses_no_retry(self):
        self.checker_run()
        self.fake_verify('cant_verify')
        self.agent.main([])
        self.assertEqual((self.fixes, len(self.verdicts)), (1, 1))
        self.assertIn('⚠️ neověřeno – bez opravy zmizel', self.messages()[-1])

    def test_no_check_without_a_fix_claim_or_a_lasting_change(self):
        self.checker_run(ask='⏳ Needs you: Pošli Kovovi: v NorthPoleFx chybí kontrola')
        self.fake_verify('pass')
        self.agent.main([])
        self.assertEqual(self.verdicts, [])

    def test_no_check_when_the_fix_changed_nothing_in_studio(self):
        self.checker_run(write=False)
        self.fake_verify('pass')
        self.agent.main([])
        self.assertEqual(self.verdicts, [])
        self.assertNotIn('Nezávislá kontrola', self.messages()[-1])

    def gate_rows(self, path, since):
        import json
        rows = [{'t': since - 10, 'tool': 'multi_edit', 'mode': 'Edit', 'file': 'Old.Script',
                 'edits': [{'old_string': 'earlier', 'new_string': 'run'}]},
                {'t': since + 1, 'tool': 'multi_edit', 'mode': 'Edit', 'datamodel': 'Edit', 'file': 'Game.Prompts',
                 'edits': [{'old_string': 'positions[1]', 'new_string': 'tap'}]},
                {'t': since + 2, 'tool': 'execute_luau', 'mode': 'Play', 'datamodel': 'Server',
                 'code': 'workspace:SetAttribute("ClaudeAgentPlay", true)'},
                {'t': since + 3, 'tool': 'execute_luau', 'mode': 'Edit', 'datamodel': 'Edit', 'code': 'x.Value = 2'}]
        with open(path, 'w') as f:
            f.write('\n'.join(json.dumps(r) for r in rows) + '\nnot json\n')

    def test_the_checker_gets_the_report_and_the_real_diff_but_no_fixer_words(self):
        import time
        t = self.tasks.create({'title': 'Klíč mizí', 'body': 'KommTo: klíč zmizel po resetu', 'project': 'app'})
        self.tasks.comment(t['id'], 'Discord: stalo se to i Kovovi', 'discord-watch', at=time.time() - 50)
        self.tasks.comment(t['id'], 'I think the cause is the reset handler', 'Claude Code 1234abcd', at=time.time() - 40)
        claimed = time.time() - 30
        self.tasks.comment(t['id'], 'owner after the claim', 'owner', at=time.time())
        log = os.path.join(self.dir.name, 'gate-writes.jsonl')
        self.gate_rows(log, claimed)
        proof = os.path.join(self.dir.name, 'proof', f"task-{t['id']}-1")
        os.makedirs(proof)
        open(os.path.join(proof, '1795000000-before-key.png'), 'w').close()
        where = os.path.join(self.dir.name, 'wt')
        os.makedirs(where)
        self.assertTrue(self.agent.check_package(self.tasks.get(t['id']), {'checker': {'writes_log': log}}, claimed, proof, where))
        inp = os.path.join(where, 'check-input')
        report, diff = open(os.path.join(inp, 'report.md')).read(), open(os.path.join(inp, 'diff.md')).read()
        self.assertIn('klíč zmizel po resetu', report)
        self.assertIn('stalo se to i Kovovi', report)
        self.assertNotIn('reset handler', report)  # the fixer's words never reach it
        self.assertNotIn('after the claim', report)
        self.assertIn('Game.Prompts', diff)
        self.assertIn('x.Value = 2', diff)  # a lasting Edit-mode change
        self.assertNotIn('ClaudeAgentPlay', diff)  # Play-only: gone with the Play
        self.assertNotIn('Old.Script', diff)  # an earlier run's
        self.assertEqual(sorted(os.listdir(inp)), ['diff.md', 'fixer-claim-before.png', 'report.md'])  # neutral names
        self.assertFalse(self.agent.check_package(self.tasks.get(t['id']), {'checker': {'writes_log': log}},
                                                  claimed + 100, proof, where))  # nothing changed: nothing to check

    def real_verify(self, said, shot=None):
        """verify() for real, its jailed Claude swapped for a script that prints `said` (and saves `shot`)."""
        import json, time
        live = os.path.join(self.dir.name, 'check-live')
        os.makedirs(live, exist_ok=True)
        log = os.path.join(self.dir.name, 'gate-writes.jsonl')
        self.app_repo({}, [('checker.md', 'Check task {id} from {input}.'), ('m.json', '{}')])
        t = self.tasks.create({'title': 'Klíč mizí', 'body': 'klíč zmizel', 'project': 'app'})
        claimed = time.time() - 30
        self.gate_rows(log, claimed)
        conf = {'env': {'CTS_AGENT': '1'}, 'checker': {'brief': 'checker.md', 'mcp_config': 'm.json', 'writes_log': log,
                'proof_from': live, 'jail': {'config_dir': os.path.join(self.dir.name, 'chome')}}}
        script = os.path.join(self.dir.name, 'checker-stub')
        with open(script, 'w') as f:
            f.write('#!/bin/sh\n' + ''.join(f'touch {live}/$(date +%s)-{x}\n' for x in (shot or '').split() if x)
                    + f"echo '{json.dumps({'structured_output': said})}'\n")
        os.chmod(script, 0o755)
        seen = {}
        def jailed(cmd, env, where, jail, timeout, unit, sock=None):
            seen.update(cmd=cmd, env=dict(env), unit=unit, sock=sock, input=os.listdir(os.path.join(where, 'check-input')))
            return [script]
        self.agent.jailed = jailed
        self.agent.login_lasts = lambda s: True
        proof = os.path.join(self.dir.name, 'proof', f"task-{t['id']}-1")
        os.makedirs(proof)
        return self.agent.verify(self.tasks.get(t['id']), conf, claimed, proof, 1), seen

    def test_a_checker_pass_with_its_own_after_shot_stands(self):
        v, seen = self.real_verify(dict(PROVEN, verdict='pass', saw='Klíč zůstal.', why='ok'),
                                   shot='before-gone.png after-key.png')
        self.assertEqual(v['verdict'], 'pass')
        tid = v['photos'][0][1].split()[0]
        self.assertEqual([cap for _, cap in v['photos']], [f'{tid} kontrola před: gone', f'{tid} kontrola po: key'])
        cmd = seen['cmd']
        self.assertIn('--json-schema', cmd)
        self.assertNotIn('Bash', cmd[cmd.index('--tools') + 1])
        self.assertTrue(cmd[cmd.index('-p') + 1].startswith('Check task'))
        self.assertIsNone(seen['sock'])  # no hub proxy: it can't read the fixer's notes
        self.assertFalse({'TASKS_URL', 'TASKS_TOKEN', 'GH_TOKEN'} & set(seen['env']))
        self.assertEqual(seen['unit'][:10], 'cts-check-')
        self.assertEqual(sorted(seen['input']), ['diff.md', 'report.md'])

    def test_a_checker_pass_without_its_own_after_shot_is_unverified(self):
        v, _ = self.real_verify(dict(PROVEN, verdict='pass', saw='Funguje.', why='ok'))
        self.assertEqual(v['verdict'], 'cant_verify')
        self.assertIn('bez vlastního snímku před opravou', v['why'])

    def test_a_checker_pass_with_only_an_after_shot_is_unverified(self):
        v, _ = self.real_verify({'verdict': 'pass', 'saw': 'Funguje.', 'why': 'ok'}, shot='after-key.png')
        self.assertEqual(v['verdict'], 'cant_verify')  # fail before, pass after: its own before is needed too

    def test_a_checker_that_answers_nothing_is_unverified(self):
        v, _ = self.real_verify(None)
        self.assertEqual(v['verdict'], 'cant_verify')

    # ---------------------------------------------------------------- guardrails (task 1744, 2026-11-21), each forced

    def test_g1_two_attempts_then_straight_to_him_with_what_was_tried(self):
        t = self.conf_run()
        A = self.agent
        self.tasks.comment(t['id'], f'{A.PICKUP} on x (session aaaaaaaa).', A.ME)
        self.tasks.comment(t['id'], '🔎 Nezávislá kontrola: ❌ opravu zamítla (klíč pořád mizí)', A.ME)
        self.tasks.comment(t['id'], '🔁 Kontrola opravu zamítla: agent ji opravuje znovu (session bbbbbbbb).', A.ME)
        self.assertEqual(A.attempts(self.tasks.get(t['id'])['comments']), 2)
        self.agent.main([])
        self.assertFalse(os.path.exists(self.seen))  # no third run
        got = self.tasks.get(t['id'])
        self.assertEqual((got['status'], 'claude' in got['tags']), ('todo', False))
        self.assertTrue(got['comments'][-1]['text'].startswith('⛔ 2 pokusy'))
        self.assertIn('klíč pořád mizí', self.messages()[-1])
        # His reply allows another go: the count starts again after his word.
        self.agent.reply(t['id'], 'zkus to přes reset handler')
        self.assertEqual(A.attempts(self.tasks.get(t['id'])['comments']), 0)
        self.assertIn('claude', self.tasks.get(t['id'])['tags'])

    def test_g1_a_run_that_could_not_start_is_no_attempt(self):
        A = self.agent
        notes = [{'author': A.ME, 'text': f'{A.PICKUP} on x (session a).'},
                 {'author': A.ME, 'text': '↩️ Agent nemohl začít (Studio); zkusí to znovu za chvíli.'},
                 {'author': A.ME, 'text': f'{A.PICKUP} on x (session b).'}]
        self.assertEqual(A.attempts(notes), 1)

    def test_g1_a_reject_on_the_last_attempt_gets_no_retry(self):
        t = self.checker_run()
        self.tasks.comment(t['id'], f'{self.agent.PICKUP} on x (session aaaaaaaa).', self.agent.ME)  # yesterday's run
        self.fake_verify('reject')
        self.agent.main([])
        self.assertEqual((self.fixes, len(self.verdicts)), (1, 1))
        self.assertIn('2 pokusy vyčerpány', self.messages()[-1])

    def test_g2_a_spent_day_starts_no_run_and_says_so_once(self):
        self.agent.ONLY = [('app', 'discord')]
        t = self.conf_run()
        self.agent.spend(999, 'fixer', 'x', 30.0, 10)
        self.agent.main([])
        self.agent.main([])
        self.assertFalse(os.path.exists(self.seen))
        self.assertEqual(len(self.messages()), 1)
        self.assertIn('rozpočet agenta je vyčerpaný ($30.00 z $25', self.messages()[0])
        self.assertEqual(self.tasks.get(t['id'])['status'], 'todo')

    def usage(self, **kw):
        import datetime, json
        u = {'fetched_at': datetime.datetime.fromtimestamp(self.agent.NOW(), datetime.timezone.utc).isoformat(),
             'weekly': {'used_percent': kw.get('week', 10)},
             'extra_usage': {'enabled': True, 'used': kw.get('extra', 0.0), 'currency': 'EUR'}}
        path = os.path.join(self.dir.name, 'usage.json')
        with open(path, 'w') as f:
            json.dump(u, f)
        self.agent.USAGE = path

    def test_g2_his_plan_week_nearly_used_up_stops_runs(self):
        self.usage(week=93)
        self.assertIn('93 %', self.agent.money_stop())
        self.usage(week=50)
        self.assertEqual(self.agent.money_stop(), '')

    def test_g2_real_money_spent_today_stops_runs(self):
        self.usage(extra=4.0)
        self.assertEqual(self.agent.money_stop(), '')  # the day's baseline
        self.usage(extra=6.5)
        self.assertIn('skutečné peníze', self.agent.money_stop())
        self.assertIn('2.50 EUR', self.agent.money_stop())

    def test_g2_every_run_has_a_budget_and_lands_in_the_ledger(self):
        import json
        t = self.conf_run(mode='ask')
        self.agent.main([])
        argv = json.load(open(self.seen))['argv']
        self.assertEqual(argv[argv.index('--max-budget-usd') + 1], '10')
        rows = self.agent.ledger()
        self.assertEqual([(r['task'], r['kind']) for r in rows], [(t['id'], 'fixer')])
        self.assertEqual(self.agent.scorecard()[str(t['id'])]['runs'], 1)

    def test_g2_three_failed_runs_in_a_row_pause_it_until_start(self):
        self.agent.ONLY = [('app', 'discord')]
        self.conf_run(mode='crash')
        for i in range(2):
            self.tasks.create({'title': f'Bug {i}', 'project': 'app', 'tags': ['claude', 'discord']})
        for _ in range(3):
            self.agent.main([])
        self.assertIn('circuit breaker', self.agent.stopped())
        self.assertTrue(any('⏸️ Agent se sám zastavil: 3 neúspěšné' in m for m in self.messages()))
        t4 = self.tasks.create({'title': 'Bug 4', 'project': 'app', 'tags': ['claude', 'discord']})
        os.remove(self.seen)
        self.agent.main([])
        self.assertFalse(os.path.exists(self.seen))  # stopped: nothing runs
        self.assertEqual(self.agent.reply(t4['id'], 'start'), 0)
        self.assertEqual(self.agent.stopped(), '')
        os.environ['STUB_TASK'] = str(t4['id'])
        self.agent.main([])
        self.assertTrue(os.path.exists(self.seen))

    def test_g2_a_success_resets_the_streak(self):
        t = {'id': 1, 'project': 'app', 'title': 'x'}
        self.agent.outcome(t, False, 'a')
        self.agent.outcome(t, False, 'b')
        self.agent.outcome(t, True)
        self.agent.outcome(t, False, 'c')
        self.assertEqual(self.agent.stopped(), '')

    def test_g2_stop_on_telegram_stops_runs_and_live_jails(self):
        t = self.conf_run()
        self.assertEqual(self.agent.reply(t['id'], 'Stop'), 0)
        self.assertIn('Stop', self.agent.stopped())
        self.assertEqual([c['text'] for c in self.tasks.get(t['id'])['comments']], [])  # not an answer
        self.assertIn('list-units', open(self.systemctl_log).read())
        self.agent.main([])
        self.assertFalse(os.path.exists(self.seen))
        self.assertEqual(self.agent.main(['start']), 0)
        self.agent.main([])
        self.assertTrue(os.path.exists(self.seen))

    def test_g3_a_pass_without_the_bug_seen_first_is_unverified(self):
        v, _ = self.real_verify(dict(PROVEN, verdict='pass', saw='ok', why='ok', reproduced_without_fix=False),
                                shot='before-a.png after-b.png')
        self.assertEqual(v['verdict'], 'cant_verify')
        self.assertIn('bez opravy sama nezopakovala', v['why'])

    def test_g3_a_pass_with_an_unmet_criterion_is_unverified(self):
        v, _ = self.real_verify(dict(PROVEN, verdict='pass', saw='ok', why='ok',
                                     criteria=[{'line': 'Klíč zůstane.', 'met': True}, {'line': 'Žádná chyba v konzoli.', 'met': False}]),
                                shot='before-a.png after-b.png')
        self.assertEqual(v['verdict'], 'cant_verify')
        self.assertIn('Žádná chyba v konzoli.', v['why'])

    def suite_stub(self, *results):
        """A regression suite that answers `results` in turn (each a {check: bad} dict, or None = could not run)."""
        import json
        path = os.path.join(self.dir.name, 'suite')
        os.makedirs(path, exist_ok=True)
        for i, r in enumerate(results):
            with open(os.path.join(path, f'{i}.json'), 'w') as f:
                f.write('' if r is None else json.dumps({'checks': {k: {'bad': v} for k, v in r.items()}}))
        script = os.path.join(path, 'run')
        with open(script, 'w') as f:
            f.write(f'#!/bin/sh\nd={path}\nn=$(ls $d/*.json | wc -l)\ni=$(cat $d/i 2>/dev/null || echo 0)\n'
                    f'echo "$@" >> $d/args\necho $((i+1)) > $d/i\n[ -s $d/$i.json ] || exit 1\ncat $d/$i.json\n')
        os.chmod(script, 0o755)
        return [script]

    def test_g4_a_check_that_gets_worse_rejects_the_fix_without_a_checker_run(self):
        t = self.checker_run()
        self.fake_verify('pass', 'pass')
        conf = self.agent.agent_conf
        stub = self.suite_stub({'snow_reach': 0, 'escape_scan': 100}, {'snow_reach': 3, 'escape_scan': 100},
                               {'snow_reach': 3}, {'snow_reach': 0, 'escape_scan': 100})
        self.agent.agent_conf = lambda where: dict(conf(where), suite=stub) if conf(where) else None
        self.agent.main([])
        texts = [c['text'] for c in self.tasks.get(t['id'])['comments']]
        self.assertTrue(any('snow_reach: 0 → 3' in x for x in texts if x.startswith('🔎 Kontrola zamítla')), texts)
        self.assertEqual((self.fixes, len(self.verdicts)), (2, 1))  # rejected by the suite, then fixed and checked
        self.assertIn('✅ ověřeno', self.messages()[-1])
        self.assertIn('Sada kontrol (před opravou → po ní): snow_reach 0 → 0, escape_scan 100 → 100',
                      '\n'.join(c['text'] for c in self.tasks.get(t['id'])['comments']))
        self.assertIn('--only snow_reach', open(os.path.join(self.dir.name, 'suite', 'args')).read())

    def test_g4_a_flaky_check_must_fail_twice(self):
        stub = self.suite_stub({'ui_overlap_check': 4}, {'ui_overlap_check': 9}, {'ui_overlap_check': 4})
        conf = {'suite': stub}
        base = self.agent.suite(conf, None)
        worse, after = self.agent.suite_worse(conf, None, base)
        self.assertEqual(worse, [])

    def test_g4_no_baseline_means_the_run_waits(self):
        t = self.checker_run()
        conf = self.agent.agent_conf
        stub = self.suite_stub(None)
        self.agent.agent_conf = lambda where: dict(conf(where), suite=stub) if conf(where) else None
        self.agent.main([])
        self.assertEqual(self.fixes, 0)
        got = self.tasks.get(t['id'])
        self.assertEqual((got['status'], 'claude' in got['tags']), ('todo', True))

    def test_g6_someone_in_team_create_before_the_check_means_neovereno(self):
        self.checker_run()
        self.fake_verify('pass')
        conf = self.agent.agent_conf
        self.agent.agent_conf = lambda where: dict(conf(where), lock=['false']) if conf(where) else None
        os.environ['TASK_AGENT_LOCK_MAX'] = '0'
        try:
            self.agent.main([])
        finally:
            os.environ.pop('TASK_AGENT_LOCK_MAX')
        self.assertEqual(self.verdicts, [])
        self.assertIn('editoval někdo jiný (Team Create)', self.messages()[-1])

    def test_g7_scorecard_and_his_ok_and_the_weekly_summary(self):
        t = self.checker_run()
        self.fake_verify('pass')
        self.agent.main([])
        row = self.agent.scorecard()[str(t['id'])]
        self.assertEqual((row['runs'], row['verdict']), (1, 'ověřeno'))
        self.agent.reply(t['id'], 'ok')
        self.assertEqual(self.agent.scorecard()[str(t['id'])]['outcome'], 'přijato')
        import datetime, zoneinfo
        now = datetime.datetime.now(zoneinfo.ZoneInfo('Europe/Prague'))
        monday = now - datetime.timedelta(days=now.weekday())
        self.agent.NOW = lambda: monday.replace(hour=9, minute=30).timestamp() + 7 * 86400 * (monday < now - datetime.timedelta(days=6))
        self.agent.ONLY = [('app', 'discord')]
        n = len(self.messages())
        self.agent.weekly()
        self.agent.weekly()
        self.assertEqual(len(self.messages()), n + 1)
        self.assertIn('✅ přijato 1', self.messages()[-1])

    def test_g7_seed_marks_planted_bugs_as_tests(self):
        A = self.agent
        A.ONLY = [('app', 'discord')]
        real = self.tasks.create({'title': 'Real bug', 'project': 'app', 'tags': ['discord'], 'status': 'done'})
        plant = self.tasks.create({'title': 'Planted', 'project': 'app', 'tags': ['discord'], 'body': 'AgentPlant1744'})
        for x in (real, plant):
            self.tasks.comment(x['id'], f'{A.PICKUP} on devbox (session abcdef12).', A.ME)
            self.tasks.comment(x['id'], '🔎 Nezávislá kontrola: ✅ ověřeno – ok', A.ME)
        self.tasks.comment(real['id'], 'ok', 'owner')
        card = A.seed_scorecard()
        self.assertEqual((card[str(real['id'])]['outcome'], card[str(real['id'])]['test']), ('přijato', False))
        self.assertTrue(card[str(plant['id'])]['test'])
        self.assertIn('1 chyba', A.week_summary(card, 0))

    def test_g8_an_unanswered_report_is_recalled_then_closed(self):
        A = self.agent
        A.ONLY = [('app', 'discord')]
        t = self.tasks.create({'title': 'Bug', 'project': 'app', 'tags': ['discord']})
        at = self.prague(12) - 100  # noon today, so "2 days later at noon" is 2 days whatever the hour the test runs
        A.NOW = lambda: at + 100
        self.tasks.comment(t['id'], '⏳ Needs you: Otestuj: Play → F8 → Key', 'Claude Code 1234abcd', at=at)
        A.stale_reports()
        self.assertEqual(self.messages(), [])
        A.NOW = lambda: self.prague(12, days=2)  # daytime: the reminder is not held for the morning
        A.stale_reports()
        A.stale_reports()
        self.assertEqual(len(self.messages()), 1)
        self.assertIn('⏰ Čeká na tebe 2 dny: Otestuj', self.messages()[0])
        A.NOW = lambda: self.prague(12, days=7)
        A.stale_reports()
        got = self.tasks.get(t['id'])
        self.assertEqual(got['status'], 'dropped')
        self.assertTrue(got['comments'][-1]['text'].startswith('⌛ Bez odpovědi 7 dní: zavřeno'))

    def test_g8_a_stale_fix_still_in_the_game_goes_back_to_be_reverted(self):
        A = self.agent
        A.ONLY = [('app', 'discord')]
        t = self.tasks.create({'title': 'Bug', 'project': 'app', 'tags': ['discord']})
        at = self.prague(12) - 100  # noon today, so "2 days later at noon" is 2 days whatever the hour the test runs
        A.NOW = lambda: at + 100
        self.tasks.comment(t['id'], 'Boosts: old -> new -- AGENT #%d' % t['id'], 'Claude Code 1234abcd', at=at - 5)
        self.tasks.comment(t['id'], '⏳ Needs you: Otestuj: Play → F8', 'Claude Code 1234abcd', at=at)
        A.NOW = lambda: at + 7 * 86400 + 60
        A.stale_reports()
        got = self.tasks.get(t['id'])
        self.assertEqual((got['status'], 'claude' in got['tags']), ('todo', True))
        self.assertIn('vrátí své změny', got['comments'][-1]['text'])
        self.assertEqual(A.attempts(got['comments']), 0)  # the revert run is not a fix attempt

    def criteria_conf(self):
        folder = os.path.join(self.dir.name, 'criteria')
        conf = self.agent.agent_conf
        self.agent.agent_conf = lambda where: dict(conf(where), criteria_dir=folder) if conf(where) else None
        return folder

    def test_g9_a_bug_without_hotovo_kdy_gets_a_question_not_a_fix(self):
        t = self.conf_run()
        self.criteria_conf()
        self.agent.main([])
        self.assertFalse(os.path.exists(self.seen))
        got = self.tasks.get(t['id'])
        self.assertEqual(sorted(got['tags']), ['discord', 'needs-spec'])
        self.assertIn('Kdy to bude opravené?', got['comments'][-1]['text'])
        self.assertEqual(self.messages(), [])  # a Decisions card, never a Telegram buzz (2026-11-22)
        cards = [q for q in self.server.Handler.questions.list('open') if q.get('task_id') == t['id']]
        self.assertEqual(len(cards), 1)
        self.assertEqual(self.agent.queue(), [])
        # His answer is the definition: needs-spec goes, the run sees his words frozen.
        self.agent.reply(t['id'], 'Klíč má po resetu zůstat v ruce.')
        self.assertEqual(sorted(self.tasks.get(t['id'])['tags']), ['claude', 'discord'])
        self.agent.main([])
        import json
        prompt = json.load(open(self.seen))['argv'][1]
        self.assertIn('Hotovo když (z odpovědi vlastníka)', prompt)
        self.assertIn('Klíč má po resetu zůstat v ruce.', prompt)

    def test_g9_a_done_test_in_the_body_is_the_spec_no_question(self):
        import json
        t = self.conf_run()
        folder = self.criteria_conf()
        self.tasks.update(t['id'], {'body': '## Goal\nx\n\n## Done-test\nThe error stops in the live stats.\n\n## Steps\n1. y'})
        self.agent.main([])
        self.assertNotIn('needs-spec', self.tasks.get(t['id'])['tags'])
        frozen = open(os.path.join(folder, f"{t['id']}.md")).read()
        self.assertIn('The error stops in the live stats.', frozen)
        self.assertNotIn('Steps', frozen)
        self.assertIn('Out of scope for this run', frozen)  # his OK and a later publish are never judged by the checker
        self.assertIn('The error stops in the live stats.', json.load(open(self.seen))['argv'][1])

    def test_g9_the_spec_card_names_the_bug_and_what_was_reported(self):
        t = self.conf_run(title='Clean the Snow: klíč mizí po resetu')
        self.tasks.update(t['id'], {'body': 'From Discord.\n\n## Discord\n```text\nSummary: Po resetu hráč nemá klíč.\n```'})
        self.criteria_conf()
        self.agent.main([])
        card = [q for q in self.server.Handler.questions.list('open') if q.get('task_id') == t['id']][0]
        self.assertIn('„klíč mizí po resetu“', card['question'])
        self.assertEqual(card['default_answer'], 'Už se neděje: klíč mizí po resetu')
        self.assertIn('Po resetu hráč nemá klíč.', card['context'])
        self.assertFalse(any(w.startswith('no context') for w in card['lint']), card['lint'])

    def test_g9_the_block_is_frozen_the_first_time_and_the_fixer_cannot_change_it(self):
        import json
        t = self.conf_run(mode='ask')
        folder = self.criteria_conf()
        body = '## Discord\nklíč mizí\n\n## Done when: zmrazeno\n- Po resetu má hráč klíč v ruce.\n\n## Done-test\nx'
        self.tasks.update(t['id'], {'body': body})
        self.agent.main([])
        frozen = open(os.path.join(folder, f"{t['id']}.md")).read()
        self.assertIn('Po resetu má hráč klíč v ruce.', frozen)
        self.assertNotIn('Done-test', frozen)
        self.assertIn('Po resetu má hráč klíč v ruce.', json.load(open(self.seen))['argv'][1])
        self.tasks.update(t['id'], {'body': '## Hotovo když\n- cokoli'})  # an edit later changes nothing
        self.assertIn('klíč v ruce', self.agent.criteria(self.tasks.get(t['id']), {'criteria_dir': folder}))

    def test_g10_auto_tasks_are_never_routed_and_needs_spec_waits(self):
        self.tasks.create({'title': 'Idea from the analyst', 'tags': ['auto']})
        self.tasks.create({'title': 'Open questions', 'tags': ['needs-spec']})
        ok = self.tasks.create({'title': 'His plain task'})
        self.assertEqual([t['id'] for t in self.agent.to_route()], [ok['id']])
        self.tasks.create({'title': 'spec, handed', 'tags': ['claude', 'needs-spec']})
        approved = self.tasks.create({'title': 'auto he approved', 'tags': ['claude', 'auto']})
        self.assertEqual([t['id'] for t in self.agent.queue()], [approved['id']])


if __name__ == '__main__':
    unittest.main()


class AnswerDispatcherTest(TaskAgentTest):
    """client/answer-dispatcher: every answer on http://beacon/decisions resumes its task, exactly once (task 1776)."""

    def setUp(self):
        super().setUp()
        loader = importlib.machinery.SourceFileLoader('answer_dispatcher', os.path.join(ROOT, 'client', 'answer-dispatcher'))
        spec = importlib.util.spec_from_loader('answer_dispatcher', loader)
        self.d = importlib.util.module_from_spec(spec)
        loader.exec_module(self.d)
        self.d.CLAUDE, self.d.TELL, self.d.SETTLE = os.environ['TASK_AGENT_CLAUDE'], os.environ['TASK_AGENT_TELL'], 0
        self.d.STATE, self.d.BRAKE = os.path.join(self.dir.name, 'dstate'), os.path.join(self.dir.name, 'brake.json')
        self.d.PROJECTS = self.dir.name
        self.d.task_agent_scope = lambda: []
        self.d.running = lambda: []
        self.started = []
        self.d.start_run = lambda q: (self.started.append(q['id']), self.d.run(q['id']))[0]  # inline, not systemd-run
        self.qs = self.server.Handler.questions

    def ask_on(self, t, text='⏳ Needs you: monthly or yearly?'):
        self.tasks.comment(t['id'], text, 'Claude Code 1234abcd')
        return self.qs.list('open', task=t['id'])[0]

    def test_an_answer_on_the_page_resumes_its_task_with_a_run_and_a_note(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web', 'body': '## Goal\nCheaper hosting.'})
        q = self.ask_on(t)
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.assertTrue(self.tasks.get(t['id'])['waiting_on_him'])
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        self.assertFalse(self.tasks.get(t['id'])['waiting_on_him'])
        self.d.tick()
        self.assertEqual(self.started, [q['id']])
        with open(self.seen) as f:
            seen = json.load(f)
        p = seen['argv'][seen['argv'].index('-p') + 1]
        for want in ('Cheaper hosting.', 'monthly or yearly?', 'His answer: yearly', f"tasks doing {t['id']}"):
            self.assertIn(want, p)
        notes = self.tasks.get(t['id'])['comments']
        self.assertEqual([c['author'] for c in notes if c['author'] in ('decisions', 'answer-dispatcher')],
                         ['decisions', 'answer-dispatcher'])
        self.assertIn('He decided ?', next(c['text'] for c in notes if c['author'] == 'decisions'))
        self.assertIn('Resumed after his answer', notes[-1]['text'])
        self.assertIn('All done.', notes[-1]['text'])
        self.assertEqual(self.messages(), [])  # a success is his Beacon card, never a Telegram echo (2026-11-22)
        self.assertEqual(self.qs.get(q['id'])['taken_by'], 'answer-dispatcher')
        self.d.tick()  # taken: never a second run
        self.assertEqual(self.started, [q['id']])

    def test_an_answer_resumes_the_session_that_asked(self):
        # task 1884: sessions end their turn after asking; the answer brings that same session back
        home = os.path.join(self.dir.name, 'claude-home')
        os.makedirs(os.path.join(home, 'projects', 'p'))
        with open(os.path.join(home, 'projects', 'p', 'sess1.jsonl'), 'w') as f:
            f.write(json.dumps({'cwd': self.dir.name}) + '\n')
        self.d.CLAUDE_HOME = home
        self.d.live_sessions = lambda: {}  # its chat is closed (or past the grace): the dispatcher brings it back
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.qs.create({'question': 'Monthly or yearly?', 'options': ['monthly', 'yearly'], 'task_id': t['id'],
                            'session_id': 'Claude Code:sess1', 'project': 'web', 'blocking': True})
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        self.d.tick()
        with open(self.seen) as f:
            argv = json.load(f)['argv']
        self.assertEqual(argv[argv.index('--resume') + 1], 'sess1')
        p = argv[argv.index('-p') + 1]
        self.assertIn('„Monthly or yearly?“ -> „yearly“', p)
        self.assertIn('never `tasks wait`', p)
        self.assertIn('The session carried on after his answer', self.tasks.get(t['id'])['comments'][-1]['text'])
        self.assertEqual(self.qs.get(q['id'])['taken_by'], 'answer-dispatcher')
        # task 1944: the run shows on its task page, where its steps can be followed live
        self.assertIn('Claude Code:sess1', [s['session_id'] for s in self.tasks.get(t['id'])['sessions']])

    def test_a_forked_run_gets_its_own_id_linked_to_the_task(self):
        home = os.path.join(self.dir.name, 'claude-home')
        os.makedirs(os.path.join(home, 'projects', 'p'))
        with open(os.path.join(home, 'projects', 'p', 'sess3.jsonl'), 'w') as f:
            f.write(json.dumps({'cwd': self.dir.name}) + '\n')
        self.d.CLAUDE_HOME = home
        self.d.live_sessions = lambda: {}
        self.d.session_open = lambda sid: True  # still open in his app: the answer runs in a copy
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.qs.create({'question': 'Monthly or yearly?', 'options': ['monthly', 'yearly'], 'task_id': t['id'],
                            'session_id': 'Claude Code:sess3', 'project': 'web', 'blocking': True})
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        self.d.tick()
        with open(self.seen) as f:
            argv = json.load(f)['argv']
        self.assertIn('--fork-session', argv)
        copy = argv[argv.index('--session-id') + 1]
        self.assertNotEqual(copy, 'sess3')
        self.assertIn(f'Claude Code:{copy}', [s['session_id'] for s in self.tasks.get(t['id'])['sessions']])

    def test_a_resumed_tap_closes_the_hubs_holder_task(self):
        home = os.path.join(self.dir.name, 'claude-home')
        os.makedirs(os.path.join(home, 'projects', 'p'))
        with open(os.path.join(home, 'projects', 'p', 'sess2.jsonl'), 'w') as f:
            f.write(json.dumps({'cwd': self.dir.name}) + '\n')
        self.d.CLAUDE_HOME = home
        self.d.live_sessions = lambda: {}
        q = self.qs.create({'question': 'Ship the build?', 'options': ['Go ahead', 'Not now'], 'kind': 'ending',
                            'session_id': 'Claude Code:sess2', 'project': 'web'})
        holder = self.tasks.get(q['task_id'])
        self.assertIn('decision', holder['tags'])
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(holder['id']))
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'Go ahead'}, self.me())
        self.d.tick()
        self.assertEqual(self.tasks.get(holder['id'])['status'], 'done')  # held only that card: nothing left in it

    def test_a_session_mid_turn_is_never_forked(self):
        # review of task 1884: a fork while the asker works would be a second agent in its folder
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.qs.create({'question': 'Monthly or yearly?', 'options': ['monthly', 'yearly'], 'task_id': t['id'],
                            'session_id': 'Claude Code:busy1', 'project': 'web', 'blocking': True})
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        self.d.live_sessions = lambda: {'Claude Code:busy1': 'working'}
        self.d.tick()
        self.assertEqual(self.started, [])
        self.assertIsNone(self.qs.get(q['id'])['applied_at'])

    def test_a_session_it_cannot_resume_gets_a_fresh_run_told_why(self):
        saved = os.environ.pop('GATE_MODE', None)  # test_questions turns the gate on at import: here it is in shadow
        self.addCleanup(lambda: saved is not None and os.environ.update(GATE_MODE=saved))
        self.d.CLAUDE_HOME = os.path.join(self.dir.name, 'empty-home')  # its log is on another machine
        self.d.live_sessions = lambda: {}
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.qs.create({'question': 'Monthly or yearly?', 'options': ['monthly', 'yearly'], 'task_id': t['id'],
                            'session_id': 'Claude Code:macsess', 'project': 'web'})  # went on with monthly
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        self.d.tick()
        with open(self.seen) as f:
            argv = json.load(f)['argv']
        self.assertNotIn('--resume', argv)
        p = argv[argv.index('-p') + 1]
        self.assertIn('runs on another machine', p)
        self.assertIn('change it to his pick', p)  # the fresh run switches the asker's default

    def test_a_resumed_asker_switches_its_own_default(self):
        q = {'id': 3, 'question': 'Monthly or yearly?', 'answer': 'yearly', 'default_answer': 'monthly',
             'matched_default': False, 'blocking': False}
        p = self.d.answer_prompt(q)
        self.assertIn("You already went on with 'monthly'", p)
        self.assertNotIn('fresh session', p)

    def test_his_note_on_the_task_resumes_it_too(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.call(f"/api/tasks/{t['id']}/comments", {'text': 'yearly'}, self.me())
        self.assertEqual(self.qs.get(q['id'])['answered_via'], 'task')
        self.d.tick()
        self.assertEqual(self.started, [q['id']])
        self.assertEqual(sum(c['author'] == 'decisions' for c in self.tasks.get(t['id'])['comments']), 0)  # his note is it

    def test_a_live_asker_gets_the_answer_and_no_run_starts(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/waiting", {}, {'Authorization': 'Bearer tok'})
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        self.d.tick()
        self.assertEqual(self.started, [])
        s1, r1 = self.call(f"/api/questions/{q['id']}/claim", {'by': 'chat'}, {'Authorization': 'Bearer tok'})
        s2, r2 = self.call(f"/api/questions/{q['id']}/claim", {'by': 'other'}, {'Authorization': 'Bearer tok'})
        self.assertEqual((r1['won'], r2['won'], r2['taken_by']), (True, False, 'chat'))
        self.d.tick()
        self.assertEqual(self.started, [])

    def test_an_answer_for_another_project_starts_fresh_even_with_its_asker_waiting(self):
        # 2026-11-22: 14 repos' cards on one cleanup task all landed in one 165k-token session.
        t = self.tasks.create({'title': 'Old PRs', 'project': 'beacon', 'body': '## Goal\nNo stale PRs.'})
        s, q = self.call('/api/questions', {'question': 'Merge the icon PR?', 'options': ['Yes', 'No'], 'kind': 'design',
                                            'project': 'ayrus', 'project_asked': True, 'blocking': True,
                                            'task_id': t['id'], 'session_id': 'Claude Code:fat1'},
                         {'Authorization': 'Bearer tok'})
        self.call(f"/api/questions/{q['id']}/waiting", {}, {'Authorization': 'Bearer tok'})
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'No'}, self.me())
        self.d.live_sessions = lambda: {'Claude Code:fat1': 'waiting'}
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.d.tick()
        self.assertEqual(self.started, [q['id']])
        with open(self.seen) as f:
            seen = json.load(f)
        p = seen['argv'][seen['argv'].index('-p') + 1]
        self.assertIn('instead of the session that asked, because it is work in ayrus', p)
        self.assertNotIn('gone now', p)

    def test_an_answer_its_asker_handed_off_starts_without_waiting_for_the_chat(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/waiting", {}, {'Authorization': 'Bearer tok'})
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        self.d.tick()
        self.assertEqual(self.started, [])  # same work, asker alive: its chat takes it
        self.call(f"/api/questions/{q['id']}/handoff", {'reason': 'this session holds 350k tokens'}, {'Authorization': 'Bearer tok'})
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.d.tick()
        self.assertEqual(self.started, [q['id']])
        with open(self.seen) as f:
            p = json.load(f)['argv']
        self.assertIn('because this session holds 350k tokens', p[p.index('-p') + 1])

    def test_a_box_answer_from_another_folder_still_waits_for_its_chat(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        self.tasks.link(t['id'], 'Claude Code:box2')
        s, q = self.call('/api/questions', {'question': 'Raise to 16?', 'options': ['Yes', 'No'], 'kind': 'next-step',
                                            'project': 'linux-setup', 'project_asked': True, 'task_id': t['id'],
                                            'session_id': 'Claude Code:box2'}, {'Authorization': 'Bearer tok'})
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'No'}, self.me())
        self.d.live_sessions = lambda: {'Claude Code:box2': 'waiting'}
        self.d.tick()
        self.assertEqual(self.started, [])  # the chat's own next step: never other work

    def test_a_held_answer_never_reaches_his_phone(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        with open(self.d.BRAKE, 'w') as f:
            json.dump({'hard': True}, f)  # held: nothing may take it
        self.d.WATCHDOG = 0
        self.d.tick()
        self.d.tick()
        self.assertEqual(self.started, [])
        self.assertEqual(self.messages(), [])  # the queue is the system working: no "waits", no "stuck"
        os.remove(self.d.BRAKE)  # the hold clears: the run that starts now is not "stuck"
        self.d.WATCHDOG = 900
        self.d.watchdog(self.qs.get(q['id']), time.time() + 60)
        self.assertEqual(self.messages(), [])

    def test_the_soft_brake_never_holds_his_answer(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        with open(self.d.BRAKE, 'w') as f:
            json.dump({'soft': {'at': 1, 'why': 'the week runs out'}, 'hard': None}, f)  # background jobs wait
        self.d.tick()
        self.assertEqual(len(self.started), 1)  # his answer is his own work, not a background job

    def test_the_watchdog_alarms_once_for_an_answer_nobody_took(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        self.d.WATCHDOG = 0
        self.d.watchdog(self.qs.get(q['id']), time.time() + 60)
        alarms = [m for m in self.messages() if 'no run has started' in m]
        self.assertEqual(len(alarms), 1)

    def test_a_long_queue_sends_one_alarm_not_one_per_answer(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        qs = []
        for i in range(3):
            q = self.ask_on(t, f'⏳ Needs you: option {i}?')
            self.call(f"/api/questions/{q['id']}/answer", {'answer': f'a{i}'}, self.me())
            qs.append(q)
        self.d.running = lambda: ['answer-run-9-sdeadbeef-pabcdef-1', 'answer-run-8-sfeedface-p123456-1']
        keys = []
        self.d.tell = lambda text, key=None, every=None: keys.append(key)  # telegram-alarm sends a key once
        for q in qs:  # two runs busy, the backlog waits: 2 hours later he hears once, for the queue
            self.d.watchdog(self.qs.get(q['id']), time.time() + 3 * 3600)
        self.assertEqual(set(keys), {'dispatch-stuck'})

    def test_a_backlog_draining_one_per_tick_never_says_stuck(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        for i in range(3):
            q = self.ask_on(t, f'⏳ Needs you: option {i}?')
            self.call(f"/api/questions/{q['id']}/answer", {'answer': f'a{i}'}, self.me())
        self.d.WATCHDOG = 0
        os.makedirs(self.d.STATE, exist_ok=True)
        with open(os.path.join(self.d.STATE, 'last_start'), 'w') as f:
            f.write(str(time.time() + 60))  # the tick below starts one: the others wait their turn
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.d.start_run = lambda q: self.started.append(q['id'])  # started, still running
        self.d.tick()
        self.assertEqual(len(self.started), 1)
        self.assertEqual([m for m in self.messages() if 'no run has started' in m], [])

    def test_an_answer_waiting_on_its_busy_chat_is_not_stuck(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        self.tasks.link(t['id'], 'Claude Code:busy1')
        s, q = self.call('/api/questions', {'question': 'Raise to 16?', 'options': ['Yes', 'No'], 'kind': 'next-step',
                                            'session_id': 'Claude Code:busy1'}, {'Authorization': 'Bearer tok'})
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'No'}, self.me())
        self.d.live_sessions = lambda: {'Claude Code:busy1': 'working'}
        self.d.WATCHDOG = 0
        self.d.tick()
        self.assertEqual(self.messages(), [])

    def test_a_queue_that_keeps_moving_never_alarms(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        self.d.running = lambda: ['answer-run-9-sdeadbeef-pabcdef-1', 'answer-run-8-sfeedface-p123456-1']
        os.makedirs(self.d.STATE, exist_ok=True)
        with open(os.path.join(self.d.STATE, 'last_start'), 'w') as f:
            f.write(str(time.time() + 3 * 3600 - 600))  # a run started 10 minutes ago
        self.d.watchdog(self.qs.get(q['id']), time.time() + 3 * 3600)
        self.assertEqual(self.messages(), [])

    def test_one_run_per_project_folder_at_a_time(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        other = {'id': 999, 'project': 'web', 'session_id': 'Claude Code:someone-else'}
        unit = f"answer-run-999-{self.d.session_tag(other)}-{self.d.project_tag(other)}-1"
        self.d.running = lambda: [unit]  # another session's answer is running in the same folder
        self.d.tick()
        self.assertEqual(self.started, [])
        self.d.running = lambda: []
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.d.tick()
        self.assertEqual(self.started, [q['id']])

    def test_a_run_killed_after_its_claim_is_retried_once_then_told(self):
        import subprocess
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        os.makedirs(os.path.join(self.d.STATE, 'taken'), exist_ok=True)

        def died():  # its run claimed it, then was killed (no except, no finally)
            self.assertTrue(self.d.claim(self.qs.get(q['id'])))
            dead = subprocess.Popen(['true'])
            dead.wait()
            with open(self.d.taken_file(q['id']), 'w') as f:
                json.dump({'pid': dead.pid, 'answer': 'yearly', 'at': time.time()}, f)
        died()
        with open(self.d.taken_file(78), 'w') as f:
            json.dump({'pid': os.getpid(), 'answer': 'monthly', 'at': time.time()}, f)  # still running
        self.d.reap()
        self.assertEqual([x['id'] for x in self.qs.list('answered', pending=True)], [q['id']])  # given back
        self.assertEqual(self.messages(), [])
        died()
        self.d.reap()
        alarms = [m for m in self.messages() if 'stopped twice' in m]
        self.assertEqual(len(alarms), 1)
        self.assertIn('yearly', alarms[0])
        self.assertTrue(os.path.exists(self.d.taken_file(78)))

    def test_a_finished_run_leaves_no_taken_mark(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.d.tick()
        self.assertFalse(os.path.exists(self.d.taken_file(q['id'])))
        with open(self.seen) as f:
            p = json.load(f)['argv']
        self.assertIn('git worktree add', p[p.index('-p') + 1])

    def test_the_scoped_agents_task_is_applied_its_way(self):
        os.environ['TASK_AGENT_ONLY'] = 'app:discord'
        self.addCleanup(os.environ.pop, 'TASK_AGENT_ONLY', None)
        self.d.task_agent_scope = lambda: [('app', 'discord')]
        ours = self.tasks.create({'title': 'Bug', 'project': 'app', 'tags': ['discord']})
        theirs = self.tasks.create({'title': 'Web', 'project': 'web'})
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(theirs['id']))
        for t in (ours, theirs):
            q = self.ask_on(t, '⏳ Needs you: works now?')
            self.call(f"/api/questions/{q['id']}/answer", {'answer': 'ok'}, self.me())
        self.d.tick()
        self.assertEqual(self.tasks.get(ours['id'])['comments'][-1]['text'], 'ok')
        self.assertIn('claude', self.tasks.get(ours['id'])['tags'])  # handed back to the task agent
        self.assertEqual(len(self.started), 1)  # the other one: a fresh run
        self.assertEqual([q for q in self.qs.list('answered', pending=True)], [])

    def test_mine_on_the_page_keeps_it_his(self):
        os.environ['TASK_AGENT_ONLY'] = 'app:discord'
        self.addCleanup(os.environ.pop, 'TASK_AGENT_ONLY', None)
        self.d.task_agent_scope = lambda: [('app', 'discord')]
        t = self.tasks.create({'title': 'Bug', 'project': 'app', 'tags': ['discord', 'claude']})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'mine'}, self.me())
        self.d.tick()
        self.assertEqual(self.tasks.get(t['id'])['tags'], ['discord'])

    def test_a_question_outside_a_task_files_one_and_the_default_closes_it(self):
        s, q = self.call('/api/questions', {'question': 'Call the helper fold or norm?', 'options': ['fold', 'norm'], 'kind': 'naming',
                                            'project': 'web'}, {'Authorization': 'Bearer tok'})
        t = self.tasks.get(q['task_id'])
        self.assertEqual((t['title'], t['tags'], t['project']), ('Decide: Call the helper fold or norm?', ['decision'], 'web'))
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'fold'}, self.me())
        self.d.tick()
        self.assertEqual(self.started, [])  # he took the default it went on with: nothing to run
        self.assertEqual(self.tasks.get(t['id'])['status'], 'done')

    def test_a_run_that_crashes_after_its_claim_tells_him(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        q = self.ask_on(t)
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'yearly'}, self.me())
        self.d.carry_out = lambda q: 1 / 0
        with self.assertRaises(ZeroDivisionError):
            self.d.tick()
        self.assertTrue(any("didn't go through" in m for m in self.messages()))

    def test_an_open_chat_gets_a_few_minutes_to_take_its_box_answer(self):
        t = self.tasks.create({'title': 'Pick a plan', 'project': 'web'})
        self.tasks.link(t['id'], 'Claude Code:box1')
        s, q = self.call('/api/questions', {'question': 'Raise to 16?', 'options': ['Yes', 'No'], 'kind': 'next-step',
                                            'session_id': 'Claude Code:box1'}, {'Authorization': 'Bearer tok'})
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'No'}, self.me())
        self.d.live_sessions = lambda: {'Claude Code:box1': 'waiting'}
        self.d.tick()
        self.assertEqual(self.started, [])  # its chat is open: his next prompt there takes it
        self.d.LIVE_GRACE = 0
        os.environ.update(STUB_MODE='progress', STUB_TASK=str(t['id']))
        self.d.tick()
        self.assertEqual(self.started, [q['id']])

    def test_a_filed_task_closes_when_the_asking_chat_takes_the_answer(self):
        s, q = self.call('/api/questions', {'question': 'Which bank?', 'options': ['Fio', 'Air'], 'kind': 'design',
                                            'session_id': 'Claude Code:lone'}, {'Authorization': 'Bearer tok'})
        self.call(f"/api/questions/{q['id']}/answer", {'answer': 'Air'}, self.me())
        s, r = self.call(f"/api/questions/{q['id']}/claim", {'by': 'Claude Code:lone'}, {'Authorization': 'Bearer tok'})
        self.assertTrue(r['won'])
        self.assertEqual(self.tasks.get(q['task_id'])['status'], 'done')


for _name in dir(TaskAgentTest):  # the dispatcher's tests only, not the task agent's again
    if _name.startswith('test') and _name not in AnswerDispatcherTest.__dict__:
        setattr(AnswerDispatcherTest, _name, None)
