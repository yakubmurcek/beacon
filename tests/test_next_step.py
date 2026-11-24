import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hub'))


class NextStepTest(unittest.TestCase):
    """The task list says whose move a task waits on, read from the whole body before the brief cuts it."""

    def test_owner_step_past_the_cut(self):
        import server
        body = '## Goal\n' + 'x' * 400 + '\n\n## Steps\n1. ✅ 🤖 Build it\n2. 👤 Reset the spare phone\n3. 🤖 Ship\n'
        t = server.brief([{'id': 1, 'body': body, 'step': None, 'last_note': None}])[0]
        self.assertEqual(t['next_step'], {'label': 'Reset the spare phone', 'mine': True})
        self.assertLessEqual(len(t['body']), server.BRIEF_BODY)

    def test_marked_agent_step_and_no_steps(self):
        import server
        body = '## Steps\n1. 👤 Look\n2. 🤖 Build the page\n'
        rows = server.brief([{'id': 1, 'body': body, 'step': 2}, {'id': 2, 'body': 'just words', 'step': None}])
        self.assertEqual(rows[0]['next_step'], {'label': 'Build the page', 'mine': False})
        self.assertIsNone(rows[1]['next_step'])


if __name__ == '__main__':
    unittest.main()
