import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import json
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hub'))
import talk  # noqa: E402
from labs import Labs  # noqa: E402
from questions import Questions  # noqa: E402
from store import Store  # noqa: E402
from tasks import Tasks  # noqa: E402


class FakeHTTP:
    """Stands in for OpenAI: records each request, answers a transcript or audio bytes."""
    def __init__(self):
        self.reqs = []

    def __call__(self, req, timeout=30):
        self.reqs.append(req)
        if req.full_url.endswith('/audio/transcriptions'):
            return json.dumps({'text': ' Brief me. '}).encode(), 'application/json'
        return b'ID3fake-mp3', 'audio/mpeg'


class TalkTest(unittest.TestCase):
    def setUp(self):
        saved = os.environ.pop('GATE_MODE', None)  # test_questions turns the gate on at import: here it is in shadow
        self.addCleanup(lambda: saved is not None and os.environ.update(GATE_MODE=saved))
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.dir.name, 't.db'), 'Europe/Prague')
        self.tasks = Tasks(self.store)
        self.qs = Questions(self.store, self.tasks)
        self.labs = Labs(self.store, self.tasks, self.qs)
        self.http = FakeHTTP()
        self.talk = talk.Talk(self.store, self.labs, self.qs, self.tasks, opener=self.http)
        os.environ['OPENAI_API_KEY'] = 'test-key'

    def tearDown(self):
        self.dir.cleanup()

    def test_brief_names_what_waits_and_ends_on_a_question(self):
        self.qs.create({'question': 'Which logo for the shop?', 'options': ['Round', 'Square']})
        self.qs.create({'question': 'Ship the update today?', 'options': ['Yes', 'No']})
        t = self.tasks.create({'title': 'New landing page', 'project': 'shop'})
        self.tasks.update(t['id'], {'status': 'done'})
        b = self.talk.brief('phone')
        self.assertTrue(b['say'].endswith('What first?'))
        self.assertIn('finished one task', b['say'])
        self.assertIn('Two decisions wait for you.', b['say'])
        self.assertEqual(len(b['decisions']), 2)
        self.assertEqual(b['cards'][0]['type'], 'say')
        # the next brief starts from this one: nothing new finished since
        self.assertIn('Nothing new finished', self.talk.brief()['say'])

    def test_hear_sends_the_clip_and_counts_its_minutes(self):
        text = self.talk.hear(b'\x1a\x45webm', 'audio/webm;codecs=opus', 6, device='desk')
        self.assertEqual(text, 'Brief me.')
        req = self.http.reqs[0]
        self.assertIn(b'gpt-4o-mini-transcribe', req.data)
        self.assertIn(b'filename="turn.webm"', req.data)
        self.assertAlmostEqual(self.talk.minutes_today(), 0.1)

    def test_say_caches_a_line_and_the_cap_stops_new_ones(self):
        audio, ctype = self.talk.say('One sec.')
        self.assertEqual((audio, ctype), (b'ID3fake-mp3', 'audio/mpeg'))
        self.talk.say('One sec.')
        self.assertEqual(len(self.http.reqs), 1)  # the repeat came from the cache
        self.talk.log('hear', talk.DAY_MINUTES * 60)
        with self.assertRaises(talk.CapReached):
            self.talk.say('Something new.')
        with self.assertRaises(talk.CapReached):
            self.talk.hear(b'x', 'audio/webm', 2)

    def test_a_confirmed_act_answers_the_decision(self):
        q = self.qs.create({'question': 'Which logo for the shop?', 'options': ['Round', 'Square']})
        out = self.talk.act({'do': 'answer', 'decision': q['id'], 'answer': 'Round'}, 'phone')
        self.assertTrue(out['ok'])
        self.assertEqual((self.qs.get(q['id'])['status'], self.qs.get(q['id'])['answer']), ('answered', 'Round'))
        with self.assertRaises(talk.VoiceError):
            self.talk.act({'do': 'send', 'decision': q['id'], 'answer': 'x'})
        self.assertEqual(self.talk.stats(1)['days'][self.store.today()]['acts'], 1)

    def test_a_risky_decision_is_never_answered_by_voice(self):
        q = self.qs.create({'question': 'Send the mail to the client?', 'options': ['Yes', 'No'], 'blocking': True})
        self.assertTrue(q['blocking'])
        with self.assertRaises(talk.VoiceError):
            self.talk.act({'do': 'answer', 'decision': q['id'], 'answer': 'Yes'})
        self.assertEqual(self.qs.get(q['id'])['status'], 'open')
        self.assertNotIn('Send the mail', self.labs.open_decisions())

    def test_the_voice_shows_as_one_job_on_ai(self):
        days = [self.store.today()]
        self.assertIsNone(self.talk.aijob(days))
        self.talk.log('session', 0, 'desk')
        self.talk.say('Hello there.')
        item = self.talk.aijob(days)
        self.assertEqual((item['name'], item['runs']), ('Talk to Beacon (voice)', 1))
        self.assertGreater(item['cost'], 0)

    def test_pc_voice_goes_first_and_an_off_pc_is_skipped_for_a_minute(self):
        old = talk.PC_URL
        talk.PC_URL = 'http://pc.example/speak'
        try:
            calls = []

            def opener(req, timeout=30):
                if req.full_url == talk.PC_URL:
                    calls.append(req)
                    if len(calls) == 1:
                        return b'pc-mp3', 'audio/mpeg'
                    raise talk.VoiceError('offline')
                return self.http(req, timeout)
            self.talk.http = opener
            self.assertEqual(self.talk.say('Good morning.')[0], b'pc-mp3')
            self.assertEqual(self.talk.say('Another line.')[0], b'ID3fake-mp3')  # PC off: OpenAI at once
            self.assertEqual(self.talk.say('Third line.')[0], b'ID3fake-mp3')
            self.assertEqual(len(calls), 2)  # the third line did not wait on the dead PC
            with self.store.connect() as con:
                kinds = [r[0] for r in con.execute('SELECT kind FROM talk_usage ORDER BY at')]
            self.assertEqual(kinds.count('say-pc'), 1)
            self.assertNotIn('say-miss', kinds)
        finally:
            talk.PC_URL = old

    def test_own_voice_speaks_first_and_a_miss_falls_back_uncached_and_alarms(self):
        sent = []
        self.talk.send = sent.append
        old = talk.TTS_URL, os.environ.get('TALK_TTS_TOKEN')
        talk.TTS_URL, os.environ['TALK_TTS_TOKEN'] = 'https://voice.example/speak', 'tok'
        try:
            own = []

            def opener(req, timeout=30):
                if req.full_url == talk.TTS_URL:
                    own.append(req)
                    if len(own) > 1:  # below its bar from the second line on
                        raise talk.VoiceError('503: below the bar')
                    return b'own-mp3', 'audio/mpeg'
                return self.http(req, timeout)
            self.talk.http = opener
            self.assertEqual(self.talk.say('Good morning.')[0], b'own-mp3')
            self.assertEqual(own[0].get_header('Authorization'), 'Bearer tok')
            for _ in range(3):
                self.assertEqual(self.talk.say('The backup failed.')[0], b'ID3fake-mp3')  # OpenAI spoke it
            self.assertEqual(len(own), 4)  # never cached: each try asked the own voice again
            with self.store.connect() as con:
                kinds = [r[0] for r in con.execute('SELECT kind FROM talk_usage ORDER BY at')]
            self.assertEqual(kinds.count('say-own'), 1)
            self.assertEqual(kinds.count('say-miss'), 3)
            import time
            time.sleep(0.05)  # the alarm goes out on its own thread
            self.assertEqual(len(sent), 1)
            self.assertIn('own voice', sent[0])
            self.assertIn('missed 3 lines', self.talk.aijob([self.store.today()])['what'])
        finally:
            talk.TTS_URL = old[0]
            if old[1] is None:
                os.environ.pop('TALK_TTS_TOKEN', None)

    def test_a_cold_own_voice_never_holds_a_line_the_quick_voice_says_it_while_it_wakes(self):
        old = talk.TTS_URL, talk.TTS_WARM_URL, os.environ.get('TALK_TTS_TOKEN')
        talk.TTS_URL, talk.TTS_WARM_URL = 'https://voice.example/speak', 'https://voice.example/warm'
        os.environ['TALK_TTS_TOKEN'] = self.id()  # any value: never the box's own vault
        try:
            calls = []

            def opener(req, timeout=30):
                calls.append(req.full_url)
                if req.full_url in (talk.TTS_URL, talk.TTS_WARM_URL):
                    return b'own-mp3', 'audio/mpeg'
                return self.http(req, timeout)
            self.talk.http = opener
            self.assertEqual(self.talk.say('One sec.')[0], b'ID3fake-mp3')  # cold: the quick voice, at once
            import time
            for _ in range(500):  # the wake runs on its own thread
                if self.talk.ready:
                    break
                time.sleep(0.01)
            self.assertNotIn(talk.TTS_URL, calls)
            self.assertIn(talk.TTS_WARM_URL, calls)
            self.assertEqual(self.talk.say('Two decisions wait.')[0], b'own-mp3')  # awake: the own voice
        finally:
            talk.TTS_URL, talk.TTS_WARM_URL = old[:2]
            if old[2] is None:
                os.environ.pop('TALK_TTS_TOKEN', None)

    def test_labs_keeps_an_act_card_only_with_a_decision_and_never_saves_it(self):
        ok = Labs.clean_card({'type': 'act', 'decision': 4, 'answer': 'Round', 'text': 'Answer the logo question: Round.'})
        self.assertEqual((ok['decision'], ok['answer']), (4, 'Round'))
        self.assertIsNone(Labs.clean_card({'type': 'act', 'answer': 'Round', 'text': 'x'}))
        self.assertIsNone(Labs.clean_card({'type': 'act', 'decision': 4, 'text': 'x'}))


if __name__ == '__main__':
    unittest.main()
