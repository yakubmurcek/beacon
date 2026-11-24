"""A running task's own steps, the step its agent marked, and the learned time-left guess (hub/progress.py)."""
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hub'))
from progress import Progress, coarse, current_index, label_of, steps_of  # noqa: E402
from store import Store  # noqa: E402
from studio import Studio  # noqa: E402
from tasks import Tasks  # noqa: E402

BODY = '''## Goal
Ship the 1.1 build to testers.
## Done-test
- testers have it
## Steps
1. 🤖 Bump the version and build (Xcode archive)
2. 🤖 Test on the iPhone: login, purchase
3. 🤖 Upload to TestFlight
4. 👤 Owner: try it on his phone, say ship or fix
## Context
1. not a step
'''


def plan(n_agent):
    return '## Goal\nx\n## Done-test\n- x\n## Steps\n' + ''.join(f'{i}. 🤖 Step {i}\n' for i in range(1, n_agent + 1))


class StepsTest(unittest.TestCase):
    def test_the_plan_is_the_tasks_own_numbered_steps(self):
        steps = steps_of(BODY)
        self.assertEqual([s['label'] for s in steps], ['Bump the version and build', 'Test on the iPhone',
                                                       'Upload to TestFlight', 'Try it on his phone, say ship or fix'])
        self.assertEqual([s['owner'] for s in steps], [False, False, False, True])
        self.assertEqual(steps_of('## Goal\nno plan written'), [])  # nothing made up

    def test_the_current_step_is_the_marked_one_else_the_first_not_ticked(self):
        steps = steps_of(BODY.replace('1. 🤖', '1. ✅ 🤖'))
        self.assertEqual(current_index(steps, 3), 2)
        self.assertEqual(current_index(steps, None), 1)
        self.assertEqual(current_index(steps, 99), 1)  # a stale mark past the end never breaks the card

    def test_labels_and_times_read_plainly(self):
        self.assertEqual(label_of('🤖 **Draw it** on the card; test, ship'), 'Draw it on the card')
        self.assertEqual([coarse(m) for m in (5, 30, 60, 120, 200, 600)],
                         ['~15 min', '~30 min', '~1 h', '~2 h', 'a few hours', 'most of a day'])


class GuessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, 'u.db'))
        self.tasks = Tasks(self.store)
        self.progress = Progress(self.store, background=False)
        with self.store.connect() as con:
            con.execute("INSERT INTO machines (name, last_seen) VALUES ('devbox', 0)")

    def tearDown(self):
        self.tmp.cleanup()

    def closed(self, project, agent_steps, minutes):
        """A closed task whose one session worked `minutes` in one burst."""
        t = self.tasks.create({'title': 'done job', 'project': project, 'body': plan(agent_steps)})
        sid = f'Claude Code:s{t["id"]}'
        self.tasks.link(t['id'], sid)
        start = time.time() - 3600 * 10
        with self.store.connect() as con:
            for k in range(0, minutes + 1, 5):
                con.execute("INSERT INTO events (id, machine, agent, session, ts, kind) VALUES (?, 'devbox', 'Claude Code', ?, ?, 'prompt')",
                            (f'{sid}-{k}', sid, start + k * 60))
        self.tasks.update(t['id'], {'status': 'done'})
        return t['id']

    def test_time_left_learns_from_closed_tasks_and_their_guesses(self):
        for _ in range(5):
            self.closed('habit-app', 2, 60)  # ~30 helper minutes per agent step
        self.progress.refresh()
        t = self.tasks.create({'title': 'TestFlight build', 'project': 'habit-app', 'body': BODY})
        t = self.tasks.update(t['id'], {'status': 'doing', 'step': 2})
        job = self.progress.of(t)
        self.assertEqual((job['at'], job['steps'][job['at']], job['then']), (1, 'Test on the iPhone', 'Upload to TestFlight'))
        self.assertEqual((job['left'], job['first_guess']), ('~1 h', False))  # 2 agent steps ahead x ~30 min
        self.assertEqual(self.progress.of(self.tasks.update(t['id'], {'step': 4}))['left'], 'waiting for you')

        # it closes after 120 real minutes against a ~92 min guess: graded, and the next guesses lean longer
        with self.store.connect() as con:
            guess = con.execute('SELECT guess_min FROM task_guesses WHERE task_id = ?', (t['id'],)).fetchone()[0]
            for k in range(0, 121, 5):
                con.execute("INSERT INTO events (id, machine, agent, session, ts, kind) VALUES (?, 'devbox', 'Claude Code', 'Claude Code:tf', ?, 'prompt')",
                            (f'tf-{k}', time.time() - 7200 + k * 60))
        self.tasks.link(t['id'], 'Claude Code:tf')
        self.tasks.update(t['id'], {'status': 'done'})
        self.progress.refresh()
        acc = self.progress.accuracy()
        self.assertEqual(acc['jobs'], 1)
        self.assertAlmostEqual(acc['off_pct'], round(100 * abs(121 - guess) / 121), delta=1)

    def test_a_project_with_few_samples_says_first_guess(self):
        for _ in range(5):
            self.closed('snow-game', 1, 10)
        self.progress.refresh()
        t = self.tasks.create({'title': 'new thing', 'project': 'habit-app', 'body': BODY})
        self.assertTrue(self.progress.of(self.tasks.update(t['id'], {'status': 'doing'}))['first_guess'])

    def test_a_task_without_steps_shows_no_progress(self):
        t = self.tasks.create({'title': 'no plan', 'project': 'habit-app', 'body': 'just do it'})
        self.assertIsNone(self.progress.of(t))
        self.assertEqual(self.tasks.get(t['id'])['plan'], [])

    def test_the_step_field_takes_only_a_step_number(self):
        t = self.tasks.create({'title': 'x', 'project': 'habit-app', 'body': BODY})
        for bad in (0, -1, 'two', True):
            with self.assertRaises(ValueError):
                self.tasks.update(t['id'], {'step': bad})
        self.assertEqual(self.tasks.update(t['id'], {'step': 3})['step'], 3)

    def test_the_team_card_shows_the_running_jobs_steps(self):
        """The 2026-11-23 miss: a TestFlight build ran while the card spoke of TikTok. Its steps now ride on the card."""
        tf = self.tasks.create({'title': 'Sleep app 1.1 TestFlight build', 'project': 'habit-app', 'body': BODY})
        self.tasks.update(tf['id'], {'status': 'doing', 'step': 3})
        self.tasks.link(tf['id'], 'Claude Code:a')
        tk = self.tasks.create({'title': 'TikTok launch', 'project': 'habit-app', 'body': 'no plan'})
        self.tasks.update(tk['id'], {'status': 'doing'})
        self.tasks.link(tk['id'], 'Claude Code:b')
        live = [{'id': sid, 'project': 'habit-app', 'state': 'working', 'idle_s': 5, 'doing': 'x', 'last': last}
                for sid, last in (('Claude Code:a', 1), ('Claude Code:b', 2))]
        store = self.store
        store.live_now = lambda: {'machines': [{'machine': 'devbox', 'sessions': live}]}
        st = Studio(store, model=lambda p: {'1': {'headline': 'Launching the sleep app on TikTok', 'kind': 'players'}},
                    background=False, progress=self.progress)
        card = st.run_once()['projects'][0]
        self.assertEqual([(j['title'], j['steps'][j['at']]) for j in card['jobs']],
                         [('Sleep app 1.1 TestFlight build', 'Upload to TestFlight')])


if __name__ == '__main__':
    unittest.main()
