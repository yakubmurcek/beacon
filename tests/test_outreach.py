"""Outreach: leads from the brain's own list readers + the hand-kept file, research from the registry + live folders, the file viewer."""
import json
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hub"))
from outreach import Outreach, logo, render_md  # noqa: E402

REF = "knowledge/reference/"


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class OutreachTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.brain = os.path.join(self.tmp.name, "ai-brain")
        self.projects = os.path.join(self.tmp.name, "projects")
        b = self.brain
        write(b, REF + "lists/gyms.tsv", "name\ttown\ttier\nRiver Gym\tRivertown\tSKIP\nFit Life\tRivertown\tWALK\n")
        write(b, REF + "outreach/sources.py", (  # the brain's own reader for its own list format
            "LABELS = {'gyms': 'Gyms', 'river-gym': 'River Gym'}\n"
            "WATCHED = ['knowledge/reference/lists/gyms.tsv']\n"
            "def readers(o):\n"
            "    def gyms():\n"
            "        return [o.lead(name=r['name'], project='gyms', town=r['town'], know=o.clip('tier ' + r['tier']),\n"
            "                       next='Walk in' if r['tier'] == 'WALK' else '')\n"
            "                for r in o.tsv(o.p('knowledge/reference/lists/gyms.tsv'))]\n"
            "    return [gyms]\n"))
        write(b, REF + "outreach/leads.json", json.dumps({"leads": [
            {"name": "River Gym", "project": "river-gym", "town": "Rivertown", "status": "drafted", "next": "Send the offer"},
            {"name": "Far Away", "project": "weddings", "source": {"label": "x", "url": "", "path": "weddings/none.md"}}]}))
        write(b, REF + "outreach/artifacts.json", json.dumps({"artifacts": [
            {"title": "Who to sell to", "url": "https://claude.ai/artifact/x", "date": "2026-11-20", "project": "gyms", "kind": "research"}]}))
        write(b, REF + "outreach/research-files.json", json.dumps({"files": [
            {"title": "Cold outreach", "project": "business", "date": "2026-09-24", "kind": "research", "doc": "ai-brain/" + REF + "cold-outreach.md"},
            {"title": "Pools", "project": "pools", "date": "2026-07-07", "kind": "research", "doc": "pools/docs/r.md"}]}))
        write(b, REF + "cold-outreach.md", "---\ntype: x\n---\n# Cold outreach\n\nSee [the list](leads.md) and **this**.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        write(b, REF + "research/2026-11-21-new-run/report.md", "# A new run\n")
        write(b, "secret.md", "# no\n")
        write(self.projects, "pools/docs/r.md", "# Pools\n")
        self.o = Outreach(self.brain, self.projects)

    def tearDown(self):
        self.tmp.cleanup()

    def test_leads_merge_and_dedupe(self):
        v = self.o.view()
        names = [(x["name"], x["project"]) for x in v["leads"]]
        self.assertIn(("River Gym", "river-gym"), names)
        self.assertNotIn(("River Gym", "gyms"), names)  # the hand-kept lead wins over the scraped one
        fit = next(x for x in v["leads"] if x["name"] == "Fit Life")
        self.assertEqual((fit["next"], fit["know"], fit["status"]), ("Walk in", "tier WALK", "not contacted"))
        projects = {p["id"]: p for p in v["projects"]}
        self.assertEqual(projects["gyms"]["leads"], 1)
        self.assertEqual(projects["gyms"]["name"], "Gyms")  # the brain's label
        self.assertEqual([p["id"] for p in v["projects"]][:2], ["gyms", "river-gym"])  # in the brain's order
        self.assertNotIn("link", next(x for x in v["leads"] if x["name"] == "Far Away")["source"])  # no such file here

    def test_a_list_in_another_checkout_is_read_live(self):
        write(self.projects, "jobs/log.tsv", "name\tstatus\nAcme\tsent\n")
        write(self.brain, REF + "outreach/sources.py", (
            "def watched(o):\n    return [o.repo('jobs/log.tsv')]\n"
            "def readers(o):\n"
            "    return [lambda: [o.lead(name=r['name'], project='jobs', status=r['status'],\n"
            "                            source={'label': 'Log', 'url': '', 'path': 'jobs/log.tsv'})\n"
            "                     for r in o.tsv(o.repo('jobs/log.tsv'))]]\n"))
        self.assertEqual([x["name"] for x in self.o.view()["leads"] if x["project"] == "jobs"], ["Acme"])
        self.assertTrue(next(x for x in self.o.view()["leads"] if x["name"] == "Acme")["source"]["link"].startswith("/outreach/doc?p=jobs"))
        write(self.projects, "jobs/log.tsv", "name\tstatus\nAcme\tsent\nBeta Ltd\tdrafted\n")  # a new line, no restart
        self.assertEqual(len([x for x in self.o.view()["leads"] if x["project"] == "jobs"]), 2)

    def test_a_lead_carries_its_detail_and_a_file_row_opens_here(self):
        write(self.projects, "jobs/drafts/a.md", "# Draft\n")
        write(self.brain, REF + "outreach/sources.py", (
            "def readers(o):\n"
            "    return [lambda: [o.lead(name='Acme', project='jobs', chance='high', chance_why='Open role fits', person='Ann',\n"
            "                            detail=[{'title': 'Application', 'rows': [{'label': 'Draft', 'text': 'Open', 'path': 'jobs/drafts/a.md'}]}])],\n"
            "            lambda: 1 / 0]\n"))  # a broken reader hides only its own list
        acme = next(x for x in self.o.view()["leads"] if x["name"] == "Acme")
        self.assertEqual((acme["chance"], acme["person"]), ("high", "Ann"))
        self.assertTrue(acme["detail"][0]["rows"][0]["url"].startswith("/outreach/doc?p=jobs/drafts/a.md"))
        self.assertEqual(next(x for x in self.o.view()["leads"] if x["name"] == "River Gym")["detail"], [])  # a hand-kept lead: none

    def test_a_logo_is_fetched_once_and_a_miss_is_remembered(self):
        calls = []
        def fetch(d):
            calls.append(d)
            return b'\x89PNG...' if d == 'acme.cz' else b''
        folder = os.path.join(self.tmp.name, 'logos')
        self.assertEqual(logo('www.Acme.cz', fetch, folder), ('image/png', b'\x89PNG...'))
        self.assertEqual(logo('acme.cz', fetch, folder)[0], 'image/png')
        self.assertIsNone(logo('none.cz', fetch, folder))
        self.assertIsNone(logo('none.cz', fetch, folder))
        self.assertEqual(calls, ['acme.cz', 'none.cz'])
        self.assertIsNone(logo('../etc/passwd', fetch, folder))  # only a plain domain

    def test_open_cards_of_a_lead_group_ride_along(self):
        class Cards:
            def list(self, status='open', limit=500):
                return [{'id': 7, 'project': 'gyms', 'question': 'Send the 5 mails?'},
                        {'id': 8, 'project': 'elsewhere', 'question': 'Not an outreach group'},
                        {'id': 9, 'project': 'business', 'question': 'A group with research only, no leads'}]
        o = Outreach(self.brain, self.projects, questions=Cards())
        self.assertEqual(o.view()["waiting"], [{'id': 7, 'project': 'gyms', 'question': 'Send the 5 mails?'}])
        self.assertEqual(self.o.view()["waiting"], [])  # no card store: nothing waits

    def test_no_sources_file_still_reads_the_hand_kept_leads(self):
        os.remove(os.path.join(self.brain, REF + "outreach/sources.py"))
        names = [x["name"] for x in self.o.view()["leads"]]
        self.assertEqual(sorted(names), ["Far Away", "River Gym"])

    def test_a_broken_sources_file_hides_only_its_lists(self):
        write(self.brain, REF + "outreach/sources.py", "def readers(o):\n    return [lambda: 1/0 if False else (_ for _ in ()).throw(KeyError('x'))]\nsyntax error here\n")
        self.assertEqual(len(self.o.view()["leads"]), 2)

    def test_research_registry_and_live_runs(self):
        r = self.o.view()["research"]
        titles = [x["title"] for x in r]
        self.assertIn("Who to sell to", titles)
        self.assertIn("A new run", titles)  # found in the research folder, never registered
        art = next(x for x in r if x["title"] == "Who to sell to")
        self.assertEqual(art["link"], "https://claude.ai/artifact/x")
        self.assertTrue(next(x for x in r if x["title"] == "Cold outreach")["link"].startswith("/outreach/doc?p="))

    def test_cache_follows_file_changes(self):
        self.assertEqual(len(self.o.view()["research"]), 4)
        write(self.brain, REF + "research/2026-11-22-another/report.md", "# Another\n")
        self.assertEqual(len(self.o.view()["research"]), 5)

    def test_doc_viewer_guards(self):
        ctype, body, foreign = self.o.doc("ai-brain/" + REF + "cold-outreach.md")
        self.assertFalse(foreign)
        self.assertIn(b"<h1>Cold outreach</h1>", body)
        self.assertIn(b"/outreach/doc?p=ai-brain/knowledge/reference/leads.md", body)
        self.assertIsNotNone(self.o.doc("pools/docs/r.md"))
        self.assertIsNone(self.o.doc("ai-brain/secret.md"))  # only knowledge/ in ai-brain
        self.assertIsNone(self.o.doc("ai-brain/" + REF + "../../secret.md"))
        self.assertIsNone(self.o.doc("../etc/passwd"))
        self.assertIsNone(self.o.doc("ai-brain/" + REF + "outreach/leads.json"))  # not a document type

    def test_markdown_table_and_escape(self):
        out = render_md("| a |\n|---|\n| <b>x</b> |\n")
        self.assertIn("<table>", out)
        self.assertIn("&lt;b&gt;", out)


if __name__ == "__main__":
    unittest.main()
