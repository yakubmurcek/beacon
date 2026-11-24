"""Notices (hub/notify.py): every notice reaches the inbox, only a handful a day reach the phone."""
import os
import random
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tasks import ROOT

sys.path.insert(0, os.path.join(ROOT, 'hub'))
import notify
from notify import Notify, from_alarm
from store import Store

TZ = 'Europe/Prague'


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo(TZ)).timestamp()


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sent = []
        self.ok = True
        self.n = Notify(Store(os.path.join(self.tmp.name, 'u.db')), self.send, TZ, 'http://hub')
        self.n.inline = True

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, kind, title, body, link):
        self.sent.append((kind, title, body, link))
        return self.ok, 'test', None if self.ok else 'down'

    def test_urgent_goes_at_once_and_opens_its_link(self):
        x = self.n.post({'level': 'urgent', 'title': '🔴 devbox down', 'link': '/machines'}, now=at(20, 13))
        self.assertEqual(x['state'], 'pushed')
        self.assertEqual(self.sent, [('urgent', '🔴 devbox down', '', 'http://hub/machines')])

    def test_night_holds_even_urgent_for_the_morning(self):
        self.n.post({'level': 'urgent', 'title': '🔴 login dead'}, now=at(20, 3))
        self.n.post({'level': 'urgent', 'title': '🔴 box down'}, now=at(20, 4))
        self.assertEqual(self.sent, [])
        self.n.flush(at(20, 8))
        self.assertEqual([(k, t) for k, t, *_ in self.sent], [('urgent', '🔴 2 updates')])

    def test_normal_waits_for_a_digest_in_waking_hours(self):
        self.n.post({'title': '✍️ Draft written: Acme', 'link': '/outreach#leads?l=acme'}, now=at(20, 9))
        self.assertIsNone(self.n.flush(at(20, 9, 5)))  # a burst gets time to gather
        got = self.n.flush(at(20, 9, 11))
        self.assertEqual(got['count'], 1)
        self.assertEqual(self.sent[0][1:], ('✍️ Draft written: Acme', '', 'http://hub/outreach#leads?l=acme'))
        self.sent.clear()
        self.n.post({'title': 'late news'}, now=at(20, 23))
        self.assertIsNone(self.n.flush(at(20, 23, 30)))  # night: held for the morning
        self.n.flush(at(21, 8, 1))
        self.assertEqual(len(self.sent), 1)

    def test_a_burst_is_one_digest(self):
        for i in range(7):
            self.n.post({'title': f'job {i} failed'}, now=at(20, 10, i))
        self.n.flush(at(20, 10, 20))
        self.assertEqual(len(self.sent), 1)
        kind, title, body, link = self.sent[0]
        self.assertEqual((kind, title, link), ('digest', '📬 7 updates', 'http://hub/inbox'))
        self.assertIn('and 2 more', body)

    def test_repeats_and_keys_count_once(self):
        a = self.n.post({'title': 'disk full', 'key': 'disk', 'every': 86400}, now=at(20, 10))
        b = self.n.post({'title': 'disk still full', 'key': 'disk', 'every': 86400}, now=at(20, 12))
        c = self.n.post({'title': 'same words'}, now=at(20, 10))
        d = self.n.post({'title': 'same words'}, now=at(20, 11))
        self.assertEqual((b['id'], b['repeats']), (a['id'], 1))
        self.assertEqual(d['id'], c['id'])

    def test_fixed_withdraws_the_alarm_and_never_pings(self):
        self.n.post({'title': 'job x failed', 'key': 'job:x'}, now=at(20, 10))
        fixed = self.n.post({'title': '🟢 fixed: job x', 'key': 'job:x:ok', 'level': 'urgent'}, now=at(20, 10, 5))
        self.assertEqual((fixed['level'], fixed['state']), ('inbox', 'kept'))
        self.assertIsNone(self.n.flush(at(20, 12)))
        self.assertEqual(self.sent, [])

    def test_caps_hold_the_rest_in_the_inbox(self):
        for h in range(0, 24):
            self.n.post({'level': 'urgent', 'title': f'🔴 thing {h}'}, now=at(20, h))
            self.n.post({'title': f'news {h}'}, now=at(20, h, 30))
            for m in range(0, 60, 5):
                self.n.flush(at(20, h, m))
        self.assertLessEqual(len(self.sent), notify.DAY_CAP)
        self.assertLessEqual(sum(k == 'urgent' for k, *_ in self.sent), notify.URGENT_CAP)
        v = self.n.view(now=at(20, 23, 59))
        self.assertEqual(v['unread'], 48)
        self.assertEqual(v['days'][-1]['notices'], 48)
        self.assertEqual(v['days'][-1]['pushes'], len(self.sent))

    def test_a_noisy_week_stays_a_handful_a_day(self):
        """About the old alarm volume (130 lines on the worst day, most of them buzzing) replayed through the rules."""
        rnd = random.Random(7)
        for day in range(16, 23):
            for _ in range(130):
                h, m = rnd.randrange(24), rnd.randrange(60)
                text = rnd.choice(['🔴 job failed', '❄️ 89 new players', '🟢 fixed: job', '🔴 box down', 'news'])
                self.n.post(from_alarm(f'{text} {rnd.randrange(40)}', quiet=rnd.random() < .4), now=at(day, h, m))
            for h in range(24):
                for m in range(0, 60, 2):
                    self.n.flush(at(day, h, m))
        for d in self.n.view(now=at(22, 23, 59))['days']:
            self.assertLessEqual(d['pushes'], notify.DAY_CAP, d)

    def test_a_failed_push_is_tried_again(self):
        self.ok = False
        self.n.post({'level': 'urgent', 'title': '🔴 login dead'}, now=at(20, 10))
        self.ok = True
        self.n.flush(at(20, 10, 16))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.n.view(now=at(20, 11))['notices'][0]['state'], 'pushed')

    def test_the_same_trouble_buzzes_once_a_day(self):
        self.n.post({'level': 'urgent', 'title': '🔴 Claude login needs a refresh on devbox'}, now=at(20, 9))
        again = self.n.post({'level': 'urgent', 'title': '🔴 Claude login needs a refresh on cloud, devbox'},
                            now=at(20, 16))
        self.assertEqual(again['level'], 'normal')  # its urgent slot stays free for a new problem
        new = self.n.post({'level': 'urgent', 'title': '🔴 cloud is down'}, now=at(20, 16, 1))
        self.assertEqual(new['state'], 'pushed')

    def test_his_own_tap_comes_back_alone_and_soon(self):
        self.n.post({'title': 'news 1'}, now=at(20, 9))
        self.n.flush(at(20, 9, 15))  # a digest just went
        self.sent.clear()
        x = self.n.post({'title': '✅ Write the draft: Acme', 'link': '/outreach#leads?l=acme', 'alone': True},
                        now=at(20, 9, 30))
        self.assertEqual(x['state'], 'pushed')
        self.assertEqual(self.sent, [('alone', '✅ Write the draft: Acme', '', 'http://hub/outreach#leads?l=acme')])

    def test_a_slow_push_never_holds_up_a_sender(self):
        import threading
        import time as clock
        gate = threading.Event()
        self.n.inline = False
        self.n.send = lambda *a: (gate.wait(5), (True, 'test', None))[1]
        self.n.post({'level': 'urgent', 'title': '🔴 box down'})
        start = clock.monotonic()
        self.n.post({'title': 'news while it sends'})
        self.assertLess(clock.monotonic() - start, 1)
        gate.set()
        clock.sleep(.05)
        with self.n.sending:  # the push thread is done before the database goes
            pass

    def test_read(self):
        a = self.n.post({'title': 'a'}, now=at(20, 10))
        self.n.post({'title': 'b'}, now=at(20, 10))
        self.assertEqual(self.n.read([a['id']])['read'], 1)
        self.assertEqual(self.n.view(now=at(20, 11))['unread'], 1)
        self.n.read(everything=True)
        self.assertEqual(self.n.view(now=at(20, 11))['unread'], 0)

    def test_alarm_levels(self):
        self.assertEqual(from_alarm('🔴 devbox down')['level'], 'urgent')
        self.assertEqual(from_alarm('🔴 job failed', quiet=True)['level'], 'inbox')
        self.assertEqual(from_alarm('❄️ 89 new players')['level'], 'normal')
        with self.assertRaises(ValueError):
            self.n.post({'title': ' '})



class OutreachDoneTest(unittest.TestCase):
    """A tapped Outreach move's task, closed: one normal notice that opens the lead's card."""

    def test_done_move_notifies_with_the_lead_link(self):
        import server
        from tasks import Tasks
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(os.path.join(tmp.name, 'u.db'))
        tasks, seen = Tasks(store), []
        n = Notify(store, None, TZ)
        tasks.on_close = server.outreach_done(tasks, n, seen.append)
        body = 'Write it.\n\nOutreach lead: coach / Urban Gym Brno\nBeacon move: draft\n'
        t = tasks.create({'title': 'Write the draft: Urban Gym Brno', 'body': body, 'project': 'coach'})
        other = tasks.create({'title': 'Unrelated', 'project': 'coach'})
        tasks.update(t['id'], {'status': 'done'})
        tasks.update(other['id'], {'status': 'done'})
        self.assertEqual(seen, [t['id'], other['id']])  # the slot's earlier owner still runs
        got = n.view()['notices']
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]['title'], got[0]['level'], got[0]['link']),
                         ('✅ Write the draft: Urban Gym Brno', 'normal', '/outreach#leads?l=coach-urban-gym-brno'))
        self.assertEqual(server.outreach_link('Outreach lead: coach / Café Ýes\n'), '/outreach#leads?l=coach-cafe-yes')


if __name__ == '__main__':
    unittest.main()
