"""His rules about cards (hub/cardrules.py): his 👎 teaches once, a repeat is logged, held-back cards are logged."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_tasks  # noqa: E402,F401 - points the settings at config.example.json, puts hub/ on the path
from store import Store  # noqa: E402
from tasks import Tasks  # noqa: E402
from questions import Questions, put_off  # noqa: E402
from endings import Endings  # noqa: E402
from cardrules import CardRules, read_rules_file  # noqa: E402

RULES = """# Rules every card is checked against
Some prose, not a rule.

## Rules

- The question names the thing it is about.
- No card asks him to merge a verified PR.

## Evidence

- 2026-11-22: "a merge card? I'm not reading the code"
"""


class CardRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, 'u.db'))
        self.qs = Questions(self.store, Tasks(self.store))
        self.file = os.path.join(self.tmp.name, 'card-rules.md')
        with open(self.file, 'w', encoding='utf-8') as f:
            f.write(RULES)
        self.asked = []
        self.reply = {'covered': None, 'rule': 'A card about a page links to that page.'}
        self.cr = CardRules(self.store, self.qs.get, self.model, self.file, background=False)
        self.qs.cardrules = self.cr

    def model(self, prompt):
        self.asked.append(prompt)
        return self.reply

    def tearDown(self):
        self.tmp.cleanup()

    def card(self, question='Ship the new header?'):
        return self.qs.create({'question': question, 'context': '- the header\n- each answer', 'options': ['Ship', 'Keep']})

    def test_only_the_rules_section_is_read(self):
        self.assertEqual(read_rules_file(self.file), ['The question names the thing it is about.',
                                                      'No card asks him to merge a verified PR.'])
        self.assertEqual([r['id'] for r in self.cr.rules()], ['f1', 'f2'])

    def test_a_flag_with_words_teaches_one_rule_once(self):
        q = self.card()
        self.qs.flag(q['id'])  # the bare tap: read at once here (no wait in tests), from the card alone
        self.qs.flag(q['id'], 'no link to the header')  # his words after: the card already taught
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.cr.rules()[-1]['text'], 'A card about a page links to that page.')
        self.assertEqual([c['verdict'] for c in self.cr.checks()], ['learned'])

    def test_a_bare_tap_waits_for_his_words(self):
        q = self.card()
        self.cr.flagged = lambda q, note=None: None  # the tap's timer has not fired yet
        self.qs.flag(q['id'])
        self.qs.flag(q['id'], 'no link to the header')
        self.assertIsNone(self.cr.learn(q['id'], None, bare=True))  # his words came: the timer leaves it to them
        self.assertIsNotNone(self.cr.learn(q['id'], 'no link to the header'))
        self.assertIn('no link to the header', self.asked[0])
        self.assertEqual(len(self.asked), 1)

    def test_a_repeat_names_the_rule_it_broke(self):
        self.reply = {'covered': 2, 'rule': ''}
        q = self.card('Merge the PR?')
        self.qs.flag(q['id'], 'why ask me to merge')
        [c] = self.cr.checks()
        self.assertEqual((c['verdict'], c['rules']), ('repeat', ['f2']))
        self.assertEqual(len(self.cr.rules()), 2)  # nothing new learned

    def test_taking_the_flag_back_drops_its_rule(self):
        q = self.card()
        self.qs.flag(q['id'], 'no link')
        self.assertEqual(len(self.cr.rules()), 3)
        self.qs.flag(q['id'])  # second tap, no words: taken back
        self.assertEqual(len(self.cr.rules()), 2)
        self.assertEqual(self.cr.checks()[0]['verdict'], 'taken-back')

    def test_the_cap_and_no_model_only_log(self):
        cr = CardRules(self.store, self.qs.get, None, self.file, background=False)
        self.qs.cardrules = cr
        q = self.card()
        self.qs.flag(q['id'], 'unclear')
        self.assertEqual(cr.checks()[0]['verdict'], 'skipped')
        self.assertEqual(len(cr.rules()), 2)

    def test_a_refused_ask_is_logged_as_held_back(self):
        with self.assertRaises(ValueError):
            self.qs.create({'question': 'Which one?', 'strict': True, 'session_id': 's1'})
        [c] = self.cr.checks()
        self.assertEqual((c['kind'], c['verdict'], c['question']), ('ask', 'refused', 'Which one?'))
        self.assertIn('no context', c['why'])

    def test_not_yet_buttons_are_dropped(self):
        for label in ('Not yet', "Haven't tried yet", 'Zatím ne', 'Not now'):
            self.assertTrue(put_off(label), label)
        self.assertFalse(put_off('Ship it'))

    def test_an_ending_that_asks_nothing_is_no_card_and_is_logged_once(self):
        e = Endings(self.store, self.qs, cardrules=self.cr)
        reply = '❓ Yes, I agree\n- see https://x.y/z?a=1\n👉 Next: reply udělej'
        e.post({'session_id': 'abc', 'line': '❓ Yes, I agree', 'reply': reply})
        e.post({'session_id': 'abc', 'line': '❓ Yes, I agree', 'reply': reply})  # a re-stop
        self.assertEqual(self.qs.list('open'), [])
        self.assertEqual([c['verdict'] for c in self.cr.checks()], ['dropped'])
        e.post({'session_id': 'abc', 'line': '❓ Ship it?', 'reply': '❓ Ship it?'})
        self.assertEqual(len(self.qs.list('open')), 1)

    def test_the_reword_writes_under_his_rules_and_drops_a_status(self):
        prompts = []
        e = Endings(self.store, self.qs, model=lambda p: prompts.append(p) or {'status': True}, cardrules=self.cr)
        q = e.questions.create({'question': 'Done?', 'context': 'x', 'kind': 'ending'})
        e.reword(q['id'], {'reply': '❓ Done?'}, 'A session')
        self.assertIn('No card asks him to merge a verified PR.', prompts[0])
        self.assertEqual(self.qs.get(q['id'])['status'], 'withdrawn')
        self.assertEqual(self.cr.checks()[0]['verdict'], 'dropped')


if __name__ == '__main__':
    unittest.main()
