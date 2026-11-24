"""Knowledge (/knowledge): corpus search, threads, calls, brain notes, and that only he reads them."""
import json
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'hub'), os.path.join(ROOT, 'tests')]

import knowledge  # noqa: E402
from test_tasks import LiveHub  # noqa: E402

# A stand-in for ai-corpus's lib/corpus.py: the same two functions the hub calls, plain LIKE search.
FAKE_LIB = textwrap.dedent('''
    def contact_keys(s):
        s = s.strip()
        return [s.lower()] if "@" in s else ["".join(c for c in s if c.isdigit())]

    def search(conn, q, source=None, sender=None, after=None, before=None, limit=20):
        sql, args = "SELECT * FROM items WHERE text LIKE ?", ["%" + q.strip("*") + "%"]
        if source:
            sql += " AND source=?"
            args.append(source)
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY ts DESC LIMIT ?", args + [limit])]
        cmap = dict(conn.execute("SELECT key, name FROM contacts"))
        for r in rows:
            for k in contact_keys(r["sender"] or ""):
                if k in cmap:
                    r["sender_name"] = cmap[k]
        return rows
''')


def item(id, source, kind, thread, sender, ts, text, meta=None, recipients=None):
    return (id, source, kind, thread, sender, recipients, ts, text, json.dumps(meta) if meta else None, None)


class Fixture:
    def make(self):
        self.ktmp = tempfile.TemporaryDirectory()
        corpus = os.path.join(self.ktmp.name, 'corpus')
        os.makedirs(os.path.join(corpus, 'lib'))
        with open(os.path.join(corpus, 'lib', 'corpus.py'), 'w') as f:
            f.write(FAKE_LIB)
        con = sqlite3.connect(os.path.join(corpus, 'corpus.db'))
        con.executescript('CREATE TABLE items (id TEXT PRIMARY KEY, source TEXT, kind TEXT, thread_id TEXT, sender TEXT, '
                          'recipients TEXT, ts TEXT, text TEXT, meta_json TEXT, original_path TEXT);'
                          'CREATE TABLE contacts (key TEXT PRIMARY KEY, name TEXT);')
        con.execute("INSERT INTO contacts VALUES ('420777111222', 'Patrik')")
        rows = [
            item('m1', 'icloud-mail', 'email', 't-mail', 'patrik@plevak.cz', '2026-11-01T10:00:00Z',
                 'Router quote\n\nCena za router je 2 000 K&#x10d;', recipients='["alex@example.com"]'),
            item('i1', 'imessage', 'message', 't-im', '+420777111222', '2026-11-02T10:00:00Z', 'ten router uz jede'),
            item('i2', 'imessage', 'message', 't-im', 'me', '2026-11-02T10:01:00Z', 'super'),
            item('s1', 'claude-sessions', 'session', 'sess-1', 'user', '2026-11-03T10:00:00Z', 'fix the router page',
                 {'cwd': '/Users/alex/Projects/scratch-2026-11-03-ab12cd'}),
            item('s2', 'claude-sessions', 'session-harness', 'sess-1', 'user', '2026-11-03T10:00:05Z', 'router tool output'),
        ]
        for i in range(6):  # a real chat in the shop project that read the decisions note
            rows.append(item(f'g{i}', 'claude-sessions', 'session', 'sess-g', 'user' if i % 2 == 0 else 'assistant',
                             f'2026-11-05T10:0{i}:00Z', 'read ~/Projects/ai-brain/knowledge/decisions.md first' if i == 1 else f'step {i}',
                             {'cwd': '/root/projects/shop/web'}))
        rows.append(item('h1', 'claude-sessions', 'session', 'sess-2', 'user', '2026-11-05T11:00:00Z', 'hi', {'cwd': '/root/projects/shop'}))
        digest = {'summary': 'They planned the router.', 'decisions': ['Buy it'], 'action_items': ['Alex: order'], 'open_questions': []}
        for i in range(5):  # a call: Alex, Alex, Patrik, Alex, Patrik
            who = ['Alex Novak', 'Alex Novak', 'Patrik', 'Alex Novak', 'Patrik'][i]
            meta = {'offset_s': i * 10.0, 'duration_s': 4.0 if who == 'Alex Novak' else 8.0, 'title': 'Router call'}
            if i == 0:
                meta['digest'] = digest
            rows.append(item(f'c{i}', 'transcripts', 'utterance', 'call-1', who, f'2026-11-04T10:00:{i * 10:02d}Z', f'line {i} router'))
            con.execute('INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?)', rows[-1][:8] + (json.dumps(meta), None))
            rows.pop()
        con.executemany('INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
        con.commit()
        con.close()

        brain = os.path.join(self.ktmp.name, 'brain')
        notes = {
            'index.md': '# map\n- [[decisions]] — key choices and the why.\n',
            'knowledge/decisions.md': '# Decisions\n\nWe chose the [[router-guide|router guide]].\n',
            'knowledge/reference/net/router-guide.md': '---\ntitle: Router guide\n---\n# Router guide\n\nHow the router works.\n',
            'knowledge/feedback/short-replies.md': '# Short replies\n\nKeep replies short, see [[nowhere]] and `[[not-a-link]]`.\n',
            'knowledge/archive/old.md': '# Old\n\nrouter retired\n',
        }
        for rel, text in notes.items():
            os.makedirs(os.path.dirname(os.path.join(brain, rel)), exist_ok=True)
            with open(os.path.join(brain, rel), 'w') as f:
                f.write(text)
        subprocess.run(['git', 'init', '-q', brain], check=True)
        subprocess.run(['git', '-C', brain, '-c', 'user.email=t@t', '-c', 'user.name=t', 'add', '.'], check=True)
        subprocess.run(['git', '-C', brain, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'notes'], check=True)
        return knowledge.Knowledge(corpus, brain, os.path.join(self.ktmp.name, 'no-gbrain'))


class KnowledgeTest(Fixture, unittest.TestCase):
    def setUp(self):
        self.k = self.make()

    def tearDown(self):
        self.ktmp.cleanup()

    def test_search_finds_every_lane_and_skips_harness_noise(self):
        r = self.k.search('router')
        ids = [x['id'] for x in r['corpus']['items']]
        self.assertIn('m1', ids)
        self.assertNotIn('s2', ids)  # tool output is not something anyone said
        mail = next(x for x in r['corpus']['items'] if x['id'] == 'm1')
        self.assertEqual((mail['title'], mail['to']), ('Router quote', 'alex@example.com'))
        self.assertIn('2 000 Kč', mail['snippet'])  # entities decoded
        im = next(x for x in r['corpus']['items'] if x['id'] == 'i1')
        self.assertEqual(im['who'], 'Patrik')  # the contact's name, not the number
        chat = next(x for x in r['corpus']['items'] if x['id'] == 's1')
        self.assertEqual((chat['who'], chat.get('project')), ('Alex', None))  # a scratch folder is no project
        self.assertEqual([x['path'] for x in r['brain']['items']][:1], ['knowledge/reference/net/router-guide.md'])
        self.assertNotIn('knowledge/archive/old.md', [x['path'] for x in r['brain']['items']])
        self.assertIn('not reachable', r['gbrain']['error'])  # a missing gbrain is one lane's note, not a failure

    def test_a_source_filter_searches_only_there(self):
        r = self.k.search('router', 'imessage')
        self.assertEqual({x['source'] for x in r['corpus']['items']}, {'imessage'})
        self.assertNotIn('brain', r)
        self.assertNotIn('gbrain', r)

    def test_thread_opens_in_order_with_names(self):
        t = self.k.thread('t-im', 'i1')
        self.assertEqual([(x['who'], x['me']) for x in t['items']], [('Patrik', False), ('Alex', True)])
        self.assertEqual(t['title'], 'Messages with Patrik')
        self.assertEqual(self.k.thread('sess-1')['total'], 1)  # harness rows left out
        self.assertIsNone(self.k.thread('nope'))

    def test_calls_list_and_one_call_by_speaker(self):
        calls = self.k.calls()
        self.assertEqual(len(calls), 1)
        c = calls[0]
        self.assertEqual((c['title'], c['turns'], c['summary'], c['decisions'], c['actions']), ('Router call', 5, 'They planned the router.', 1, 1))
        self.assertEqual({s['who']: s['seconds'] for s in c['speakers']}, {'Alex Novak': 12, 'Patrik': 16})
        one = self.k.call('call-1')
        self.assertEqual([t['who'] for t in one['turns']], ['Alex Novak', 'Patrik', 'Alex Novak', 'Patrik'])
        self.assertEqual(one['turns'][0]['text'], 'line 0 router line 1 router')  # one person's run is one turn
        self.assertEqual(one['decisions'], ['Buy it'])

    def test_notes_are_cards_with_index_lines_and_no_archive(self):
        notes = {n['path']: n for n in self.k.notes()}
        self.assertNotIn('knowledge/archive/old.md', notes)
        self.assertEqual(notes['knowledge/decisions.md']['blurb'], 'key choices and the why.')
        self.assertEqual(notes['knowledge/reference/net/router-guide.md']['section'], 'reference')
        self.assertEqual(notes['knowledge/feedback/short-replies.md']['section'], 'feedback')
        n = self.k.note('knowledge/reference/net/router-guide.md')
        self.assertEqual((n['title'], n['text'].strip()), ('Router guide', 'How the router works.'))
        self.assertIsNone(self.k.note('../../etc/passwd'))
        self.assertIsNone(self.k.note('knowledge/../index.md/../../x.md'))


    def test_graph_links_open_the_note_the_page_opens(self):
        g = self.k.graph()
        nodes = {x['id']: x for x in g['nodes']}
        links = {(l['source'], l['target']) for l in g['links']}
        self.assertNotIn('index.md', nodes)  # a table of contents, not knowledge: it drew one big star
        self.assertIn(('chat:sess-g', 'project:shop'), links)  # a chat sits in its project's cluster
        self.assertIn(('chat:sess-g', 'knowledge/decisions.md'), links)  # and links to the note it names by path
        self.assertNotIn('chat:sess-2', nodes)  # too short to be a real chat
        self.assertIn(('knowledge/decisions.md', 'knowledge/reference/net/router-guide.md'), links)  # [[router-guide|label]]
        for a, b in links:  # every note a line points at opens through the note API
            if not b.startswith(('missing:', 'person:', 'project:')):
                self.assertIsNotNone(self.k.note(b), b)
        self.assertEqual(nodes['missing:nowhere']['group'], 'missing')  # a link to a note nobody wrote yet
        self.assertNotIn('missing:not-a-link', nodes)  # code is not a link
        self.assertNotIn('knowledge/archive/old.md', nodes)
        self.assertEqual(nodes['knowledge/decisions.md']['deg'], 2)  # router-guide + the chat
        self.assertEqual(knowledge.graph_project('C:\\Users\\x\\Projects\\clean-the-snow\\src\\live'), 'clean-the-snow')
        self.assertEqual(knowledge.graph_project('/var/lib/ai-brain'), 'ai-brain')
        self.assertIsNone(knowledge.graph_project('/Users/alex/Projects'))
        self.assertIsNone(knowledge.graph_project('C:\\Users\\murcj'))
        self.assertIsNone(knowledge.graph_project('C:\\Users\\murcj\\Projects'))
        self.assertEqual(knowledge.graph_project('C:\\Users\\murcj\\Godot Projects\\ayrus'), 'ayrus')
        self.assertIn(('call:call-1', 'person:alex'), links)  # Alex Novak and Alex are one person
        self.assertIn(('call:call-1', 'person:patrik'), links)

    def test_speaker_labels_become_people_only_when_they_are_names(self):
        sn = knowledge.speaker_name
        self.assertEqual(sn('Alex Novak'), ('alex', 'Alex'))
        self.assertEqual(sn('KommTo')[0], sn('Komto')[0])
        self.assertEqual(sn('Radim (bazénový technik)'), ('radim', 'Radim'))
        self.assertEqual(sn('Šárka'), ('sarka', 'Šárka'))
        for label in ('Mixed (Soniox labels swap): Šárka/KommTo for most of the play', 'majitel bazénu', 'Třetí hráč (Komto)',
                      'Spolupracovník (Roblox)', 'Mix (Alex, Šárka, Komto)', '[nenahráno', 'kamarád (na telefonu)', ''):
            self.assertIsNone(sn(label), label)


class KnowledgeHttpTest(Fixture, LiveHub, unittest.TestCase):
    def setUp(self):
        LiveHub.setUp(self)
        self.server.Handler.knowledge = self.make()

    def tearDown(self):
        self.server.Handler.knowledge = None
        LiveHub.tearDown(self)
        self.ktmp.cleanup()

    def test_only_he_or_the_token_reads_it(self):
        self.assertEqual(self.call('/api/knowledge/calls')[0], 401)  # any other tailnet device
        self.assertEqual(self.call('/api/knowledge/graph')[0], 401)
        self.assertEqual(self.call('/api/knowledge/graph', headers={'Tailscale-User-Login': 'someone@else'})[0], 401)
        code, body = self.call('/api/knowledge/graph', headers=self.me())
        self.assertEqual(code, 200)
        self.assertTrue(body['nodes'] and body['links'])
        self.assertEqual(self.call('/api/knowledge/search?q=router', headers={'Tailscale-User-Login': 'someone@else'})[0], 401)
        code, body = self.call('/api/knowledge/calls', headers=self.me())
        self.assertEqual((code, len(body['calls'])), (200, 1))
        code, body = self.call('/api/knowledge/search?q=router', headers={'Authorization': 'Bearer tok'})
        self.assertEqual(code, 200)
        self.assertTrue(body['corpus']['items'])

    def test_bad_and_missing(self):
        self.assertEqual(self.call('/api/knowledge/search', headers=self.me())[0], 400)
        self.assertEqual(self.call('/api/knowledge/thread?id=nope', headers=self.me())[0], 404)
        self.assertEqual(self.call('/api/knowledge/note?path=../../x.md', headers=self.me())[0], 404)
        self.assertEqual(self.call('/api/knowledge/gbrain?slug=x', headers=self.me())[0], 503)  # not on this box
        code, page = self.call('/knowledge')
        self.assertEqual(code, 200)
        self.assertIn(b'knowledge.js', page)


if __name__ == '__main__':
    unittest.main()
