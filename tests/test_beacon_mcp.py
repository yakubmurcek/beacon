import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from beacon_mcp import UI_MIME, UI_URI, BeaconMCP  # noqa: E402
from labs import Labs  # noqa: E402
from store import Store  # noqa: E402


def rpc(mcp, method, params=None, mid=1):
    return mcp.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


class BeaconMCPTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        labs = Labs(Store(os.path.join(self.dir.name, "t.db"), "Europe/Prague"))
        labs.source = lambda name, days=7: {"kind": "series", "unit": "users", "points": [{"label": "Mon", "value": 5}],
                                            "total": 5, "prev": 9, "span": "last 7 days"}
        labs.digest = lambda: "- users_days: 5 this week"
        self.mcp = BeaconMCP(labs)

    def tearDown(self):
        self.dir.cleanup()

    def test_handshake_echoes_a_known_version_and_offers_the_ui_extension(self):
        r = rpc(self.mcp, "initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertIn("io.modelcontextprotocol/ui", r["capabilities"]["extensions"])
        self.assertIsNone(self.mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_the_show_tool_points_at_the_card_view(self):
        tools = {t["name"]: t for t in rpc(self.mcp, "tools/list")["result"]["tools"]}
        self.assertEqual(tools["beacon_show"]["_meta"]["ui"]["resourceUri"], UI_URI)
        self.assertNotIn("_meta", tools["beacon_numbers"])  # data only, no view

    def test_cards_come_back_live_and_only_from_the_catalog(self):
        r = rpc(self.mcp, "tools/call", {"name": "beacon_show", "arguments": {"title": "Users", "cards": [
            {"type": "number", "title": "New users", "source": "users_days", "days": 7},
            {"type": "html", "html": "<script>alert(1)</script>"},
            {"type": "say", "text": "the model speaks for itself"}]}})["result"]
        cards = r["structuredContent"]["cards"]
        self.assertEqual([c["type"] for c in cards], ["number"])
        self.assertEqual(cards[0]["data"]["total"], 5)
        self.assertIn("New users: 5", r["content"][0]["text"])  # the model reads the same number it shows

    def test_nothing_drawable_is_a_tool_error(self):
        r = rpc(self.mcp, "tools/call", {"name": "beacon_show", "arguments": {"cards": [{"type": "nope"}]}})["result"]
        self.assertTrue(r["isError"])

    def test_the_view_is_one_html_page_with_the_labs_cards_inside(self):
        c = rpc(self.mcp, "resources/read", {"uri": UI_URI})["result"]["contents"][0]
        self.assertEqual(c["mimeType"], UI_MIME)
        self.assertIn("function columns(", c["text"])  # sliced from web/labs.js
        self.assertIn(".lc-big", c["text"])            # sliced from web/labs.css
        self.assertIn("ui/initialize", c["text"])
        self.assertNotIn("/*CARDS_CSS*/", c["text"])
        self.assertNotIn("<script src", c["text"])     # nothing from outside: the default CSP holds

    def test_unknown_method_and_resource(self):
        self.assertEqual(rpc(self.mcp, "nope")["error"]["code"], -32601)
        self.assertEqual(rpc(self.mcp, "resources/read", {"uri": "ui://x"})["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()


class KeyedDoorTest(unittest.TestCase):
    """/mcp/<key>: the link Claude's and ChatGPT's servers use. The key is the login; no key set = no door."""
    KEY = 'k' * 40

    def setUp(self):
        sys.path.insert(0, os.path.dirname(__file__))
        from test_tasks import LiveHub
        import server
        self.hub = LiveHub()
        self.hub.setUp()
        server.Handler.mcp = BeaconMCP(Labs(server.Handler.store))
        self.saved_key = server.MCP_KEY
        self.server = server

    def tearDown(self):
        self.server.MCP_KEY = self.saved_key
        self.hub.tearDown()

    def ping(self, path, headers=None):
        return self.hub.call(path, {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, headers)[0]

    def test_only_the_right_key_opens_it(self):
        self.server.MCP_KEY = ''
        self.assertEqual(self.ping('/mcp/' + self.KEY), 404)   # unset: the door doesn't exist
        self.server.MCP_KEY = 'short'
        self.assertEqual(self.ping('/mcp/short'), 404)          # a weak key never opens it
        self.server.MCP_KEY = self.KEY
        self.assertEqual(self.ping('/mcp/' + self.KEY), 200)
        self.assertEqual(self.ping('/mcp/' + 'x' * 40), 404)
        self.assertEqual(self.ping('/mcp/'), 404)
        self.assertEqual(self.ping('/mcp'), 401)                # the plain path still wants his login
        self.assertEqual(self.ping('/mcp', self.hub.me()), 200)
