import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
import outcomes  # noqa: E402

NOW = 1795400000.0  # 2026-11-22 afternoon, Prague
OK = {"ok": True, "checked": NOW - 120}


def card(view, period, key):
    return next(c for c in view["periods"][period]["cards"] if c["key"] == key)


def part(c, label):
    return next(p for p in c["parts"] if p["label"] == label)


class OutcomesTest(unittest.TestCase):
    def sources(self, **over):
        s = {n: dict(OK) for n in ("appstore", "roblox", "reach", "vercel", "bank", "mail", "calls")}
        s["appstore"]["through"] = s["roblox"]["through"] = "2026-11-20"
        s["bank"]["through"] = "2026-09-02"
        s.update(over)
        return s

    def test_periods_are_calendar_today_week_month(self):
        p = outcomes.periods("2026-11-22")  # a Sunday
        self.assertEqual(p["week"][0].isoformat(), "2026-11-16")
        self.assertEqual(p["month"][0].isoformat(), "2026-11-01")

    def test_honest_zero_vs_not_read(self):
        series = {"2026-11-18": {"appstore_new": 0, "roblox_new": 40}}
        v = outcomes.build(series, [], self.sources(roblox={"ok": False, "why": "timeout", "good_at": NOW - 7200}),
                           "2026-11-22", NOW, NOW - 60)
        users = card(v, "week", "users")
        self.assertEqual(part(users, "App downloads")["value"], 0)  # a real zero
        self.assertIsNone(part(users, "Roblox new players")["value"])  # down: not a zero
        self.assertEqual(users["value"], 0)
        self.assertEqual(users["down"], 1)
        self.assertTrue(part(users, "Roblox new players")["age"].startswith("not read since"))

    def test_late_source_is_unknown_today_not_zero(self):
        v = outcomes.build({}, [], self.sources(), "2026-11-22", NOW, NOW - 60)
        self.assertIsNone(part(card(v, "today", "users"), "App downloads")["value"])
        self.assertEqual(part(card(v, "week", "users"), "App downloads")["age"], "through 20 Nov")

    def test_bank_that_stops_before_the_month_is_unknown(self):
        v = outcomes.build({"2026-09-01": {"bank_in": 5000}}, [], self.sources(), "2026-11-22", NOW, NOW - 60)
        bank = part(card(v, "month", "money"), "Bank")
        self.assertIsNone(bank["value"])
        self.assertIn("2 Sep", bank["age"])

    def test_money_sums_parts_and_carries_the_burn_line(self):
        series = {"2026-11-03": {"robux_kc": 700, "appstore_kc": 50}, "2026-11-19": {"robux_kc": 30}}
        v = outcomes.build(series, [], self.sources(), "2026-11-22", NOW, NOW - 60, robux_ever=40000)
        money = card(v, "month", "money")
        self.assertEqual(money["value"], 415)  # his half of 730 Kč of Robux + 50 Kč App Store
        self.assertEqual(money["burn"], {"low": 20000, "high": 30000})
        self.assertEqual(card(v, "week", "money")["burn"]["low"], 4667)  # 7 days of a 20k month

    def test_robux_below_the_cash_out_line_are_shown_not_counted(self):
        series = {"2026-11-03": {"robux_kc": 700, "appstore_kc": 50, "robux": 8000}}
        v = outcomes.build(series, [], self.sources(), "2026-11-22", NOW, NOW - 60)
        money = card(v, "month", "money")
        self.assertEqual(money["value"], 50)
        robux = part(money, "Robux, his half")
        self.assertEqual(robux["value"], 350)
        self.assertFalse(robux["counted"])
        self.assertIn("8,000 of 30,000", robux["age"])

    def test_roblox_buyers_missing_until_read(self):
        v = outcomes.build({"2026-11-18": {"appstore_paid": 0}}, [], self.sources(), "2026-11-22", NOW, NOW - 60)
        self.assertIsNone(part(card(v, "week", "paying"), "Roblox buyers")["value"])
        v = outcomes.build({"2026-11-18": {"roblox_payers": 3}}, [], self.sources(), "2026-11-22", NOW, NOW - 60)
        self.assertEqual(card(v, "week", "paying")["value"], 3)

    def test_shipped_counts_releases_and_sites_by_day(self):
        rel = [{"key": "roblox:x", "day": "2026-11-21", "kind": "roblox", "what": "Clean the Snow update on Roblox"},
               {"key": "vercel:alex-site:2026-11-16", "day": "2026-11-16", "kind": "web", "what": "alex-site"}]
        vercel = dict(OK, sites=[{"name": "alex-site", "day": "2026-11-16"}, {"name": "alex-site", "day": "2026-11-22"},
                                 {"name": "excel-s-ai", "day": "2026-11-22"}])
        v = outcomes.build({}, rel, self.sources(vercel=vercel), "2026-11-22", NOW, NOW - 60)
        self.assertEqual(card(v, "today", "shipped")["value"], 2)
        week = card(v, "week", "shipped")
        self.assertEqual(week["value"], 3)
        self.assertIn("alex.example ×2", [x["what"] for x in week["items"]])

    def test_partial_coverage_says_since_when(self):
        src = dict(OK, since={"roblox_visits": "2026-11-16"})
        v = outcomes.build({"2026-11-17": {"roblox_visits": 100}}, [], self.sources(reach=src), "2026-11-22", NOW, NOW - 60)
        self.assertEqual(part(card(v, "month", "reach"), "Roblox visits")["age"], "counted since 16 Nov")

    def test_one_stale_part_does_not_take_down_its_neighbour(self):
        reach = dict(OK, down={"tiktok_views": "TikTok last read 20 Nov"})
        v = outcomes.build({"2026-11-21": {"roblox_visits": 50, "tiktok_views": 9}}, [], self.sources(reach=reach), "2026-11-22", NOW, NOW - 60)
        c = card(v, "week", "reach")
        self.assertIsNone(part(c, "TikTok views")["value"])
        self.assertEqual(part(c, "TikTok views")["state"], "down")
        self.assertEqual(part(c, "Roblox visits")["value"], 50)

    def test_a_part_can_run_later_than_its_source(self):
        app = dict(OK, through="2026-11-20", through_by={"appstore_seen": "2026-11-18"})
        v = outcomes.build({}, [], self.sources(appstore=app), "2026-11-22", NOW, NOW - 60)
        self.assertEqual(part(card(v, "week", "reach"), "App Store views")["age"], "through 18 Nov")
        today = part(card(v, "today", "reach"), "App Store views")
        self.assertEqual((today["value"], today["state"]), (None, "late"))
        self.assertEqual(today["age"], "App Store reports 4 days late")

    def test_cash_out_gate_uses_the_sources_lifetime_robux(self):
        rob = dict(OK, through="2026-11-20", robux_ever=31000)
        v = outcomes.build({"2026-11-18": {"robux_kc": 100}}, [], self.sources(roblox=rob), "2026-11-22", NOW, NOW - 60)
        self.assertEqual(card(v, "week", "money")["value"], 50)

    def test_stale_collector(self):
        self.assertTrue(outcomes.build({}, [], self.sources(), "2026-11-22", NOW, NOW - 4 * 3600)["stale"])
        self.assertTrue(outcomes.build({}, [], {}, "2026-11-22", NOW, None)["stale"])


if __name__ == "__main__":
    unittest.main()
