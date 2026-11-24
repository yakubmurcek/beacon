"""The approval gate (task 1764): Layer 2's tool-call check, his yes from the chat, the hook's
output, and the hub's approval endpoint."""
import io
import json
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tasks import ROOT, LiveHub  # noqa: E402
import gate  # noqa: E402

T = gate.rules
CLIENT = os.path.join(ROOT, 'client', 'tasks')


def bash(cmd, cwd='/root/projects/weddings'):
    return T.risky_call('Bash', {'command': cmd}, cwd)


def transcript(*turns):
    """A transcript file from (origin, text) turns; origin None leaves turnOrigin out."""
    f = tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False)
    for origin, text in turns:
        rec = {'type': 'user', 'message': {'role': 'user', 'content': text}, 'timestamp': '2026-11-22T10:00:00Z'}
        if origin == 'compact':
            rec.update(turnOrigin='human', isCompactSummary=True)
        elif origin == 'tool':
            rec['message']['content'] = [{'type': 'tool_result', 'content': text}]
        elif origin:
            rec['turnOrigin'] = origin
        f.write(json.dumps(rec) + '\n')
        f.write(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'ok'}]}}) + '\n')
    f.close()
    return f.name


class RiskyCallTest(unittest.TestCase):
    def test_risky_commands_name_their_category(self):
        for cmd, cat in (
                ('vercel --prod', 'live'), ('vercel deploy --prod --yes', 'live'), ('npm publish', 'publish'),
                ('gh release create v1.0', 'publish'), ('git push --force origin main', 'delete'),
                ('git push origin --delete main', 'delete'), ('rm -rf ~/projects/weddings', 'delete'),
                ('sudo rm -r /root/projects/budget/data', 'delete'), ('sqlite3 data/usage.db "DROP TABLE tasks"', 'delete'),
                ('psql -c "delete from users"', 'delete'), ('python3 -c "import smtplib; smtplib.SMTP(\'smtp.gmail.com\').sendmail(a, b, m)"', 'send'),
                ("ssh cloud 'cd /srv/klient && vercel --prod'", 'live'), ('bash -c "npm publish"', 'publish'),
                ("python3 - <<'EOF'\nimport smtplib\nsmtplib.SMTP_SSL('smtp.mail.me.com').send_message(m)\nEOF", 'send'),
                ("psql $DB <<'SQL'\nDROP TABLE users;\nSQL", 'delete'), ('FOO=1 npx vercel --prod', 'live'),
                ('curl -X POST https://api.resend.com/emails -d @m.json', 'send'), ('echo hi | mail -s Hi x@y.cz', 'send'),
                ("osascript -e 'tell application \"Mail\" to send m'", 'send'), ('gh issue create -R vercel/next.js -t x', 'people'),
                ('gh pr comment 3 -R someone/repo -b hi', 'people'), ('gh repo delete example-user/x --yes', 'delete'),
                ('stripe refunds create --charge ch_1', 'pay'), ('fly deploy', 'live'), ('eas submit -p ios', 'publish')):
            hit = bash(cmd)
            self.assertTrue(hit, cmd)
            self.assertEqual(hit[0], cat, cmd)

    def test_ordinary_work_passes(self):
        for cmd in ('ls -la', 'git push -u origin feat/x', 'git push origin main', 'rm -rf node_modules dist .next',
                    'rm -rf /tmp/build-123', 'rm -f notes.txt', 'gh pr create -R example-user/weddings -t x',
                    'gh pr merge 5 --squash', 'cd ~/projects/lighthouse && vercel --prod', 'vercel deploy',
                    'grep -rn "send" .', 'echo "DROP TABLE" > notes.md && cat notes.md | head',
                    'python3 -m unittest', 'git push origin --delete feat/old-branch', 'find /tmp/x -name "*.log" -delete'):
            self.assertIsNone(bash(cmd, '/root/projects/beacon') if 'echo "DROP' not in cmd else None, cmd)
        self.assertIsNone(bash('grep -rn "send" .'))

    def test_words_that_only_carry_text_never_act(self):
        for cmd in ('tasks note 12 "deploy with vercel --prod tomorrow, then npm publish"',
                    'git commit -qm "fix: never git push --force; drop table guard"',
                    'echo "rm -rf ~/projects/x" >> notes.md', 'grep -n "DELETE FROM task_sessions" hub/*.py',
                    "cat > deploy.sh <<'EOF'\nvercel --prod\nnpm publish\nEOF",
                    'gh pr create -R example-user/x --title "npm publish flow" --body "runs vercel --prod"',
                    'gh pr merge 392 --squash --delete-branch', 'rm -rf $d', 'rm -rf /root/agent-work/task-5-1',
                    "python3 - <<'EOF'\nprint('smtplib and stripe are words here')\nEOF"):
            self.assertIsNone(bash(cmd), cmd)
        self.assertIsNone(T.risky_call('Bash', {'command': 'rm -r gt'}, '/tmp'))
        self.assertIsNone(T.risky_call('mcp__claude-code-remote__send_message', {'message': 'hi'}))
        self.assertIsNone(T.risky_call('mcp__claude_ai_Gmail__delete_draft', {}))

    def test_a_script_that_sends_is_caught_by_its_contents(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'blast.py'), 'w') as f:
                f.write('import smtplib\nsmtplib.SMTP("smtp.gmail.com").sendmail(a, b, m)\n')
            with open(os.path.join(d, 'ok.py'), 'w') as f:
                f.write('print("hi")\n')
            self.assertEqual(T.risky_call('Bash', {'command': 'python3 blast.py'}, d)[0], 'send')
            self.assertIsNone(T.risky_call('Bash', {'command': 'python3 ok.py'}, d))

    def test_mcp_tools(self):
        self.assertEqual(T.risky_call('mcp__claude_ai_Gmail__send_message', {})[0], 'send')
        self.assertIsNone(T.risky_call('mcp__claude_ai_Gmail__create_draft', {}))
        for tool in ('mcp__Read_and_Send_iMessages__send_imessage', 'mcp__discord__send_channel_message',
                     'mcp__whatsapp__send_whatsapp', 'mcp__slack__reply_to_thread', 'mcp__github__create_comment'):
            self.assertEqual(T.risky_call(tool, {})[0], 'send', tool)
        for tool in ('mcp__Read_and_Send_iMessages__read_imessages', 'mcp__figma__get_comments',
                     'mcp__Read_and_Send_iMessages__search_contacts'):
            self.assertIsNone(T.risky_call(tool, {}), tool)
        self.assertEqual(T.risky_call('mcp__claude_ai_Gmail__trash_thread', {})[0], 'delete')
        self.assertEqual(T.risky_call('mcp__claude_ai_Supabase__execute_sql', {'query': 'DROP TABLE x'})[0], 'delete')
        self.assertIsNone(T.risky_call('mcp__claude_ai_Supabase__execute_sql', {'query': 'select 1'}))
        self.assertEqual(T.risky_call('mcp__claude_ai_Google_Calendar__create_event', {'attendees': ['a@b.cz']})[0], 'people')
        self.assertIsNone(T.risky_call('mcp__claude_ai_Google_Calendar__create_event', {'summary': 'gym'}))
        self.assertIsNone(T.risky_call('Read', {'file_path': '/etc/passwd'}))


class ReplayMissesTest(unittest.TestCase):
    """Shapes the 2026-11-22 replay showed slipping through (task 1764); each one is a real past call."""

    def test_caught(self):
        for cmd, cat, cwd in (
                ("cd /tmp/s && PW=$(secrets get ICLOUD_MAIL_APP_PASSWORD) python3 - <<'EOF'\nimport smtplib\n"
                 "s=smtplib.SMTP_SSL('smtp.mail.me.com', 465)\nEOF", 'send', '/root'),
                ('curl -s -X POST -H "Authorization: Bearer $T" "https://api.apify.com/v2/acts/compass~crawler/runs?maxTotalChargeUsd=1" -d @in.json',
                 'pay', '/root'),
                ('npx -y vercel@latest integration add upstash/upstash-kv --name kv --yes', 'pay', '/root/projects/jev-fit'),
                ('v() { npx -y vercel@65.10.0 "$@"; }; printf x | v env add TOKEN production', 'live', '/root/projects/excel-s-ai'),
                ('vercel domains add couple-site.example', 'live', '/root/projects/weddings'),
                ('npx vercel@latest redeploy https://x.vercel.app', 'live', '/root/projects/jev-fit'),
                ('api(){ curl -s "$@"; }\napi -X POST "https://api.vercel.com/v10/projects/p/domains" -d \'{"name":"x.cz"}\'', 'live', '/root'),
                ("python3 - <<'EOF'\nimport urllib.request\nsql=open('supabase/migrations/1_x.sql').read()\n"
                 "urllib.request.Request('https://api.supabase.com/v1/projects/abc/database/query', data=sql, method='POST')\nEOF",
                 'live', '/root/projects/weddings'),
                ('U=https://abc.supabase.co/rest/v1/rpc; curl -s -X POST "$U/admin_update_content" -d \'{}\'', 'live', '/root'),
                ('curl -s -X POST https://abc.supabase.co/rest/v1/rpc/rsvp_submit -d \'{}\'', 'live', '/root'),
                ('./sqlq.sh /root/projects/weddings/supabase/migrations/2026_x.sql', 'live', '/tmp/s'),
                ('gh gist edit 61d8e3a6 -a README.md', 'publish', '/root'),
                ('git -C "$S/preview" push origin gh-pages', 'publish', '/root'),
                ('gh api repos/example-user/x/pages -X POST -f "source[branch]=main"', 'publish', '/root'),
                ('node ~/.claude/scripts/asc.mjs POST /v1/betaGroups/1/relationships/builds g.json', 'publish', '/root'),
                ("ssh cloud 'systemctl restart client-api'", 'live', '/root'),
                ('curl -sS -X POST -d \'[{"role":"member"}]\' https://api.tailscale.com/api/v2/tailnet/-/user-invites', 'people', '/root'),
                ('curl -s -X POST https://flashletter.app/api/send-test-email', 'send', '/root'),
                ('sudo -n wipefs /dev/sdb', 'delete', '/root'), ('sudo -n zpool detach vault /dev/sdc', 'delete', '/root'),
                ('D="/Volumes/SANDISK USB/s"\nrm -f "$D/backup.tar.gz.gpg"', 'delete', '/root'),
                ('cd "/Volumes/T7 Shield/iCloud Photos"\nrm "iCloud Photos.zip"', 'delete', '/root'),
                ('gh release delete v1 --yes', 'delete', '/root'),
                ("node -e \"const {list,del}=require('@vercel/blob');list().then(r=>del(r.blobs.map(b=>b.url)))\"", 'delete', '/root'),
                ('git reset --hard origin/main', 'delete', '/root/projects/weddings'), ('git stash drop', 'delete', '/root/.claude'),
                ('git checkout -- .', 'delete', '/root/projects/weddings'),
                ("ssh gamer 'Remove-Item -Recurse -Force C:\\Users\\murcj\\transcribe-local'", 'delete', '/root'),
                ("osascript -e 'tell application \"Reminders\"\nrepeat 60 times\ndelete (item 1 of rs)\nend repeat'", 'delete', '/root'),
                ('cd ~/Projects/infinitemoney-alarm && vercel deploy --prod --yes', 'live', '/root')):
            hit = T.risky_call('Bash', {'command': cmd}, cwd)
            self.assertTrue(hit, cmd)
            self.assertEqual(hit[0], cat, cmd)
        self.assertEqual(T.risky_call('mcp__clickup__clickup_create_comment', {})[0], 'send')
        for cmd in ('x=$((1<<4))\nvercel --prod --yes', 'read A <<< "x"\nvercel --prod --yes', 'cat <<<\'y\'\nvercel --prod --yes',
                    "cat <<'EOF'\nunclosed body\nvercel --prod --yes"):
            self.assertEqual((T.risky_call('Bash', {'command': cmd}, '/root/projects/weddings') or [None])[0], 'live', cmd)

    def test_a_question_is_not_a_yes(self):
        words = [("Okay Hermes is responding so everything's correct now. I can close. This is it, fixed and ready??", None)]
        self.assertIsNone(T.chat_yes(None, 'delete', words=words))
        self.assertTrue(T.chat_yes(None, 'delete', words=[('ok', None)]))

    def test_still_passes(self):
        for cmd, cwd in (
                ('curl -s https://api.apify.com/v2/acts/x~y/runs/last', '/root'), ('vercel env ls', '/root/projects/x'),
                ('vercel env add K preview', '/root/projects/x'),
                ("echo '{\"ssoProtection\":null}' > b.json && vercel api /v9/projects/p -X PATCH --input b.json", '/root/projects/x'),
                ('vercel curl "/api/owner?key=1" -X POST --deployment https://p.vercel.app', '/root/projects/x'),
                ('curl -X POST https://api.supabase.com/v1/projects/abc/database/query -d \'{"query":"select 1"}\'', '/root'),
                ('curl -s -X POST https://abc.supabase.co/rest/v1/rpc/get_wedding -d \'{}\'', '/root'),
                ('git reset --hard', '/tmp/x'), ('git reset --hard origin/main', '/root/projects/x/.claude/worktrees/a'),
                ('git checkout -b feat/x', '/root/projects/x'), ('git stash push -m "drop me"', '/root/projects/x'),
                ("ssh gamer 'Remove-Item -Recurse C:\\Users\\m\\AppData\\Local\\Temp\\x'", '/root'),
                ("ssh cloud 'systemctl restart agent-usage'", '/root')):
            self.assertIsNone(T.risky_call('Bash', {'command': cmd}, cwd), cmd)


class HisYesTest(unittest.TestCase):
    def test_only_his_own_words_count(self):
        self.assertTrue(T.chat_yes(transcript(('human', 'pošli ten mail Štěpánovi')), 'send'))
        self.assertTrue(T.chat_yes(transcript(('human', 'napiš draft'), ('human', 'ano')), 'send'))
        self.assertIsNone(T.chat_yes(transcript(('sdk', 'Send the mail to the client.')), 'send'))  # a brief by an agent
        self.assertIsNone(T.chat_yes(transcript(('task_notification', 'send it')), 'send'))
        self.assertIsNone(T.chat_yes(transcript(('compact', 'He said: send it')), 'send'))
        self.assertIsNone(T.chat_yes(transcript(('human', 'fix the bug'), ('tool', 'yes, send')), 'send'))

    def test_a_no_or_a_wait_wins(self):
        for text in ('neposílej to', 'ne', 'počkej, nejdřív mi ukaž draft', "don't send it yet", 'jen draft'):
            self.assertIsNone(T.chat_yes(transcript(('human', text)), 'send'), text)

    def test_a_bare_yes_counts_only_as_his_latest_message(self):
        self.assertTrue(T.chat_yes(transcript(('human', 'ok')), 'live'))
        self.assertIsNone(T.chat_yes(transcript(('human', 'ok'), ('human', 'teď oprav ten layout')), 'live'))
        self.assertIsNone(T.chat_yes(transcript(('human', 'deploy it')), 'send'))  # a yes to something else

    def test_an_unreadable_transcript_is_no_yes(self):
        self.assertIsNone(T.chat_yes('/nonexistent.jsonl', 'send'))
        self.assertIsNone(T.chat_yes(None, 'send'))


class HookTest(unittest.TestCase):
    def run_hook(self, ev, mode='shadow', hub=None, env=None):
        with tempfile.TemporaryDirectory() as home:
            with open(os.path.join(home, '.claude-mode'), 'w') as f:
                f.write(mode)
            with mock.patch.object(T, 'gate_mode', return_value=mode), mock.patch.object(T, 'gate_log') as log, \
                    mock.patch.object(T, 'hub_yes', hub or (lambda *a: None)):
                out = io.StringIO()
                with mock.patch('sys.stdout', out):
                    T.hook_gate(io.StringIO(json.dumps(ev)))
                return out.getvalue(), log

    EV = {'session_id': 's1', 'transcript_path': '/nonexistent', 'cwd': '/root/projects/weddings',
          'tool_name': 'Bash', 'tool_input': {'command': 'vercel --prod'}}

    def test_shadow_logs_and_never_prints(self):
        out, log = self.run_hook(self.EV, 'shadow')
        self.assertEqual(out, '')
        rec = log.call_args[0][0]
        self.assertEqual((rec['category'], rec['mode'], rec['approved']), ('live', 'shadow', None))

    def test_enforce_denies_in_czech_and_never_allows(self):
        out, _ = self.run_hook(self.EV, 'enforce')
        d = json.loads(out)['hookSpecificOutput']
        self.assertEqual(d['permissionDecision'], 'deny')
        self.assertIn('jeho ano', d['permissionDecisionReason'])
        self.assertIn('tasks ask', d['permissionDecisionReason'])
        out, log = self.run_hook({**self.EV, 'tool_input': {'command': 'ls'}}, 'enforce')
        self.assertEqual(out, '')
        log.assert_not_called()

    def test_his_answered_question_lets_it_through(self):
        out, log = self.run_hook(self.EV, 'enforce', hub=lambda *a: (12, 'Ano, nasaď to'))
        self.assertEqual(out, '')
        self.assertIn('question #12', log.call_args[0][0]['approved'])

    def test_hub_down_irreversible_waits_reversible_goes_on(self):
        def down(*a):
            raise OSError('unreachable')
        out, _ = self.run_hook(self.EV, 'enforce', hub=down)  # live: reversible
        self.assertEqual(out, '')
        out, _ = self.run_hook({**self.EV, 'tool_input': {'command': 'npm publish'}}, 'enforce', hub=down)
        self.assertEqual(json.loads(out)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_a_crash_denies_only_an_irreversible_call_in_enforce(self):
        with mock.patch.object(T, 'gate', side_effect=RuntimeError('boom')):
            out, _ = self.run_hook({**self.EV, 'tool_input': {'command': 'npm publish'}}, 'enforce')
            self.assertEqual(json.loads(out)['hookSpecificOutput']['permissionDecision'], 'deny')
            out, _ = self.run_hook({**self.EV, 'tool_input': {'command': 'ls'}}, 'enforce')
            self.assertEqual(out, '')
            out, _ = self.run_hook({**self.EV, 'tool_input': {'command': 'npm publish'}}, 'shadow')
            self.assertEqual(out, '')

    def test_bad_input_never_blocks(self):
        out = subprocess.run([sys.executable, CLIENT, 'hook-gate'], input='not json', capture_output=True, text=True,
                             env={**os.environ, 'TASKS_URL': 'http://127.0.0.1:9', 'HOME': tempfile.gettempdir()}, timeout=20)
        self.assertEqual((out.returncode, out.stdout), (0, ''))

    def test_the_mode_file_and_the_env(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {'HOME': home}, clear=False):
                os.environ.pop('GATE_MODE', None)
                self.assertEqual(T.gate_mode(), 'shadow')  # no file: shadow
                os.makedirs(os.path.join(home, '.claude'))
                with open(os.path.join(home, '.claude', 'gate-mode'), 'w') as f:
                    f.write('enforce\n')
                self.assertEqual(T.gate_mode(), 'enforce')
                with mock.patch.dict(os.environ, {'GATE_MODE': 'shadow'}):
                    self.assertEqual(T.gate_mode(), 'enforce')  # the env never lowers it
                with open(os.path.join(home, '.claude', 'gate-mode'), 'w') as f:
                    f.write('shadow')
                with mock.patch.dict(os.environ, {'GATE_MODE': 'enforce'}):
                    self.assertEqual(T.gate_mode(), 'enforce')  # but can raise it (the live test)

    def test_fast_enough(self):
        ev = json.dumps({**self.EV, 'tool_input': {'command': 'ls -la'}})
        times = []
        for _ in range(5):
            t = time.time()
            subprocess.run([sys.executable, CLIENT, 'hook-gate'], input=ev, capture_output=True, text=True, timeout=20)
            times.append(time.time() - t)
        self.assertLess(sorted(times)[2], 0.25)  # ~50 ms here; generous for a loaded CI box


class ApprovalApiTest(LiveHub, unittest.TestCase):
    def ask(self, **f):
        return self.server.Handler.questions.create({'question': 'Pošlu mail klientovi?', 'options': ['Ano', 'Ne'],
                                                     'session_id': 'Claude Code:s1', **f})

    def test_his_yes_on_the_page_approves_this_category_for_this_session(self):
        qs = self.server.Handler.questions
        q = self.ask()
        self.assertEqual(self.call('/api/gate/approval?session=Claude+Code:s1&category=send')[1], {})
        qs.answer(q['id'], {'answer': 'Ano'}, via='page')
        code, out = self.call('/api/gate/approval?session=Claude+Code:s1&category=send')
        self.assertEqual(out['id'], q['id'])
        self.assertEqual(self.call('/api/gate/approval?session=Claude+Code:s2&category=send')[1], {})  # another session
        self.assertEqual(self.call('/api/gate/approval?session=Claude+Code:s1&category=pay')[1], {})  # another category

    def test_a_no_or_an_agent_logged_answer_approves_nothing(self):
        qs = self.server.Handler.questions
        qs.answer(self.ask()['id'], {'answer': 'Ne, počkej'}, via='page')
        qs.answer(self.ask()['id'], {'answer': 'Ano'}, via='cli')  # an agent's `tasks answer`
        qs.answer(self.ask()['id'], {'answer': 'Ano'}, via='chat')  # logged by the agent itself
        self.assertEqual(self.call('/api/gate/approval?session=Claude+Code:s1&category=send')[1], {})

    def test_an_old_yes_expires(self):
        qs = self.server.Handler.questions
        q = self.ask()
        qs.answer(q['id'], {'answer': 'Ano'}, via='page')
        self.assertIsNone(qs.approval('Claude Code:s1', 'send', now=time.time() + 25 * 3600))

    def test_a_task_question_approves_the_sessions_working_on_it(self):
        t = self.server.Handler.tasks.create({'title': 'Mail the client the offer'}, 'owner')
        self.server.Handler.tasks.link(t['id'], 'Claude Code:s9')
        qs = self.server.Handler.questions
        q = qs.create({'question': 'Send the offer mail to the client?', 'options': ['Yes', 'No'], 'task_id': t['id']})
        qs.answer(q['id'], {'answer': 'Yes, send it'}, via='task')
        self.assertEqual(qs.approval('Claude Code:s9', 'send')['id'], q['id'])

    def test_the_log_takes_his_servers_and_is_capped(self):
        rec = {'ts': time.time(), 'machine': 'devbox', 'category': 'live', 'what': 'x'}
        self.assertEqual(self.call('/api/gate/log', rec)[0], 401)  # who wrote it is tailscaled's answer, see test_auth
        server = {'X-Forwarded-For': self.SERVER_IP}
        code, _ = self.call('/api/gate/log', rec, server)
        self.assertEqual(code, 201)
        code, out = self.call('/api/gate/log')
        self.assertEqual((out['total'], out['by'][0]['machine'], out['by'][0]['unapproved']), (1, 'devbox', 1))
        code, _ = self.call('/api/gate/log', raw=b'x' * 9000, headers={'Content-Type': 'application/json', **server})
        self.assertEqual(code, 413)


if __name__ == '__main__':
    unittest.main()
