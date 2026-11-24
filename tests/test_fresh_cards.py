"""Fresh cards (task 1942): an agent reads what he already said before asking again, a card behind the work
leaves the deck, a card nobody answered in days goes, and a session outside git still names its project."""
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_tasks  # noqa: E402,F401 - points the settings at config.example.json, puts hub/ on the path
from store import Store  # noqa: E402
from tasks import Tasks  # noqa: E402
from questions import Questions, STALE_DAYS, SUPERSEDED_SEC  # noqa: E402
from cardrules import CardRules  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTX = '- 🎞️ the slides\n- ✅ Yes = posted, Still off = I fix it'
PLAIN = '- 🏷️ the app name\n- ✅ Keep = as it is, Rename = a new one'


def load_client():
    loader = importlib.machinery.SourceFileLoader('tasks_client_fresh', os.path.join(ROOT, 'client', 'tasks'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FreshCardsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, 'u.db'))
        self.tasks = Tasks(self.store)
        self.qs = Questions(self.store, self.tasks)
        rules = os.path.join(self.tmp.name, 'card-rules.md')
        with open(rules, 'w', encoding='utf-8') as f:
            f.write('## Rules\n\n- The question names the thing it is about.\n')
        self.qs.cardrules = CardRules(self.store, self.qs.get, lambda p: {'covered': None, 'rule': ''}, rules,
                                      background=False)
        self.task = self.tasks.create({'title': 'TikTok: 3 slideshows posted', 'project': 'budget'})['id']

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, question, session='Claude Code:a', **more):
        return self.qs.create({'question': question, 'context': CTX, 'options': ['Yes, post them', 'Still off'],
                               'task_id': self.task, 'session_id': session, 'strict': True, **more})

    def plain(self, question, **more):
        """A card the approval gate never holds, in shadow or enforce mode (test_questions switches it on)."""
        return self.ask(question, kind='naming', options=['Keep the name', 'Rename it'], context=PLAIN, **more)

    def test_an_ask_after_his_own_words_is_refused_once_with_them(self):
        q = self.ask('Can all 3 shows go up?')
        self.qs.answer(q['id'], {'answer': 'the font on top is super hard to read'})
        with self.assertRaises(ValueError) as e:
            self.ask('Bigger font now: can all 3 shows go up?', session='Claude Code:b')
        self.assertIn('the font on top is super hard to read', str(e.exception))
        self.assertIn('fresh reviewer', str(e.exception))
        again = self.ask('Bigger font now: can all 3 shows go up?', session='Claude Code:b')  # read: it passes
        self.assertEqual(again['status'], 'open')

    def test_a_new_answer_of_his_is_shown_again(self):
        q = self.ask('Can all 3 shows go up?')
        self.qs.answer(q['id'], {'answer': 'the wifi looks off'})
        self.assertRaises(ValueError, self.ask, 'Wifi fixed: can they go up?')
        q2 = self.ask('Wifi fixed: can they go up?')
        self.qs.answer(q2['id'], {'answer': 'the CTA copy is mediocre'})
        with self.assertRaises(ValueError) as e:
            self.ask('New CTA: can they go up?')
        self.assertIn('the CTA copy is mediocre', str(e.exception))
        self.assertNotIn('the wifi looks off', str(e.exception))  # already shown to this session

    def test_his_pick_of_the_agents_default_is_no_reason_to_refuse(self):
        q = self.ask('Can all 3 shows go up?')
        self.qs.answer(q['id'], {'answer': 'Yes, post them'})
        self.assertEqual(self.ask('Post show 2 too?')['status'], 'open')

    def test_cards_the_hub_makes_itself_are_never_refused(self):
        q = self.ask('Can all 3 shows go up?')
        self.qs.answer(q['id'], {'answer': 'the font is hard to read'})
        card = self.qs.create({'question': 'Fixed the font: post?', 'context': CTX, 'options': ['Post'],
                               'task_id': self.task, 'session_id': 'Claude Code:c', 'kind': 'ending'})
        self.assertEqual(card['status'], 'open')

    def test_an_answer_takes_the_same_sessions_older_card_off_the_deck(self):
        # kind naming: a card the approval gate never holds, in shadow or enforce mode (another test may switch it)
        now = time.time()
        old = self.plain('Plug the pad in with a cable?', created_at=now - SUPERSEDED_SEC - 60)
        todo = self.qs.create({'question': 'Set up the spare phone', 'context': CTX, 'options': ['Done'],
                               'steps': [{'text': 'Open Settings'}], 'task_id': self.task, 'session_id': 'Claude Code:a',
                               'created_at': now - SUPERSEDED_SEC - 60})
        other = self.ask('Which voice?', session='Claude Code:z', created_at=now - SUPERSEDED_SEC - 60)
        held = self.ask('Send the 5 mails?', blocking=True, created_at=now - SUPERSEDED_SEC - 60)
        twin = self.plain('And the speed OK?', created_at=now - 5)  # asked in the same go as the newer one
        new = self.plain('Hold PS 10 s, then try again?')
        self.qs.answer(new['id'], {'answer': 'Keep the name'})
        self.assertEqual(self.qs.get(old['id'])['status'], 'withdrawn')
        self.assertEqual(self.qs.get(todo['id'])['status'], 'open')  # a to-do he ticks stays
        self.assertEqual(self.qs.get(other['id'])['status'], 'open')  # another session's card stays
        self.assertEqual(self.qs.get(held['id'])['status'], 'open')  # an agent holds work on it
        self.assertEqual(self.qs.get(twin['id'])['status'], 'open')

    def test_a_card_unanswered_for_days_leaves_the_deck_with_a_note(self):
        long_ago = time.time() - STALE_DAYS * 86400 - 60
        old = self.plain('Name the app Lull?', created_at=long_ago)
        held = self.plain('Send the mail?', blocking=True, created_at=long_ago)  # an agent holds work on it
        todo = self.qs.create({'question': 'Set up the spare phone', 'context': CTX, 'options': ['Done'],
                               'steps': [{'text': 'Open Settings'}], 'task_id': self.task, 'created_at': long_ago})
        fresh = self.plain('Which subject line?')
        self.qs.expired_at = 0
        self.assertEqual(sorted(q['id'] for q in self.qs.list('open')), sorted([held['id'], todo['id'], fresh['id']]))
        self.assertEqual(self.qs.get(old['id'])['status'], 'withdrawn')
        notes = [c['text'] for c in self.tasks.get(self.task)['comments']]
        self.assertTrue(any('unanswered' in n for n in notes))


    def test_a_decided_holder_task_closes_done_not_dropped(self):
        t = self.qs.file_task('Which plan?', PLAIN, 'budget', 'Claude Code:h')
        now = time.time()
        self.qs.create({'question': 'Monthly or yearly?', 'context': PLAIN, 'options': ['Monthly', 'Yearly'], 'kind': 'naming',
                        'task_id': t, 'session_id': 'Claude Code:h', 'created_at': now - SUPERSEDED_SEC - 60})
        new = self.qs.create({'question': 'Which plan name?', 'context': PLAIN, 'options': ['Pro', 'Plus'], 'kind': 'naming',
                              'task_id': t, 'session_id': 'Claude Code:h'})
        self.qs.answer(new['id'], {'answer': 'Plus'}, via='chat')  # taken at once by its own chat
        self.assertEqual(self.tasks.get(t)['status'], 'done')

    def test_a_card_turns_blocking_in_place_never_lowered(self):
        q = self.plain('Name the app Lull?')
        self.assertFalse(q['blocking'])
        q = self.qs.add_see(q['id'], {'blocking': True})
        self.assertTrue(q['blocking'])
        self.assertTrue(self.qs.add_see(q['id'], {'blocking': False})['blocking'])
        self.assertEqual(len(self.qs.list('open')), 1)  # one card, not a withdrawn one and a second

class FolderProjectTest(unittest.TestCase):
    def test_a_folder_outside_git_is_named_like_the_usage_collector_names_it(self):
        cli = load_client()
        self.assertEqual(cli.folder_project('C:\\Users\\x\\Documents\\Unreal Projects\\Game\\Source'), 'Game')
        self.assertEqual(cli.folder_project('/home/x/Projects/site'), 'site')
        self.assertEqual(cli.folder_project('/home/x/Projects/parked/old'), 'old')
        self.assertIsNone(cli.folder_project('/home/x/Projects'))
        self.assertIsNone(cli.folder_project('/home/x/Projects/.tasks-client'))
        self.assertIsNone(cli.folder_project('/home/x/notes'))


if __name__ == '__main__':
    unittest.main()
