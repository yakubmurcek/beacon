"""Every POST route answers the same, with and without each service, for each caller and body: pins the router.

tests/fixtures/post-routes.json holds (status, error) per route, recorded from do_POST before it became a route
table with one body reader. `BEACON_RECORD=1` rewrites it; any other difference is a change in behaviour.
"""
import http.client
import json
import os
import unittest
from unittest import mock

from test_routes import SERVICES, Stub
from test_tasks import ROOT, LiveHub

FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'post-routes.json')
PATHS = [
    '/api/tasks', '/api/tasks/1', '/api/tasks/1/comments', '/api/tasks/1/sessions', '/api/tasks/x',
    '/api/outreach/move', '/api/questions', '/api/questions/1', '/api/questions/1/answer', '/api/questions/1/reopen',
    '/api/questions/1/withdraw', '/api/questions/1/claim', '/api/questions/1/release', '/api/questions/1/waiting',
    '/api/questions/1/handoff', '/api/questions/1/see', '/api/questions/1/flag', '/api/questions/1/applied',
    '/api/questions/x', '/api/feedback', '/api/rules/sweep', '/api/rules/1', '/api/rules/uses/1/undo', '/api/rules/x',
    '/api/endings', '/api/endings/clear', '/api/endings/unclear', '/api/endings/1/clear', '/api/endings/1/unclear',
    '/api/endings/1/act', '/api/endings/x', '/api/labs/ask', '/api/labs/forget', '/api/labs/x', '/api/talk/hear',
    '/api/talk/say', '/api/talk/brief', '/api/talk/act', '/api/talk/warm', '/api/talk/played', '/api/talk/start',
    '/api/talk/x', '/mcp', '/mcp/short', '/mcp/' + 'k' * 40, '/api/gate/log', '/api/names', '/api/rank/ingest',
    '/api/ingest', '/api/notify', '/api/notify/read', '/api/notify/subscribe', '/api/notify/test', '/api/nothing', '/',
]
CALLERS = {'anonymous': {}, 'agent': {'Authorization': 'Bearer tok'}, 'collector': {'Authorization': 'Bearer usage'},
           'owner': {'X-Forwarded-For': LiveHub.OWNER_IP}, 'owner cross-site': {'X-Forwarded-For': LiveHub.OWNER_IP,
                                                                               'Sec-Fetch-Site': 'cross-site'}}
BODIES = {'object': b'{"machine": "box", "events": []}', 'list': b'[]', 'not json': b'not json', 'empty': b''}


class Talk(Stub):
    def say(self, *a):
        return b'', 'audio/mpeg'


class Mcp(Stub):
    def handle(self, msg):
        return {'jsonrpc': '2.0', 'id': 1, 'result': {}}


class PostRoutesTest(LiveHub, unittest.TestCase):
    maxDiff = None

    def answer(self, path, headers, body):
        con = http.client.HTTPConnection(*self.httpd.server_address, timeout=10)
        try:
            con.request('POST', path, body=body, headers={'Content-Type': 'application/json', **headers})
            res = con.getresponse()
            raw = res.read()
        except http.client.RemoteDisconnected:  # the handler crashed before answering
            return [0, 'no answer']
        finally:
            con.close()
        try:
            error = json.loads(raw).get('error') if raw[:1] == b'{' else None
        except ValueError:
            error = None
        return [res.status, error if isinstance(error, (str, type(None))) else error.get('message')]

    def answers(self, services):
        stubs = {name: Stub() if services else None for name in (*SERVICES, 'feedback', 'notify', 'webpush')}
        if services:
            stubs.update(talk=Talk(), mcp=Mcp())
        with mock.patch.multiple(self.server.Handler, create=True, **stubs), \
                mock.patch.multiple(self.server, TOKEN='usage', MCP_KEY='k' * 40):
            return {f'{path} | {"services" if services else "bare"} | {who} | {kind}': self.answer(path, headers, body)
                    for path in PATHS for who, headers in CALLERS.items() for kind, body in BODIES.items()}

    def test_every_post_route_keeps_its_answer(self):
        got = {**self.answers(False), **self.answers(True)}
        if os.environ.get('BEACON_RECORD'):
            with open(FIXTURE, 'w') as f:
                json.dump(got, f, ensure_ascii=False, indent=0, sort_keys=True)
                f.write('\n')
        with open(FIXTURE) as f:
            want = json.load(f)
        self.assertEqual({k: v for k, v in got.items() if want.get(k) != v},
                         {k: v for k, v in want.items() if got.get(k) != v})


if __name__ == '__main__':
    unittest.main()
