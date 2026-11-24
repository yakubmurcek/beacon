"""Standing rules learned from his repeated answers (hub/rules.py, task 1764). The model is a fake:
these tests pin the code around it (when to ask it, the risk guard, the log, undo)."""
import os
import time
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tasks import ROOT  # noqa: E402,F401
from store import Store  # noqa: E402
from tasks import Tasks  # noqa: E402
from questions import Questions  # noqa: E402
from rules import Rules  # noqa: E402


class FakeModel:
    def __init__(self, same=True, match=True):
        self.same, self.match, self.calls = same, match, []

    def __call__(self, prompt):
        self.calls.append(prompt)
        if 'standing rules a person approved' in prompt:
            rid = int(prompt.split('Rules:\n#', 1)[1].split(':', 1)[0])
            return {'match': self.match, 'rule_id': rid, 'answer': 'Take the suggestion', 'why': f'rule #{rid}: same setup choice'}
        return {'same': self.same, 'summary': 'Internal setup choices: take the agent\'s suggestion',
                'answer': 'Take the suggestion', 'why': 'he took it every time'}


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store = Store(os.path.join(self.tmp.name, 'u.db'))
        self.qs = Questions(store, Tasks(store))
        self.model = FakeModel()
        self.rules = Rules(store, self.qs, model=self.model, background=False)

    def tearDown(self):
        self.tmp.cleanup()

    def ask_answer(self, question, answer='Take the suggestion', via='page', kind='setup', **f):
        q = self.qs.create({'question': question, 'options': ['Take the suggestion', 'Other'], 'kind': kind,
                            'project': 'linux-setup', **f})
        return self.qs.answer(q['id'], {'answer': answer}, via=via)

    def proposal(self):
        return [q for q in self.qs.list('open') if q['kind'] == 'rule']

    def test_two_same_answers_propose_a_rule_with_the_evidence(self):
        self.ask_answer('Which shell config tool for the box setup script?')
        self.assertEqual(self.proposal(), [])
        self.ask_answer('Which config tool for the gamer setup script?')
        [p] = self.proposal()
        self.assertIn('Internal setup choices', p['question'])
        self.assertIn('2×', p['context'])
        self.assertIn('Which shell config tool', p['context'])
        self.assertEqual(p['default_answer'], 'Ne, ptej se dál')  # nothing changes until he says yes
        [r] = self.rules.list()
        self.assertEqual((r['status'], len(r['evidence'])), ('proposed', 2))

    def test_no_proposal_without_his_yes_and_none_twice(self):
        self.ask_answer('Which shell config tool for the box setup script?')
        self.ask_answer('Which config tool for the gamer setup script?')
        self.ask_answer('Which config tool for the devbox setup script?')
        self.assertEqual(len(self.rules.list()), 1)

    def test_risky_repeats_are_never_proposed(self):
        self.ask_answer('Send the weekly mail to the client setup list?')
        self.ask_answer('Send the monthly mail to the client setup list?')
        self.assertEqual(self.rules.list(), [])
        self.assertEqual(self.model.calls, [])  # the code said no before any model call

    def test_his_yes_activates_and_the_rule_answers_the_next_one_with_why(self):
        self.ask_answer('Which shell config tool for the box setup script?')
        self.ask_answer('Which config tool for the gamer setup script?')
        [p] = self.proposal()
        self.qs.answer(p['id'], {'answer': 'Ano, ať to platí'}, via='page')
        self.assertEqual(self.rules.list()[0]['status'], 'active')
        q = self.qs.create({'question': 'Which config tool for the pi setup script?', 'options': ['Take the suggestion', 'Other'],
                            'kind': 'setup', 'project': 'linux-setup'})
        q = self.qs.get(q['id'])
        self.assertEqual((q['status'], q['answered_via'], q['answer']), ('answered', 'rule', 'Take the suggestion'))
        self.assertIn('Pravidlo #', q['answer_note'])
        [r] = self.rules.list()
        self.assertEqual((r['fired'], r['uses'][0]['question_id']), (1, q['id']))
        # undo: the question opens again for him
        out = self.rules.undo_use(r['uses'][0]['id'])
        self.assertTrue(out['reopened'])
        self.assertEqual(self.qs.get(q['id'])['status'], 'open')

    def test_his_no_declines(self):
        self.ask_answer('Which shell config tool for the box setup script?')
        self.ask_answer('Which config tool for the gamer setup script?')
        [p] = self.proposal()
        self.qs.answer(p['id'], {'answer': 'Ne, ptej se dál'}, via='page')
        self.assertEqual(self.rules.list()[0]['status'], 'declined')

    def test_an_inferred_rule_never_answers_a_risky_question(self):
        self.ask_answer('Which shell config tool for the box setup script?')
        self.ask_answer('Which config tool for the gamer setup script?')
        self.qs.answer(self.proposal()[0]['id'], {'answer': 'Ano'}, via='page')
        n = len(self.model.calls)
        q = self.qs.create({'question': 'Which config tool, and deploy it to production?', 'options': ['Take the suggestion'],
                            'kind': 'setup', 'project': 'linux-setup'})
        self.assertEqual(self.qs.get(q['id'])['status'], 'open')
        self.assertEqual(len(self.model.calls), n)  # never even asked the model

    def test_a_rule_in_his_own_words_may_answer_a_risky_one(self):
        q = self.qs.create({'question': 'Deploy the hub to production after tests pass?', 'options': ['Yes'], 'kind': 'setup',
                            'project': 'linux-setup'})
        self.qs.answer(q['id'], {'answer': 'Take the suggestion', 'standing': True}, via='cli')
        [r] = self.rules.list()
        self.assertEqual((r['source'], r['status']), ('his-words', 'active'))
        q = self.qs.create({'question': 'Deploy the hub to production now that tests pass?', 'options': ['Take the suggestion'],
                            'kind': 'setup', 'project': 'linux-setup'})
        self.assertEqual(self.qs.get(q['id'])['answered_via'], 'rule')

    def test_unsure_means_he_is_asked(self):
        self.model.match = False
        self.ask_answer('Which shell config tool for the box setup script?')
        self.ask_answer('Which config tool for the gamer setup script?')
        self.qs.answer(self.proposal()[0]['id'], {'answer': 'Ano'}, via='page')
        q = self.qs.create({'question': 'Which config tool for the pi setup script?', 'options': ['x'], 'kind': 'setup',
                            'project': 'linux-setup'})
        self.assertEqual(self.qs.get(q['id'])['status'], 'open')

    def test_a_rule_off_answers_nothing_and_a_proposal_cant_be_switched_on_by_hand(self):
        self.ask_answer('Which shell config tool for the box setup script?')
        self.ask_answer('Which config tool for the gamer setup script?')
        r = self.rules.list()[0]
        with self.assertRaises(ValueError):
            self.rules.update(r['id'], {'status': 'active'})
        self.qs.answer(self.proposal()[0]['id'], {'answer': 'Ano'}, via='page')
        self.rules.update(r['id'], {'status': 'off'})
        q = self.qs.create({'question': 'Which config tool for the pi setup script?', 'options': ['x'], 'kind': 'setup',
                            'project': 'linux-setup'})
        self.assertEqual(self.qs.get(q['id'])['status'], 'open')

    def test_a_rule_answer_never_approves_a_risky_action(self):
        self.assertNotIn('rule', __import__('questions').APPROVAL_VIA)


    # ------------------------------------------------------------ the Learned view (task 1824)

    def rule_answers_one(self):
        self.ask_answer('Which shell config tool for the box setup script?')
        self.ask_answer('Which config tool for the gamer setup script?')
        [p] = self.proposal()
        self.qs.answer(p['id'], {'answer': 'Ano, ať to platí'}, via='page')
        q = self.qs.create({'question': 'Which config tool for the pi setup script?', 'options': ['Take the suggestion', 'Other'],
                            'kind': 'setup', 'project': 'linux-setup'})
        return self.qs.get(q['id'])

    def test_history_names_the_rule_that_answered_and_a_taken_back_use_names_none(self):
        q = self.rule_answers_one()
        [r] = self.rules.list()
        rows = self.rules.annotate(self.qs.list('answered', limit=50))
        by = {x['id']: x.get('rule') for x in rows}
        self.assertEqual(by[q['id']], {'id': r['id'], 'summary': r['summary']})
        self.assertEqual([x for x in by.values() if x], [by[q['id']]])  # his own answers carry no rule
        self.assertEqual((r['answered'], r['taken_back']), (1, 0))
        self.qs.applied(q['id'])  # the agent took the answer: undo can't reopen it, but the use is marked
        self.rules.undo_use(r['uses'][0]['id'])
        self.assertNotIn('rule', self.rules.annotate([self.qs.get(q['id'])])[0])
        [r] = self.rules.list()
        self.assertEqual((r['answered'], r['taken_back'], r['fired']), (0, 1, 1))

    def test_weekly_counts_him_and_the_rules_per_monday_with_empty_weeks(self):
        q = self.rule_answers_one()
        week = 7 * 86400
        with self.qs.store.connect() as con:  # his two answers three weeks back, the rule's this week
            con.execute('UPDATE questions SET answered_at = answered_at - ? WHERE answered_via = ? AND kind = ?', (3 * week, 'page', 'setup'))
        weeks = self.rules.weekly()
        self.assertEqual(len(weeks), 4)
        self.assertEqual([(w['him'], w['auto']) for w in weeks], [(2, 0), (0, 0), (0, 0), (0, 1)])  # the yes on the proposal is not counted
        self.assertTrue(all(__import__('datetime').date.fromisoformat(w['week']).weekday() == 0 for w in weeks))
        self.assertEqual(q['answered_via'], 'rule')
        self.qs.applied(q['id'])  # taken back after the agent had it: the question stays answered, now counted as his
        self.rules.undo_use(self.rules.list()[0]['uses'][0]['id'])
        self.assertEqual([(w['him'], w['auto']) for w in self.rules.weekly()][-1], (1, 0))

    def test_no_rule_is_learned_from_rule_proposals(self):
        for text in ('Pravidlo: číst kód první?', 'Pravidlo: číst kód vždy první?'):
            q = self.qs.create({'question': text, 'options': ['Ano', 'Ne'], 'kind': 'rule', 'project': 'personal'})
            self.qs.answer(q['id'], {'answer': 'Ano'}, via='page')
        self.assertEqual(self.rules.list(), [])
        self.assertEqual(self.model.calls, [])


    # ------------------------------------------------------------ the evening sweep (task 1829)

    def answered_quietly(self, question, project, kind='next-step', answer='Yes, do it'):
        """An answer the per-answer check doesn't see (as before the hub learned): straight into the table."""
        q = self.qs.create({'question': question, 'options': ['Yes, do it', 'No'], 'default': 'Yes, do it', 'kind': kind,
                            'project': project})
        with self.qs.store.connect() as con:
            con.execute('UPDATE questions SET status = ?, answer = ?, answered_via = ?, answered_at = ? WHERE id = ?',
                        ('answered', answer, 'chat', time.time() - 3600 + q['id'], q['id']))
        return q['id']

    def test_sweep_proposes_across_projects_and_a_yes_turns_it_on_without_a_dispatcher_run(self):
        ids = [self.answered_quietly('Fix the queue and rerun the build?', 'personal'),
               self.answered_quietly('Let the sessions talk to each other?', 'personal'),
               self.answered_quietly('Log first, then switch the watcher on?', 'job-search')]
        self.assertIsNone(self.rules.last_sweep())
        out = self.rules.sweep()
        self.assertEqual(out['model_calls'], 1)
        s = self.rules.last_sweep()  # Learned's nightly line: what it read and what it proposed
        self.assertEqual((s['answers'], s['model_calls'], len(s['proposed'])), (3, 1, 1))
        [r] = self.rules.list()
        self.assertEqual((r['project'], r['kind'], r['status'], r['evidence'], r['takes_default']), (None, 'next-step', 'proposed', ids, 1))
        [p] = self.proposal()
        self.assertIn('napříč projekty', p['context'])
        self.assertEqual(self.rules.sweep()['model_calls'], 0)  # held by that rule: never proposed twice
        self.qs.answer(p['id'], {'answer': 'Ano, ať to platí'}, via='page')
        self.assertIsNotNone(self.qs.get(p['id'])['applied_at'])  # decided here: nothing left for answer-dispatcher
        self.assertEqual(self.rules.list()[0]['status'], 'active')
        q = self.qs.create({'question': 'Rerun the nightly check now?', 'options': ['Yes, do it', 'No'], 'default': 'Yes, do it',
                            'kind': 'next-step', 'project': 'clean-the-snow'})
        self.assertEqual(self.qs.get(q['id'])['answered_via'], 'rule')  # a rule for every project answers anywhere
        self.assertIsNotNone(self.rules.list()[0]['decided_at'])  # Learned: when he turned it on
        [d] = self.rules.decided()  # Learned: decided for you, with the rule and the answer
        self.assertEqual((d['question_id'], d['rule_id'], d['answer'], d['undone_at']), (q['id'], r['id'], 'Yes, do it', None))
        self.rules.update(r['id'], {'status': 'off'})
        q = self.qs.create({'question': 'Rerun the morning check now?', 'options': ['Yes, do it', 'No'], 'default': 'Yes, do it',
                            'kind': 'next-step', 'project': 'clean-the-snow'})
        self.assertEqual(self.qs.get(q['id'])['status'], 'open')  # switched off: it comes to him

    def test_sweep_prefers_one_project_skips_risky_and_mixed_and_caps_model_calls(self):
        for i in range(3):
            self.answered_quietly(f'Rename the internal script number {i}?', 'linux-setup', kind='naming')
        for i in range(3):
            self.answered_quietly(f'Send the client invoice mail {i}?', 'excel-s-ai', kind='mail')
        for i, a in enumerate(['Yes, do it', 'No', 'Yes, do it']):
            self.answered_quietly(f'Merge the cleanup {i}?', 'linux-setup', kind='cleanup', answer=a)
        out = self.rules.sweep()
        self.assertEqual([(p['project'], p['kind']) for p in out['proposed']], [('linux-setup', 'naming')])
        self.assertEqual((out['repeated'], out['risky']), (2, 1))  # naming and the invoice mails; the cleanup answers differ
        self.assertNotIn('invoice', ''.join(self.model.calls))  # the risk guard before any model call
        for k in range(4):
            for i in range(3):
                self.answered_quietly(f'Pick a word for thing {k} {i}?', f'proj-{k}', kind=f'words-{k}')
        self.assertEqual(self.rules.sweep(calls=2)['model_calls'], 2)


if __name__ == '__main__':
    unittest.main()
