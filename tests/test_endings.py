"""Endings (task 1857): every session's last reply is one card in the feed; a tap goes back into the session."""
import importlib.machinery
import importlib.util
import json
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tasks import ROOT, LiveHub  # noqa: E402
from store import Store  # noqa: E402
from tasks import Tasks  # noqa: E402
from questions import Questions  # noqa: E402
from endings import Endings, state_of, strip_mark  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = load('tasks_cli_endings', os.path.join(ROOT, 'client', 'tasks'))


class EndingsStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, 'u.db'))
        self.tasks = Tasks(self.store)
        self.qs = Questions(self.store, self.tasks)
        self.e = Endings(self.store, self.qs)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_mark_is_the_state(self):
        self.assertEqual([state_of(x) for x in ('✅ Done', '⚠️ Not yet', '❓ Need you', '**✅ Done**', 'Just chat')],
                         ['done', 'stuck', 'needs', 'done', 'done'])
        self.assertEqual(strip_mark('⚠️ **Not yet**: tests fail'), 'Not yet: tests fail')

    def test_one_card_per_session_and_a_new_reply_brings_it_back(self):
        a = self.e.post({'session_id': 'abc', 'line': '✅ Site live', 'next': 'open it', 'actions': ['Go ahead']})
        self.assertEqual((a['session_id'], a['state'], a['line'], a['actions']), ('Claude Code:abc', 'done', 'Site live', ['Go ahead']))
        self.e.clear(a['id'])
        self.assertEqual(self.e.feed(), [])
        self.e.post({'session_id': 'abc', 'line': '✅ Site live', 'next': 'open it', 'actions': ['Go ahead']})  # the same reply again (a block, a re-stop): stays cleared
        self.assertEqual(self.e.feed(), [])
        b = self.e.post({'session_id': 'abc', 'line': '⚠️ Deploy failed'})
        self.assertEqual(b['id'], a['id'])
        self.assertEqual([(x['state'], x['line']) for x in self.e.feed()], [('stuck', 'Deploy failed')])

    def test_the_backstop_never_overwrites_the_hook(self):
        self.e.post({'session_id': 'abc', 'line': '✅ Site live'})
        self.e.post({'session_id': 'abc', 'line': '⚠️ guessed', 'source': 'backstop'})
        self.assertEqual(self.e.feed()[0]['line'], 'Site live')

    def test_a_needs_card_is_the_open_decision_and_goes_once_answered(self):
        self.e.post({'session_id': 'abc', 'line': '❓ Merge it?'})
        q = self.qs.create({'question': 'Merge it?', 'options': ['Yes', 'No'], 'session_id': 'Claude Code:abc'})
        self.assertEqual(self.e.feed()[0]['open_question'], q['id'])  # the page shows the decision, not this
        self.qs.answer(q['id'], {'answer': 'Yes'})
        self.assertEqual(self.e.feed(), [])

    def test_needs_and_stuck_are_deck_cards(self):
        a = self.e.post({'session_id': 'abc', 'line': '⚠️ Build fails', 'next': 'retry with the old Node', 'actions': ['Retry']})
        [q] = self.qs.list('open')
        self.assertEqual((q['kind'], q['session_id'], q['default_answer']), ('ending', 'Claude Code:abc', 'Retry'))
        self.assertIn('retry with the old Node', q['context'])
        self.e.post({'session_id': 'abc', 'line': '⚠️ Build fails', 'next': 'retry with the old Node', 'actions': ['Retry']})
        self.assertEqual([x['id'] for x in self.qs.list('open')], [q['id']])  # a re-stop deals no second card
        self.qs.withdraw(q['id'], 'dismissed by him: not needed')
        self.e.post({'session_id': 'abc', 'line': '⚠️ Build fails', 'next': 'retry with the old Node', 'actions': ['Retry']})
        self.assertEqual(self.qs.list('open'), [])  # ...nor brings back one he dropped
        self.e.post({'session_id': 'abc', 'line': '❓ Use Node 20?', 'title': 'Build fix'})
        [n] = self.qs.list('open')
        self.assertEqual((n['question'], n['options'], n['default_answer']), ('Use Node 20?', [], None))  # no made-up button
        self.assertIn('Build fix', n['context'])
        self.e.post({'session_id': 'abc', 'line': '✅ Fixed it myself'})
        self.assertEqual(self.qs.list('open'), [])  # the next reply settles it
        self.e.post({'session_id': 'def', 'line': '✅ Shipped'})
        self.assertEqual(self.qs.list('open'), [])  # done is never a card
        self.assertEqual(self.e.get(a['id'])['state'], 'done')

    def test_a_decision_the_session_files_replaces_its_card(self):
        self.e.post({'session_id': 'abc', 'line': '❓ Merge it?'})
        q = self.qs.create({'question': 'Merge the one-pile change?', 'options': ['Yes', 'No'], 'session_id': 'Claude Code:abc'})
        self.assertEqual([x['id'] for x in self.qs.list('open')], [q['id']])
        self.e.post({'session_id': 'abc', 'line': '❓ Merge it, really?'})
        self.assertEqual([x['id'] for x in self.qs.list('open')], [q['id']])  # its own decision stands alone

    def test_a_not_yet_reply_without_buttons_is_no_card(self):
        self.e.post({'session_id': 'abc', 'line': '⚠️ Keeping the key for now', 'next': 'tell me once you renew'})
        self.assertEqual(self.qs.list('open'), [])  # a status, not a decision: it stays in the log
        self.assertEqual([x['state'] for x in self.e.feed()], ['stuck'])

    def test_a_backstop_guess_or_a_stand_in_button_is_no_card(self):
        self.e.post({'session_id': 'abc', 'line': '❓ Needs spend details to continue', 'source': 'backstop'})
        self.e.post({'session_id': 'def', 'line': '⚠️ Keeping the key', 'next': 'tell me', 'actions': ['Go ahead']})
        self.assertEqual(self.qs.list('open'), [])

    def test_the_card_carries_the_reply_as_its_why(self):
        self.e.post({'session_id': 'abc', 'line': '❓ Spend cap for the ads?',
                     'reply': '❓ Spend cap for the ads?\n- The campaign stops at 0 Robux\n👉 Next: say a cap'})
        [q] = self.qs.list('open')
        self.assertIn('The campaign stops at 0 Robux', q['context'])

    def test_a_needs_reply_is_reworded_into_a_decision(self):
        asked = []
        def model(prompt):
            asked.append(prompt)
            return {'question': 'Stop agents offering a "not yet" button?', 'pick': 'Build it',
                    'options': [{'label': 'Build it', 'detail': 'agents use Later instead'}, {'label': 'Leave it'}],
                    'why': ['A "not yet" tap starts a whole new session for nothing']}
        e = Endings(self.store, self.qs, model=model)
        e.post({'session_id': 'abc', 'line': '❓ Yes, I agree: build it?', 'title': 'Decisions polish',
                'reply': '❓ Yes, I agree: build it?\n- a tap wastes a run\n👉 Next: reply udělej'})
        for _ in range(100):
            [q] = self.qs.list('open')
            if q['options']:
                break
            time.sleep(0.02)
        self.assertEqual((q['question'], [o['label'] for o in q['options']], q['default_answer']),
                         ('Stop agents offering a "not yet" button?', ['Build it', 'Leave it'], 'Build it'))
        self.assertIn('starts a whole new session', q['context'])
        self.assertIn('a tap wastes a run', asked[0])

    def test_a_failed_rewording_leaves_the_plain_card(self):
        e = Endings(self.store, self.qs, model=lambda p: None)
        a = e.post({'session_id': 'abc', 'line': '❓ Which logo?', 'reply': '❓ Which logo?\nthe blue or the red'})
        [q] = self.qs.list('open')
        e.reword(q['id'], e.get(a['id']), None)
        self.assertEqual(self.qs.get(q['id'])['question'], 'Which logo?')

    def test_cards_from_the_older_rule_are_rebuilt_once(self):
        a = self.e.post({'session_id': 'abc', 'line': '⚠️ Keeping the key', 'actions': ['Drop it']})
        self.e.post({'session_id': 'def', 'line': '❓ Which logo?', 'title': 'Logo'})
        with self.store.connect() as con:  # as the older rule dealt them: no rule mark, made-up button, title prefix
            con.execute("UPDATE endings SET actions = '[]' WHERE id = ?", (a['id'],))
            con.execute("UPDATE questions SET source = 'ending-card:' || substr(source, 13, instr(substr(source, 13), ':')) "
                        "|| 'abcdef123456', question = 'Logo: Which logo?'")
        Endings(self.store, self.qs)
        [q] = self.qs.list('open')
        self.assertEqual((q['question'], q['session_id']), ('Which logo?', 'Claude Code:def'))
        Endings(self.store, self.qs)
        self.assertEqual([x['id'] for x in self.qs.list('open')], [q['id']])  # once, not every start

    def test_old_cards_join_the_deck_when_the_hub_starts(self):
        bare = Endings(self.store, None)
        bare.post({'session_id': 'abc', 'line': '⚠️ Deploy failed', 'actions': ['Retry']})
        self.assertEqual(self.qs.list('open'), [])
        Endings(self.store, self.qs)
        self.assertEqual([x['kind'] for x in self.qs.list('open')], ['ending'])

    def test_a_hub_start_leaves_an_old_reply_off_the_deck(self):
        bare = Endings(self.store, None)
        a = bare.post({'session_id': 'abc', 'line': '❓ Remove the not-yet button?'})
        with self.store.connect() as con:  # a reply from yesterday, its work shipped by another session since
            con.execute('UPDATE endings SET updated_at = updated_at - 17 * 3600 WHERE id = ?', (a['id'],))
        Endings(self.store, self.qs)
        self.assertEqual(self.qs.list('open'), [])

    def test_a_tap_is_an_answer_the_dispatcher_takes(self):
        a = self.e.post({'session_id': 'abc', 'line': '✅ Built it', 'next': 'deploy', 'actions': ['Deploy it'], 'project': 'beacon'})
        out = self.e.act(a['id'], 'Deploy it')
        q = out['question']
        self.assertEqual((q['kind'], q['status'], q['answer'], q['session_id'], q['answered_via']),
                         ('ending', 'answered', 'Deploy it', 'Claude Code:abc', 'page'))
        self.assertIsNone(q['applied_at'])  # pending: answer-dispatcher claims it
        self.assertIn('deploy', q['context'])
        self.assertEqual(self.e.feed(), [])
        again = self.e.act(a['id'], 'Deploy it')  # a double tap files nothing twice
        self.assertEqual(again['question']['id'], q['id'])
        pending = self.qs.list('answered', pending=True)
        self.assertEqual([x['id'] for x in pending], [q['id']])

    def test_a_card_answered_on_the_deck_leaves_no_second_button(self):
        self.e.post({'session_id': 'abc', 'line': '❓ Ship the build to TestFlight?', 'actions': ['Ship it', 'Not now']})
        q = self.qs.list('open')[0]
        self.qs.answer(q['id'], {'answer': 'Ship it'})  # on Decisions, not through the card's own tap
        self.assertEqual(self.e.feed(), [])  # nothing left to tap: a second tap would carry it out twice

    def test_his_own_words_are_the_answer(self):
        a = self.e.post({'session_id': 'abc', 'line': '❓ Build it?', 'actions': ['Go ahead']})
        q = self.e.act(a['id'], None, words='Yes, but keep the old button for a week')['question']
        self.assertEqual((q['status'], q['answer'], q['options']),
                         ('answered', 'Yes, but keep the old button for a week', [{'label': 'Go ahead'}]))
        self.assertIn('own words', q['context'])
        b = self.e.post({'session_id': 'def', 'line': '⚠️ Stuck'})  # no buttons at all: still answerable
        self.assertEqual(self.e.act(b['id'], '', words='try the other port')['question']['answer'], 'try the other port')
        with self.assertRaises(ValueError):
            self.e.act(self.e.post({'session_id': 'ghi', 'line': '✅ x'})['id'], '', words='   ')

    def test_a_machine_outside_beacon_gets_no_card_and_no_backstop(self):
        now = time.time()
        self.e.saw('laptop', self.store.clean_live([  # config.example.json: the laptop is manual
            {'id': 'm', 'agent': 'Claude Code', 'state': 'waiting', 'doing': '⚠️ exploring', 'last': now - 600}]))
        self.assertEqual(self.e.missed(), [])
        self.assertIn('skipped', self.e.post({'session_id': 'm', 'line': '❓ Which one?', 'machine': 'laptop'}))
        self.assertEqual((self.e.feed(), self.qs.list('open')), ([], []))

    def test_missed_turns_for_the_backstop(self):
        now = time.time()
        self.e.saw('devbox', self.store.clean_live([
            {'id': 'a', 'agent': 'Claude Code', 'state': 'waiting', 'doing': '✅ did a', 'last': now - 600},
            {'id': 'b', 'agent': 'Claude Code', 'state': 'waiting', 'doing': '⚠️ b stuck', 'last': now - 600},
            {'id': 'c', 'agent': 'Claude Code', 'state': 'working', 'doing': 'Editing x', 'last': now},
            {'id': 'd', 'agent': 'Claude Code', 'state': 'waiting', 'doing': '✅ old', 'last': now - 3 * 86400},
        ]))
        self.e.post({'session_id': 'a', 'line': '✅ did a'})
        self.assertEqual([m['session_id'] for m in self.e.missed()], ['Claude Code:b'])

    def test_a_new_turn_with_the_same_line_is_a_new_card(self):
        a = self.e.post({'session_id': 'abc', 'line': '✅ Done.', 'next': 'deploy', 'reply': '✅ Done.\nnext: deploy'})
        self.e.act(a['id'], 'Go ahead')
        b = self.e.post({'session_id': 'abc', 'line': '✅ Done.', 'next': 'check it', 'reply': '✅ Done.\nnext: check it'})
        self.assertEqual((b['cleared_at'], b['acted'], b['question_id']), (None, None, None))
        self.assertEqual(self.e.act(b['id'], 'Go ahead')['question']['answer'], 'Go ahead')
        self.assertEqual(len(self.qs.list('answered', kind='ending')), 2)

    def test_the_backstop_fills_a_turn_the_hook_missed(self):
        self.e.post({'session_id': 'abc', 'line': '✅ A'})
        later = time.time() + 1000
        self.e.saw('devbox', self.store.clean_live([{'id': 'abc', 'agent': 'Claude Code', 'state': 'waiting', 'doing': '⚠️ B', 'last': later}]))
        self.assertEqual([m['session_id'] for m in self.e.missed()], ['Claude Code:abc'])
        self.assertEqual(self.e.post({'session_id': 'abc', 'line': '⚠️ B', 'source': 'backstop'})['line'], 'B')

    def test_a_tap_never_copies_the_reply(self):
        a = self.e.post({'session_id': 'abc', 'line': '✅ Built', 'next': 'deploy', 'reply': 'SECRET-ish reply body'})
        q = self.e.act(a['id'], 'Go ahead')['question']
        self.assertNotIn('SECRET', q['context'])
        self.assertNotIn('SECRET', json.dumps(self.tasks.get(q['task_id'])))

    def test_clear_all_done(self):
        for s in ('a', 'b'):
            self.e.post({'session_id': s, 'line': '✅ ok'})
        self.e.post({'session_id': 'c', 'line': '⚠️ no'})
        ids = [x['id'] for x in self.e.feed() if x['state'] == 'done']
        self.assertEqual(self.e.clear(ids=ids), {'cleared': 2})
        self.assertEqual([x['state'] for x in self.e.feed()], ['stuck'])


class EndingsServerTest(LiveHub, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server.Handler.endings = Endings(self.server.Handler.store, self.server.Handler.questions)

    def tearDown(self):
        self.server.Handler.endings = None
        super().tearDown()

    def test_post_read_act(self):
        self.assertEqual(self.call('/api/endings', {'session_id': 'x', 'line': '✅ a'})[0], 401)
        code, e = self.call('/api/endings', {'session_id': 'x', 'line': '✅ Shipped', 'actions': ['Go ahead']},
                            {'Authorization': 'Bearer tok'})
        self.assertEqual(code, 201)
        code, out = self.call('/api/endings')
        self.assertEqual([x['line'] for x in out['endings']], ['Shipped'])
        code, out = self.call(f"/api/endings/{e['id']}/act", {'label': 'Go ahead'}, self.me())
        self.assertEqual((code, out['question']['answered_via']), (200, 'page'))
        self.assertEqual(self.call('/api/endings')[1]['endings'], [])


class HookEndingTest(unittest.TestCase):
    def test_first_line_next_step_and_button(self):
        self.assertEqual(cli.ending_of('✅ **Done**: [feed](http://x) live.\n\nmore\n\n👉 Next step: open it on your phone'),
                         ('✅ Done: feed live.', 'open it on your phone', ['Go ahead']))
        self.assertEqual(cli.ending_of('⚠️ Not yet\n**Next:** I fix it', ['Fix now', 'Later']),
                         ('⚠️ Not yet', 'I fix it', ['Fix now', 'Later']))
        self.assertEqual(cli.ending_of('Just chat.'), ('Just chat.', None, []))
        self.assertIsNone(cli.ending_of('  \n'))


if __name__ == '__main__':
    unittest.main()
