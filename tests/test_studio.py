import os

os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hub'))
from studio import Studio  # noqa: E402


class FakeStore:
    def __init__(self, sessions):
        self.sessions = sessions

    def live_now(self):
        return {'machines': [{'machine': 'devbox', 'sessions': self.sessions}]}

    def connect(self):
        raise sqlite3.OperationalError('no such table: tasks')  # what SQLite says on a hub without them


def s(id, project, state='working', idle=5, doing='Editing x'):
    return {'id': id, 'project': project, 'state': state, 'idle_s': idle, 'doing': doing, 'title': 'job ' + id, 'last': 1}


class StudioTest(unittest.TestCase):
    def studio(self, sessions, reply):
        self.asked = []
        def model(prompt):
            self.asked.append(prompt)
            return reply(prompt)
        return Studio(FakeStore(sessions), model=model, background=False)

    def test_sessions_fold_into_projects_and_each_tool_is_its_own_card(self):
        st = self.studio([s('a', 'snow-game'), s('b', 'snow-game', state='waiting', idle=60), s('c', 'ai-brain'),
                          s('d', 'beacon')],
                         lambda p: {'1': {'headline': 'Making TikToks to bring new players', 'aim': 'More players', 'kind': 'content'},
                                    '2': {'headline': 'Making the notes easier to find', 'kind': 'numbers'},
                                    '3': {'headline': 'Making the team tools faster', 'kind': 'hq'}})
        view = st.run_once()
        self.assertEqual(sorted(p['key'] for p in view['projects']), ['ai-brain', 'beacon', 'snow-game'])
        self.assertEqual(view['projects'][-1]['kind'], 'hq')  # a tool is always kind hq, whatever Haiku said
        self.assertEqual(view['projects'][-1]['what'], 'the tools the AI team works with')
        game = view['projects'][0]
        self.assertEqual((game['headline'], game['aim'], game['kind'], game['working'], game['helpers'], game['busy']),
                         ('Making TikToks to bring new players', 'More players', 'content', True, 2, 1))
        self.assertIsNone(view['projects'][1]['aim'])  # no aim given: none shown, never made up
        self.assertNotIn('Snow Game', [r['name'] for r in view['resting']])

    def test_a_project_only_waiting_on_him_says_so_and_old_waits_rest(self):
        st = self.studio([s('a', 'habit-app', state='waiting', idle=600), s('b', 'weddings', state='waiting', idle=7 * 3600)],
                         lambda p: {'1': {'headline': 'Showing what the test players found', 'kind': 'numbers'}})
        view = st.run_once()
        self.assertEqual([(p['key'], p['waiting']) for p in view['projects']], [('habit-app', True)])
        self.assertIn('Wedding sites', [r['name'] for r in view['resting']])

    def test_no_caption_still_shows_the_project_and_nothing_new_asks_nothing(self):
        st = self.studio([s('a', 'habit-app')], lambda p: None)
        self.assertEqual([p['headline'] for p in st.run_once()['projects']], ['On the next step'])  # never vanishes, never a session name
        st.model = lambda p: (self.asked.append(p), {'1': {'headline': 'Building the next level', 'kind': 'update'}})[1]
        st.said.clear()
        st.run_once()
        n = len(self.asked)
        st.run_once()
        self.assertEqual(len(self.asked), n)

    def test_loose_folders_are_classified_once(self):
        def reply(p):
            if p.startswith('Which project'):
                return {'1': 'coaching'}
            return {'1': {'headline': 'Pricing the coaching', 'kind': 'money'}}
        st = self.studio([s('a', '(home)')], reply)
        self.assertEqual(st.run_once()['projects'][0]['name'], 'Spreadsheet coaching')
        st.run_once()
        self.assertEqual(sum(p.startswith('Which project') for p in self.asked), 1)

    def test_a_failed_classify_asks_again_instead_of_hiding_the_work(self):
        st = self.studio([s('a', '(home)')], lambda p: None)
        st.run_once()
        self.assertNotIn('a', st.picked)


if __name__ == '__main__':
    unittest.main()
