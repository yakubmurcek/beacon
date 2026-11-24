import datetime
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
import rank  # noqa: E402
from store import Store  # noqa: E402


def m(**kw):
    base = dict(sessions=0, tasks=0, prs=0, releases=0, users=0, robux=0, spend=0, passive=0, money=0)
    base.update(kw)
    base.setdefault('profit', base['money'] - base['spend'])
    base.setdefault('passive_profit', base['passive'] - base['spend'])
    return base


class ScorerTest(unittest.TestCase):
    # gates as the owner set them: profit that is his, Grandmaster ~$300 a month passive, Challenger ~$20k
    def test_pure_grind_is_hardstuck_bronze(self):
        s = rank.score(m(sessions=5000, tasks=900, prs=700))
        self.assertEqual(s["tier"], "BRONZE")

    def test_live_then_users(self):
        self.assertEqual(rank.score(m(prs=3, releases=20))["tier"], "SILVER")
        self.assertEqual(rank.score(m(releases=1, users=100))["tier"], "GOLD")

    def test_now_a_live_game_without_profit_is_gold_iv(self):
        # 2026-11-22: 23k Roblox new users, 13k Robux earned (not cashable), ads cost more
        s = rank.score(m(releases=14, users=23403, robux=13361, spend=3000))
        self.assertEqual(s["label"], "Gold IV")

    def test_robux_are_money_only_when_cashable_and_his_half(self):
        today = datetime.date(2026, 11, 22)
        series = {(today - datetime.timedelta(days=i)).isoformat(): {"robux": 1000, "robux_kc": 85, "roblox_new": 10} for i in range(20)}
        mm = rank.metrics_for(series, today.isoformat())
        self.assertEqual(mm["money"], 0)  # 20,000 earned: below DevEx
        for i in range(20, 30):
            series[(today - datetime.timedelta(days=i)).isoformat()] = {"robux": 1000, "robux_kc": 85}
        mm = rank.metrics_for(series, today.isoformat())
        self.assertAlmostEqual(mm["money"], 30 * 85 / 2)
        self.assertEqual(rank.decide_tier(mm), "PLATINUM")

    def test_ads_count_against_profit(self):
        self.assertEqual(rank.score(m(users=500, money=1500, passive=1500, spend=3000))["label"], "Gold II")  # 50% to break-even
        self.assertEqual(rank.score(m(users=500, money=4000, passive=0, spend=1000))["tier"], "EMERALD")  # a client, ads cost 1,000

    def test_money_tiers(self):
        self.assertEqual(rank.score(m(money=40000, passive=0))["tier"], "DIAMOND")  # a client, nothing passive
        self.assertEqual(rank.score(m(money=2200, passive=2200))["tier"], "MASTER")  # ~$100 a month passive
        self.assertEqual(rank.score(m(money=6700, passive=6700))["tier"], "GRANDMASTER")  # ~$300
        ch = rank.score(m(money=445000, passive=445000))  # ~$20,000
        self.assertEqual(ch["tier"], "CHALLENGER")
        self.assertIsNone(ch["division"])

    def test_warning_finds_the_day_silver_falls(self):
        today = datetime.date(2026, 11, 22)
        series = {(today - datetime.timedelta(days=b)).isoformat(): {"releases": 1} for b in (25, 24)}
        self.assertEqual(rank.decide_tier(rank.metrics_for(series, today.isoformat())), "SILVER")
        w = rank.warning("SILVER", series, today.isoformat())
        self.assertEqual(w["drop"], (today + datetime.timedelta(days=6)).isoformat())
        self.assertEqual(w["what"], "release")

    def test_divisions(self):
        self.assertEqual(rank.division_for("PLATINUM", 0), "IV")
        self.assertEqual(rank.division_for("PLATINUM", 80), "I")
        self.assertIsNone(rank.division_for("MASTER", 50))


class RankStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "u.db"), "Europe/Prague")
        self.rank = rank.Rank(self.store)
        self.today = datetime.date.fromisoformat(self.store.today())

    def day(self, back):
        return (self.today - datetime.timedelta(days=back)).isoformat()

    def test_ingest_scores_and_keeps_history(self):
        res = self.rank.ingest({"sources": {"roblox": {"ok": True}}, "days": {
            self.day(20): {"roblox_new": 100, "robux_kc": 50}, self.day(2): {"roblox_new": 5}},
            "releases": [{"key": "roblox:x", "day": self.day(25), "kind": "roblox", "what": "Clean the Snow update"}]})
        self.assertEqual(res["tier_label"], "Gold IV")  # 100 Robux are not cashable: users only
        st = self.rank.get()
        self.assertEqual(len(st["history"]), 31)
        self.assertEqual(st["history"][0]["tier"], "IRON")  # 30 days ago nothing had happened yet
        self.assertEqual(st["now"]["warning"]["what"], "new user")  # 100 of the 105 users came 20 days ago
        self.assertEqual(st["now"]["warning"]["drop"], self.day(-10))
        users = next(s for s in st["signals"] if s["key"] == "users")
        self.assertEqual(users["value"], 105)
        self.assertEqual(users["last7"], 5)

    def test_failed_source_keeps_the_good_days(self):
        self.rank.ingest({"sources": {"roblox": {"ok": True}}, "days": {self.day(3): {"roblox_new": 40}}})
        self.rank.ingest({"sources": {"roblox": {"ok": False, "why": "timeout"}}, "days": {}})
        st = self.rank.get()
        users = next(s for s in st["signals"] if s["key"] == "users")
        self.assertEqual(users["value"], 40)
        self.assertFalse(users["ok"])  # shown as not read, the number kept
        rb = next(s for s in st["sources"] if s["name"] == "Roblox")
        self.assertFalse(rb["ok"])
        self.assertTrue(rb["good_at"])

    def test_bad_values_are_dropped(self):
        self.rank.ingest({"days": {self.day(1): {"roblox_new": -5, "robux_kc": float("nan"), "evil": 3, "prs": 2}}})
        with self.store.connect() as con:
            keys = [r[0] for r in con.execute("SELECT key FROM rank_days")]
        self.assertEqual(keys, ["prs"])
        self.rank.ingest({"days": {}, "rates": {"USD": "<img src=x>", "devex_usd": 0.0038}})
        self.assertIsNone(self.rank.get()["rates"])  # only numbers are kept
        with self.assertRaises(ValueError):
            self.rank.ingest({"days": {"not-a-day": {"prs": 1}}})


if __name__ == "__main__":
    unittest.main()
