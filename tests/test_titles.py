import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hub'))
from store import Store  # noqa: E402
from tasks import Tasks  # noqa: E402
from titles import Titles, key_of  # noqa: E402


class TitlesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.dir.name, 'u.db'), 'Europe/Prague')
        Tasks(self.store)
        self.asked = []

        def model(prompt):
            self.asked.append(prompt)
            return {'1': 'Review gate PR', '2': '"Grade code reviews."'}
        self.titles = Titles(self.store, model=model, background=False)

    def tearDown(self):
        self.dir.cleanup()

    def test_model_titles_are_cached_and_shared_by_a_batch(self):
        a = {'id': 'Claude Code:a', 'project': 'review', 'first_prompt': 'Review this change before it ships.'}
        b = {'id': 'Claude Code:b', 'project': 'review', 'first_prompt': 'Review this change before it ships.'}
        c = {'id': 'Claude Code:c', 'project': 'grade', 'first_prompt': '[scheduled] You grade several reports'}
        self.titles.annotate([a, b, c])
        self.assertNotIn('title', a)
        self.assertEqual(len(self.titles.pending), 2)  # a and b are one batch
        self.titles.run_once()
        self.assertEqual(len(self.asked), 1)
        self.assertNotIn('[scheduled]', self.asked[0])
        a2, b2, c2 = dict(a), dict(b), dict(c)
        self.titles.annotate([a2, b2, c2])
        self.assertEqual((a2['title'], b2['title'], c2['title']), ('Review gate PR', 'Review gate PR', 'Grade code reviews'))
        self.assertEqual(self.titles.pending, {})

    def test_a_task_title_wins(self):
        with self.store.connect() as con:
            con.execute("INSERT INTO tasks (id, project, title, status, created_at, updated_at) VALUES (5, 'p', 'Usage page readable', 'doing', 0, 0)")
            con.execute("INSERT INTO task_sessions (task_id, session_id, linked_at) VALUES (5, 'Claude Code:x', 0)")
        s = {'id': 'Claude Code:x', 'project': 'p', 'first_prompt': 'hello'}
        self.titles.annotate([s])
        self.assertEqual((s['title'], s['title_from']), ('Usage page readable', 'task'))
        self.assertEqual(self.titles.pending, {})
        self.assertNotEqual(key_of('p', 'x'), key_of('q', 'x'))


    def test_a_name_from_the_log_wins_and_ai_never_replaces_custom(self):
        with self.store.connect() as con:
            con.execute("INSERT INTO tasks (id, project, title, status, created_at, updated_at) VALUES (6, 'p', 'Task name', 'doing', 0, 0)")
            con.execute("INSERT INTO task_sessions (task_id, session_id, linked_at) VALUES (6, 'Claude Code:y', 0)")
            con.execute("INSERT INTO task_sessions (task_id, session_id, linked_at) VALUES (6, 'Claude Code:z', 0)")
        self.titles.save('devbox', [{'session': 'Claude Code:y', 'title': 'Renamed by him', 'source': 'custom'},
                                   {'session': 'Claude Code:z', 'title': 'Claude own title', 'source': 'ai'},
                                   {'session': 'Claude Code:w', 'title': 'Only ai', 'source': 'ai'},
                                   {'session': 'Claude Code:v', 'title': 'x', 'source': 'bogus'}, 'junk'])
        self.titles.save('devbox', [{'session': 'Claude Code:y', 'title': 'Later ai title', 'source': 'ai'}])
        y = {'id': 'Claude Code:y', 'machine': 'devbox', 'first_prompt': 'hi'}
        z = {'id': 'Claude Code:z', 'machine': 'devbox', 'first_prompt': 'hi'}
        w = {'id': 'Claude Code:w', 'machine': 'devbox', 'first_prompt': 'hi'}
        other = {'id': 'Claude Code:w', 'machine': 'laptop', 'first_prompt': 'hi'}
        self.titles.annotate([y, z, w, other])
        self.assertEqual((y['title'], y['title_from']), ('Renamed by him', 'name'))
        self.assertEqual((z['title'], z['title_from']), ('Task name', 'task'))  # a task beats ai-title
        self.assertEqual((w['title'], w['title_from']), ('Only ai', 'name'))
        self.assertNotIn('title', other)  # names are per machine


if __name__ == '__main__':
    unittest.main()
