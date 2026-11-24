"""Machines and background jobs: what the hub shows at /api/machines and when it tells him."""
import json
import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'collector'), os.path.join(ROOT, 'hub')]

import host  # noqa: E402
import machines  # noqa: E402
from store import Store  # noqa: E402


def job(name, state='ok', **kw):
    return dict({'id': 'systemd:' + name, 'name': name, 'kind': 'timer', 'desc': None, 'state': state,
                 'last_run': None, 'next_run': None, 'code': None, 'alarm': False, 'alarm_failed': False,
                 'why': None}, **kw)


class Box(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.dir.name, 'u.db'), 'Europe/Prague')
        self.m = machines.Machines(self.store, alarms=False)
        self.m._ts = (time.time() + 10 ** 6, {})  # no tailscale in tests
        self.sent = []

    def tearDown(self):
        self.dir.cleanup()

    def send(self, text, key, quiet=False):
        self.sent.append((key, text))
        return True

    def push(self, name, jobs, at=None):
        at = at or time.time()
        self.m.save(name, {'stats': {'disks': []}, 'versions': {}, 'jobs': jobs, 'jobs_at': at})
        with self.store.connect() as con:
            con.execute('INSERT OR REPLACE INTO live(machine, seen_at, sessions) VALUES (?, ?, ?)', (name, at, '[]'))

    def age(self, table, sec):
        """Everything stored happened `sec` earlier."""
        with self.store.connect() as con:
            if table == 'jobs_seen':
                con.execute('UPDATE jobs_seen SET first_seen = first_seen - ?, last_seen = last_seen - ?, '
                            'bad_since = bad_since - ?', (sec, sec, sec))
            else:
                con.execute(f'UPDATE {table} SET seen_at = seen_at - ?', (sec,))

    def baseline(self):
        self.m.tick(send=self.send)
        self.sent.clear()

    def keys(self):
        return [k for k, _ in self.sent]


class StuckUpdate(Box):
    def problems(self, collector, lacks_since):
        machines._MISSING.clear()
        old = machines.missing_since
        machines.missing_since = lambda c, now: lacks_since if c == collector else None
        self.addCleanup(setattr, machines, 'missing_since', old)
        self.m.save('gamer', {'stats': {'disks': []}, 'versions': {'collector': collector}, 'jobs': [],
                                'jobs_at': time.time()})
        with self.store.connect() as con:
            con.execute('INSERT OR REPLACE INTO live(machine, seen_at, sessions) VALUES (?, ?, ?)',
                        ('gamer', time.time(), '[]'))
        return next(m for m in self.m.view()['machines'] if m['machine'] == 'gamer')['problems']

    def test_a_box_missing_hours_old_code_is_told(self):
        self.assertIn('its collector stopped updating 6 h ago', self.problems('2e4825e 2026-11-22', time.time() - 6 * 3600))

    def test_normal_lag_and_current_code_are_quiet(self):
        self.assertEqual(self.problems('2e4825e 2026-11-22', time.time() - 3600), [])
        self.assertEqual(self.problems('86847d9 2026-11-22', None), [])

    def test_the_real_git_answer(self):
        head = machines.subprocess.run(['git', '-C', machines._HERE, 'rev-parse', 'HEAD'], capture_output=True,
                                       text=True).stdout.strip()
        machines._MISSING.clear()
        self.assertIsNone(machines.missing_since(head[:7] + ' 2026-11-22', time.time()))
        self.assertIsNone(machines.missing_since('not-a-hash', time.time()))


class Alarms(Box):
    def test_first_pass_is_one_summary_of_what_is_already_broken(self):
        self.push('devbox', [job('khazix', 'failed', why='HTTP 503'), job('disk-watch')])
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['_baseline'])
        self.assertIn('khazix keeps failing', self.sent[0][1])
        self.sent.clear()
        self.m.tick(send=self.send)
        self.assertEqual(self.sent, [])  # already told

    def test_failed_job_waits_out_the_blip_grace(self):
        self.baseline()
        self.push('devbox', [job('sync', 'failed')])
        self.m.tick(send=self.send)
        self.assertEqual(self.sent, [])
        self.age('jobs_seen', machines.FAIL_GRACE_SEC + 5)
        self.push('devbox', [job('sync', 'failed', why='could not resolve host')])
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['job:devbox:systemd:sync'])
        self.assertTrue(self.sent[0][1].startswith('🔴 devbox: sync keeps failing'))

    def test_a_package_job_is_shown_never_alarmed(self):
        self.baseline()
        self.push('devbox', [job('openipmi', 'failed', own=False)])
        self.age('jobs_seen', 3600)
        self.push('devbox', [job('openipmi', 'failed', own=False)])
        self.m.tick(send=self.send)
        self.assertEqual(self.sent, [])

    def failing_then(self, *later):
        """A job told as failing, then the scans in `later`; returns what was sent after the 🔴."""
        self.baseline()
        self.push('devbox', [job('sync', 'failed')], at=time.time() - 3600)
        self.age('jobs_seen', 3600)
        self.push('devbox', [job('sync', 'failed')])
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['job:devbox:systemd:sync'])
        self.sent.clear()
        for j in later:
            self.push('devbox', [j], at=time.time() + len(self.sent) + 1)
            self.m.tick(send=self.send)
        return self.keys()

    def test_a_scan_that_catches_the_next_run_is_not_fixed(self):
        self.assertEqual(self.failing_then(job('sync', 'running'), job('sync', 'failed')), [])

    def test_a_reboot_forgetting_the_failure_is_not_fixed(self):
        self.assertEqual(self.failing_then(job('sync', 'ok', last_run=time.time() - 7200)), [])

    def test_a_good_run_after_it_broke_is_fixed(self):
        self.assertEqual(self.failing_then(job('sync', 'ok', last_run=time.time() + 5)),
                         ['job:devbox:systemd:sync:ok'])

    def test_hung_timer_run(self):
        self.baseline()
        now = time.time()
        self.push('devbox', [job('backup', 'running', started=now - 7 * 3600)], at=now)
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['job:devbox:systemd:backup'])
        self.assertIn('hung', self.sent[0][1])

    def test_a_failure_is_sent_whatever_its_own_alarm_did(self):
        """2026-11-21: alarm@ failed on DNS at boot, so the failure never reached him; and unit-alarm
        exits 0 when it skips a send, so even a 'working' hook is no proof he heard."""
        self.baseline()
        self.push('devbox', [job('tasks-sweep', 'failed', alarm=True, alarm_failed=True)])
        self.age('jobs_seen', 3600)
        self.push('devbox', [job('tasks-sweep', 'failed', alarm=True, alarm_failed=True)])
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['job:devbox:systemd:tasks-sweep'])

    def test_overdue_job(self):
        self.baseline()
        now = time.time()
        self.push('devbox', [job('backup', next_run=now - 3600)], at=now)
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['job:devbox:systemd:backup'])
        self.assertIn('did not run', self.sent[0][1])

    def test_a_vanished_app_task_is_not_news(self):
        self.baseline()
        self.push('gamer', [job('NVIDIA SelfUpdate_{B2FE}', own=False), job('x')])
        self.age('jobs_seen', 300)
        self.push('gamer', [job('x')])
        self.m.tick(send=self.send)
        self.assertEqual(self.sent, [])

    def test_a_job_that_vanishes_is_told_once(self):
        self.baseline()
        self.push('devbox', [job('backup'), job('sync')])
        self.age('jobs_seen', 300)
        self.push('devbox', [job('sync')])
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['job:devbox:systemd:backup:gone'])
        self.sent.clear()
        with self.store.connect() as con:  # a day later: still gone, not told again
            con.execute('UPDATE alarms SET sent_at = sent_at - 90000')
        self.m.tick(send=self.send)
        self.assertEqual(self.sent, [])

    def test_a_failed_one_off_task_is_told_once_out_loud(self):
        """2026-11-24: a training run (a one-shot scheduled task) crashed overnight and nobody heard."""
        quiet = []
        send = lambda text, key, quiet_=False, **kw: quiet.append(kw.get('quiet', quiet_)) or self.send(text, key)
        self.baseline()
        train = job('Ozpin voice train', 'failed', kind='schtask', id='schtask:\\Ozpin voice train')
        self.push('gamer', [train])
        self.age('jobs_seen', 3600)
        self.push('gamer', [train])
        self.m.tick(send=send)
        self.assertEqual(self.keys(), ['job:gamer:schtask:\\Ozpin voice train:once'])
        self.assertIn('Ozpin voice train failed', self.sent[0][1])
        self.assertEqual(quiet, [False])
        self.sent.clear()
        with self.store.connect() as con:  # a day later: still failed, not told again
            con.execute('UPDATE alarms SET sent_at = sent_at - 90000')
        self.m.tick(send=send)
        self.assertEqual(self.sent, [])

    def test_always_on_machine_that_stops_reporting(self):
        self.push('devbox', [job('sync')])
        self.baseline()
        self.age('live', machines.SILENT_SEC + 60)
        self.age('hosts', machines.SILENT_SEC + 60)
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['machine:devbox'])
        self.assertTrue(self.sent[0][1].startswith('🔴 devbox stopped reporting'))

    def test_sleeping_mac_is_asleep_not_an_alarm(self):
        self.push('laptop', [job('x', 'failed')])
        self.baseline()
        self.age('live', 7200)
        self.age('hosts', 7200)
        self.m.tick(send=self.send)
        self.assertEqual(self.sent, [])
        mac = next(x for x in self.m.view()['machines'] if x['machine'] == 'laptop')
        self.assertEqual(mac['state'], 'asleep')

    def test_fixed_gets_a_quiet_green_line(self):
        self.baseline()
        now = time.time()
        self.push('devbox', [job('backup', next_run=now - 3600)], at=now)
        self.m.tick(send=self.send)
        self.sent.clear()
        self.push('devbox', [job('backup', next_run=now + 3600, last_run=now + 1)])
        self.m.tick(send=self.send)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0][1].startswith('🟢 fixed: devbox: backup'))

    def test_failed_send_is_retried(self):
        self.baseline()
        now = time.time()
        self.push('devbox', [job('backup', next_run=now - 3600)], at=now)
        self.m.tick(send=lambda *a, **k: False)  # DNS down
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['job:devbox:systemd:backup'])


    def test_a_frozen_job_scan_is_told(self):
        self.push('devbox', [job('sync')])
        self.baseline()
        self.age('jobs_seen', machines.STALE_SCAN_SEC + 60)
        with self.store.connect() as con:  # still pushing live, the job list just stopped changing
            con.execute('UPDATE live SET seen_at = ?', (time.time(),))
            con.execute('UPDATE hosts SET seen_at = ?', (time.time(),))
        self.m.tick(send=self.send)
        self.assertEqual(self.keys(), ['machine:devbox:scan'])

    def test_send_health_is_tracked(self):
        self.m.health['send_failing_since'] = None
        wrapped = self.m._tracked(lambda *a, **k: False)
        wrapped('x', 'k')
        self.assertIsNotNone(self.m.health['send_failing_since'])
        self.m._tracked(lambda *a, **k: True)('x', 'k')
        self.assertIsNone(self.m.health['send_failing_since'])


class View(Box):
    def test_failed_first_and_machine_state(self):
        self.push('devbox', [job('b'), job('a', 'failed')])
        devbox = next(x for x in self.m.view()['machines'] if x['machine'] == 'devbox')
        self.assertEqual([j['name'] for j in devbox['jobs']], ['a', 'b'])
        self.assertEqual(devbox['state'], 'bad')
        self.assertTrue(devbox['always_on'])

    def test_a_package_job_does_not_color_the_machine(self):
        self.push('devbox', [job('apport-autoreport', 'failed', own=False), job('a')])
        devbox = next(x for x in self.m.view()['machines'] if x['machine'] == 'devbox')
        self.assertEqual((devbox['state'], devbox['problems']), ('ok', []))

    def test_always_on_box_without_data_is_bad(self):
        pi = next(x for x in self.m.view()['machines'] if x['machine'] == 'pi')
        self.assertEqual((pi['state'], pi['problems']), ('bad', ['offline']))


class Collector(unittest.TestCase):
    def test_report_has_stats_and_caches_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'c.json')
            calls = []
            real = host.scan_jobs
            host.scan_jobs = lambda: calls.append(1) or [job('x')]
            try:
                r = host.report(path, now=1000)
                host.report(path, now=1010)
            finally:
                host.scan_jobs = real
            self.assertEqual(len(calls), 1)
            self.assertEqual(r['jobs'][0]['name'], 'x')
            self.assertIn('disks', r['stats'])

    def test_a_failed_one_off_windows_task_alarms_an_ended_one_does_not(self):
        rows = [{'name': '\\Train', 'state': 'Ready', 'code': 1, 'next': None, 'run': 'conhost.exe --headless powershell x'},
                {'name': '\\Killed', 'state': 'Ready', 'code': 0xC000013A, 'next': None, 'run': 'conhost.exe --headless y'},
                {'name': '\\Cut', 'state': 'Ready', 'code': 0x41306, 'next': None, 'last': time.time() - 3600,
                 'run': 'wscript.exe //B C:\\Users\\u\\.local\\bin\\run-hidden.vbs python train.py'},
                {'name': '\\Updater', 'state': 'Ready', 'code': 1, 'next': None, 'run': 'C:\\Program Files\\x.exe'}]
        real = host.run
        host.run = lambda *a, **k: json.dumps(rows)
        try:
            got = {j['name']: j['state'] for j in host.windows_jobs()}
        finally:
            host.run = real
        self.assertEqual(got, {'Train': 'failed', 'Killed': 'unknown', 'Cut': 'failed', 'Updater': 'unknown'})

    def test_service_state(self):
        self.assertEqual(host.service_state({'ActiveState': 'failed', 'Result': 'exit-code'}), 'failed')
        self.assertEqual(host.service_state({'ActiveState': 'activating', 'SubState': 'auto-restart'}), 'failed')
        self.assertEqual(host.service_state({'ActiveState': 'activating', 'SubState': 'start'}), 'running')
        self.assertEqual(host.service_state({'ActiveState': 'inactive', 'Result': 'success',
                                             'ExecMainStartTimestamp': '@1795348326'}), 'ok')
        self.assertEqual(host.service_state({'ActiveState': 'inactive', 'Result': 'success'}), 'never')

    def test_node_exporter_parse(self):
        text = ('node_memory_MemTotal_bytes 1e9\nnode_memory_MemAvailable_bytes 4e8\nnode_load1 0.5\n'
                'node_filesystem_size_bytes{device="/dev/root",fstype="ext4",mountpoint="/"} 3.2e10\n'
                'node_filesystem_avail_bytes{device="/dev/root",fstype="ext4",mountpoint="/"} 1.6e10\n'
                'node_thermal_zone_temp{type="cpu-thermal",zone="0"} 47.2\n')
        r = machines.parse_node(text)
        self.assertEqual((r['mem_total'], r['temp_c'], r['disks'][0]['free']), (1e9, 47, 1.6e10))


if __name__ == '__main__':
    unittest.main()
