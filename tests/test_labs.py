import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from labs import Labs, similarity  # noqa: E402
from store import Store  # noqa: E402


def scripted(*answers):
    """A stand-in for Sonnet: each call streams the next answer in small pieces."""
    queue = list(answers)
    prompts = []

    def model(prompt):
        prompts.append(prompt)
        text = queue.pop(0)
        for i in range(0, len(text), 7):
            yield text[i:i + 7]
    model.prompts = prompts
    return model


FIRST = '\n'.join([
    '{"view":"weekly-spend","title":"Weekly spend","changed":""}',
    '{"type":"say","text":"About $100 this week."}',
    '{"type":"number","title":"This week","source":"spend_days","days":7}',
    '{"type":"suggest","items":["And last month?"]}',
])
AGAIN = '\n'.join([
    '{"view":"weekly-spend","title":"Weekly spend","changed":"added a warning: devbox is failing"}',
    '{"type":"say","text":"Still about $100."}',
    '{"type":"number","title":"This week","source":"spend_days","days":7}',
    '{"type":"alert","tone":"bad","text":"devbox keeps failing a job"}',
    '{"type":"suggest","items":["Fix devbox?"]}',
])


class LabsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.dir.name, "t.db"), "Europe/Prague")

    def tearDown(self):
        self.dir.cleanup()

    def labs(self, model):
        lab = Labs(self.store, model=model)
        lab.source = lambda name, days=7: {"kind": "series", "unit": "$", "points": [], "total": 100, "prev": 90}
        lab.digest = lambda: "- spend_days: test"
        return lab

    def test_first_answer_streams_cards_and_is_saved(self):
        lab = self.labs(scripted(FIRST))
        events = list(lab.ask("How much did I spend this week?"))
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds, ["view", "card", "card", "card", "done"])
        self.assertEqual(events[2]["card"]["data"]["total"], 100)  # bound to a live source, filled by the server
        v = lab.view("weekly-spend")
        self.assertEqual(v["asks"], 1)
        self.assertNotIn("data", v["cards"][1])  # saved unresolved, so the next render is fresh

    def test_asking_again_shows_the_saved_view_first_then_refreshes_it(self):
        model = scripted(FIRST, AGAIN)
        lab = self.labs(model)
        list(lab.ask("How much did I spend this week?"))
        events = list(lab.ask("how much did I spend this week"))
        self.assertTrue(events[0]["cached"])
        self.assertIn("cached_done", [e["type"] for e in events])
        refresh = [e for e in events if e["type"] == "view" and e.get("refresh")]
        self.assertTrue(refresh and refresh[0]["same"])
        self.assertIn('"cards"', model.prompts[1])  # the old layout went to the model to keep
        v = lab.view("weekly-spend")
        self.assertEqual(v["asks"], 2)
        self.assertEqual(v["changes"][0]["what"], "added a warning: devbox is failing")
        self.assertEqual([c["type"] for c in v["cards"]], ["say", "number", "alert", "suggest"])

    def test_only_catalog_cards_with_plain_values_get_through(self):
        self.assertIsNone(Labs.clean_card({"type": "html", "html": "<script>x</script>"}))
        self.assertIsNone(Labs.clean_card({"type": "say"}))
        c = Labs.clean_card({"type": "list", "title": "T", "source": "rm -rf", "onclick": "x",
                             "items": [{"title": "a", "tone": "evil", "href": "javascript:x"}]})
        self.assertNotIn("source", c)
        self.assertNotIn("onclick", c)
        self.assertEqual(c["items"], [{"title": "a"}])

    def test_garbage_from_the_model_is_an_error_not_a_crash(self):
        lab = self.labs(scripted("Sorry, I can't.\n```"))
        events = list(lab.ask("anything"))
        self.assertEqual(events[-1]["type"], "error")

    def test_forget(self):
        lab = self.labs(scripted(FIRST))
        list(lab.ask("How much did I spend this week?"))
        self.assertTrue(lab.forget("weekly-spend")["ok"])
        self.assertIsNone(lab.view("weekly-spend"))

    def test_similar_questions_match(self):
        self.assertGreaterEqual(similarity("How much did I spend this week?", "how much did i spend this week"), 0.5)
        self.assertLess(similarity("How much did I spend this week?", "Are my machines OK?"), 0.5)


if __name__ == "__main__":
    unittest.main()
