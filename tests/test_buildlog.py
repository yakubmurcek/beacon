import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hub"))
import buildlog  # noqa: E402


class BuildLogTest(unittest.TestCase):
    def test_games_are_the_projects_that_say_game(self):
        games = buildlog.load_games()
        self.assertIn("snow-game", games)
        self.assertEqual(games["snow-game"]["name"], "Snow Game")
        self.assertNotIn("hq", games)

    def test_uuid_of_a_hub_session_id(self):
        self.assertEqual(buildlog.uuid_of("Claude Code:a305bca5-9a2c-41a1-8fdd-f72c56a221e2"),
                         "a305bca5-9a2c-41a1-8fdd-f72c56a221e2")
        self.assertIsNone(buildlog.uuid_of("Codex:nothing"))

    def test_result_line_is_the_first_text_line(self):
        self.assertEqual(buildlog.first_line("\n**✅ Done.**\nmore"), "✅ Done.")
        self.assertTrue(buildlog.RESULT.match("⚠️ Not yet"))
        self.assertFalse(buildlog.RESULT.match("Now RunManager locally."))

    def test_not_a_game_is_refused(self):
        log = buildlog.BuildLog(store=None)
        with self.assertRaises(ValueError):
            log.game("budget")

    def test_a_session_that_is_not_a_game_is_not_shown(self):
        class Store:
            def session(self, machine, session):
                return {"project": "beacon", "turns": []}
        self.assertIsNone(buildlog.BuildLog(store=Store()).session("devbox", "Claude Code:a305bca5-9a2c-41a1-8fdd-f72c56a221e2"))


if __name__ == "__main__":
    unittest.main()
