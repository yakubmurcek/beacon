import os
os.environ['BEACON_CONFIG'] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.example.json')  # the example settings, never the box's own
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import tailnet_check as tc  # noqa: E402

NOW = 2_000_000_000


def svc(name, comment=None):
    return {'name': 'svc:' + name, 'comment': comment}


def serve(*names, port=3000, up=True, used=None):
    return [{'name': 'svc:' + n, 'backends': [f'http://127.0.0.1:{port}'], 'up': up, 'last_used': used} for n in names]


def run(services, hosts, machines, probes, policy=(), state=None, now=NOW, kept=()):
    devices = {'n1': 'devbox', 'n2': 'cloud'}
    return tc.build(services, hosts, devices, list(policy), {'machines': machines}, probes,
                    state if state is not None else {'names': {}}, now, kept)


class TailnetCheck(unittest.TestCase):
    def test_planted_duplicate_is_flagged_once(self):
        services = [svc('lighthouse', 'Lighthouse'), svc('lighthouse-copy', 'planted')]
        hosts = {s['name']: [{'nodeId': 'n1', 'configured': 'ready'}] for s in services}
        machines = [{'machine': 'devbox', 'serve': {'sampled': True, 'names': serve('lighthouse', 'lighthouse-copy')}}]
        names, flags = run(services, hosts, machines, {'lighthouse': True, 'lighthouse-copy': True})
        dup = [f for f in flags if f['kind'] == 'duplicate']
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]['names'], ['lighthouse', 'lighthouse-copy'])
        self.assertTrue(all(n['state'] == 'ok' for n in names))
        q = tc.question(dup[0], names)
        self.assertEqual(q['default'], 'Keep both')  # nothing goes without his answer
        self.assertEqual(q['source'], dup[0]['source'])
        # the same pair next run asks nothing new: the source key is stable
        self.assertEqual(run(services, hosts, machines, {'lighthouse': True, 'lighthouse-copy': True})[1][0]['source'],
                         dup[0]['source'])

    def test_dead_previews_grouped_and_policy_orphans(self):
        services = [svc('a', 'preview: /root/projects/a'), svc('b', 'preview: /root/projects/b'), svc('app', 'x')]
        hosts = {'svc:a': [{'nodeId': 'n1', 'configured': 'not-configured'}],
                 'svc:b': [{'nodeId': 'n1', 'configured': 'not-configured'}],
                 'svc:app': [{'nodeId': 'n1', 'configured': 'ready'}]}
        names, flags = run(services, hosts, [], {'a': False, 'b': False, 'app': True}, policy=['svc:app', 'svc:gone'])
        kinds = {f['kind']: f for f in flags}
        self.assertEqual(kinds['dead-preview']['names'], ['a', 'b'])
        self.assertEqual(kinds['dead-policy']['names'], ['gone'])
        self.assertNotIn('dead-app', kinds)
        self.assertEqual({n['name']: n['what'] for n in names}['a'], 'preview of a')

    def test_an_api_without_a_page_is_not_called_open(self):
        services = [svc('relay', 'relay'), svc('site', 'site')]
        hosts = {s['name']: [{'nodeId': 'n1', 'configured': 'ready'}] for s in services}
        names_ = [dict(serve('relay')[0], page=False), dict(serve('site', port=3001)[0], page=True)]
        machines = [{'machine': 'devbox', 'serve': {'sampled': True, 'names': names_}}]
        names, flags = run(services, hosts, machines, {})
        self.assertEqual({n['name']: n['state'] for n in names}, {'relay': 'api', 'site': 'ok'})
        self.assertFalse([f for f in flags if 'relay' in f['names']])  # works: nothing to ask

    def test_down_waits_half_an_hour(self):
        services = [svc('budget', 'cloud')]
        hosts = {'svc:budget': [{'nodeId': 'n2', 'configured': 'ready'}]}
        state = {'names': {}}
        _, flags = run(services, hosts, [], {'budget': False}, state=state)
        self.assertFalse([f for f in flags if f['kind'] == 'down'])
        _, flags = run(services, hosts, [], {'budget': False}, state=state, now=NOW + tc.DOWN_SEC)
        self.assertEqual([f['kind'] for f in flags], ['down'])
        _, flags = run(services, hosts, [], {'budget': True}, state=state, now=NOW + tc.DOWN_SEC + 60)
        self.assertEqual(flags, [])

    def test_leftover_approval_and_last_used(self):
        services = [svc('power', 'cloud: power')]
        hosts = {'svc:power': [{'nodeId': 'n1', 'configured': 'ready'}, {'nodeId': 'n2', 'configured': 'not-configured'}]}
        machines = [{'machine': 'devbox', 'serve': {'sampled': True, 'names': serve('power', used=NOW - 60)}}]
        names, flags = run(services, hosts, machines, {'power': True})
        self.assertEqual(flags[0]['kind'], 'leftover')
        self.assertEqual(flags[0]['pairs'], ['power@cloud'])
        self.assertEqual(names[0]['last_used'], NOW - 60)
        self.assertTrue(names[0]['used_known'])

    def test_idle_preview(self):
        services = [svc('old', 'preview: /root/projects/old')]
        hosts = {'svc:old': [{'nodeId': 'n1', 'configured': 'ready'}]}
        state = {'names': {'old': {'first_seen': NOW - tc.IDLE_SEC - 1}}}
        names, flags = run(services, hosts, [], {'old': True}, state=state)
        self.assertEqual(names[0]['state'], 'idle')
        self.assertEqual(flags[0]['kind'], 'idle-preview')

    def test_kept_names_leave_the_grouped_card(self):
        services = [svc('a', 'preview: /root/projects/a'), svc('b', 'preview: /root/projects/b')]
        hosts = {s['name']: [{'nodeId': 'n1', 'configured': 'not-configured'}] for s in services}
        state = {'asked': {'x': {'qid': 7, 'kind': 'dead-preview', 'names': ['a']},
                           'y': {'qid': 8, 'kind': 'duplicate', 'names': ['b', 'c']}}}
        kept = tc.kept_names(state, {7: 'Keep', 8: 'Keep one, remove the rest'})
        self.assertEqual(kept, {'a'})
        _, flags = run(services, hosts, [], {'a': False, 'b': False}, kept=kept)
        self.assertEqual(flags[0]['names'], ['b'])

    def test_a_replaced_card_is_withdrawn_after_three_runs(self):
        state = {'names': {}}
        open_qs = [{'id': 1}]
        found = [{'source': 'new', 'question_id': 2}]
        self.assertEqual(tc.stale(open_qs, found, state), [])
        self.assertEqual(tc.stale(open_qs, found, state), [])
        self.assertEqual([q['id'] for q in tc.stale(open_qs, found, state)], [1])
        state = {'names': {}}  # back in one run resets the count
        tc.stale(open_qs, found, state)
        tc.stale(open_qs, [{'source': 'old', 'question_id': 1}], state)
        self.assertEqual(state['missing'], {})

    def test_ask_links_decides_and_reasks_withdrawn(self):
        f = lambda src: {'kind': 'dead-preview', 'names': ['a'], 'source': src, 'text': 'http://a: x'}
        names = [{'name': 'a', 'what': None, 'why': None, 'kind': 'preview', 'hosts': [], 'in_policy': False}]
        state, posted = {}, []

        def post(body):
            posted.append(body['source'])
            if body['source'] == 'gone':  # the hub hands back the withdrawn question for a used source
                return {'id': 1, 'status': 'withdrawn'}
            return {'id': 10 + len(posted), 'status': 'open'}
        found = [f('s1'), f('gone')]
        self.assertEqual(tc.ask(found, names, state, set(), {}, post, NOW), 2)
        self.assertEqual(posted, ['s1', 'gone', f'gone:{NOW}'])
        self.assertEqual(found[1]['question_id'], 13)
        # next run: s1 open -> linked, gone's card answered -> decided, nothing posted
        posted.clear()
        found = [f('s1'), f('gone')]
        self.assertEqual(tc.ask(found, names, state, {11}, {13: 'Keep'}, post, NOW), 0)
        self.assertEqual(found[1]['decided'], 'Keep')
        # s1's card withdrawn by the hub later: asked again under a fresh source
        found = [f('s1')]
        tc.ask(found, names, state, set(), {}, post, NOW + 5)
        self.assertEqual(posted, [f's1:{NOW + 5}'])

    def test_sources_stay_short(self):
        services = [svc(f'preview-name-number-{i}', f'preview: /root/projects/p{i}') for i in range(40)]
        hosts = {s['name']: [] for s in services}
        _, flags = run(services, hosts, [], {})
        self.assertTrue(all(len(f['source']) < 60 for f in flags))

    def test_what_comes_from_the_box(self):
        services = [svc('budget', 'cloud: budget')]
        hosts = {'svc:budget': [{'nodeId': 'n1', 'configured': 'ready'}]}
        srv = serve('budget')
        srv[0]['what'] = 'budget dashboard'
        names, _ = run(services, hosts, [{'machine': 'devbox', 'serve': {'sampled': True, 'names': srv}}], {})
        self.assertEqual(names[0]['what'], 'budget dashboard')

    def test_name_on_a_switched_off_box_is_not_asked(self):
        services = [svc('rwby', 'wall')]
        hosts = {'svc:rwby': [{'nodeId': 'n1', 'configured': 'ready'}]}
        state = {'names': {'rwby': {'first_seen': 0, 'down_since': NOW - 86400}}}
        machines = [{'machine': 'devbox', 'state': 'bad', 'tailscale': {'online': False}}]
        names, flags = run(services, hosts, machines, {'rwby': False}, state=state)
        self.assertEqual(names[0]['state'], 'off')
        self.assertEqual(names[0]['why'], 'devbox is switched off')
        self.assertEqual(flags, [])
        self.assertNotIn('down_since', state['names']['rwby'])
        # the box is back but the app still does not open: down again, counted afresh
        machines[0]['tailscale']['online'] = True
        names, flags = run(services, hosts, machines, {'rwby': False}, state=state)
        self.assertEqual(names[0]['state'], 'down')
        self.assertEqual(state['names']['rwby']['down_since'], NOW)


if __name__ == '__main__':
    unittest.main()
