"""Every GET route answers with the same status, with and without each service and login: pins the router."""
import http.client
import unittest
from unittest import mock

from test_tasks import LiveHub


class Stub:
    """Any service: every method answers an empty dict (a list where the view expects one)."""
    def __getattr__(self, name):
        if name == 'health':
            return {}
        if name == 'image':
            return lambda handler, *a: handler.send_json(200, {})
        if name in ('missed', 'feed', 'list', 'weekly', 'decided', 'views'):
            return lambda *a, **k: []
        return lambda *a, **k: {}


SERVICES = ('outcomes', 'studio', 'rank', 'aijobs', 'endings', 'machines', 'names', 'outreach', 'labs', 'rules', 'buildlog', 'knowledge')
AGENT = {'Authorization': 'Bearer tok'}
PATHS = [
    '/api/outcomes', '/api/team', '/api/live', '/api/rank', '/api/ai', '/api/endings', '/api/endings/missed',
    '/api/endings/bogus', '/api/meta', '/api/machines', '/api/freshness', '/api/names', '/api/outreach',
    '/api/day', '/api/range', '/api/range?from=2026-01-01&to=2026-01-02', '/api/search', '/api/session',
    '/api/session?session=x', '/api/knowledge/overview', '/mcp', '/mcp/x', '/api/labs/views', '/api/tasks',
    '/api/tasks/projects', '/api/tasks/auth', '/api/tasks/999', '/api/questions/file/nope.png', '/api/questions',
    '/api/rules', '/api/gate/approval', '/api/gate/log', '/api/buildlog', '/api/buildlog/game',
    '/api/buildlog/session', '/api/buildlog/zzz', '/buildlog/img/x.png', '/api/nothing', '/', '/outreach/doc?p=x.md',
]
# path -> status (no services: anonymous, token; every service: anonymous, token), read from the router
# before it became a table.
STATUS = {
    '/api/outcomes': (404, 404, 200, 200),
    '/api/team': (200, 200, 200, 200),
    '/api/live': (200, 200, 200, 200),
    '/api/rank': (404, 404, 200, 200),
    '/api/ai': (404, 404, 200, 200),
    '/api/endings': (404, 404, 200, 200),
    '/api/endings/missed': (404, 404, 200, 200),
    '/api/endings/bogus': (404, 404, 404, 404),
    '/api/meta': (200, 200, 200, 200),
    '/api/machines': (404, 404, 200, 200),
    '/api/freshness': (404, 404, 200, 200),
    '/api/names': (404, 404, 200, 200),
    '/api/outreach': (404, 404, 200, 200),
    '/api/day': (200, 200, 200, 200),
    '/api/range': (400, 400, 400, 400),
    '/api/range?from=2026-01-01&to=2026-01-02': (200, 200, 200, 200),
    '/api/search': (400, 400, 400, 400),
    '/api/session': (400, 400, 400, 400),
    '/api/session?session=x': (404, 404, 404, 404),
    '/api/knowledge/overview': (401, 404, 401, 200),
    '/mcp': (405, 405, 405, 405),
    '/mcp/x': (405, 405, 405, 405),
    '/api/labs/views': (401, 200, 401, 200),
    '/api/tasks': (200, 200, 200, 200),
    '/api/tasks/projects': (200, 200, 200, 200),
    '/api/tasks/auth': (401, 200, 401, 200),
    '/api/tasks/999': (404, 404, 404, 404),
    '/api/questions/file/nope.png': (401, 404, 401, 404),
    '/api/questions': (200, 200, 200, 200),
    '/api/rules': (200, 200, 200, 200),
    '/api/gate/approval': (200, 200, 200, 200),
    '/api/gate/log': (200, 200, 200, 200),
    '/api/buildlog': (401, 404, 401, 200),
    '/api/buildlog/game': (401, 404, 401, 400),
    '/api/buildlog/session': (401, 404, 401, 400),
    '/api/buildlog/zzz': (401, 404, 401, 404),
    '/buildlog/img/x.png': (401, 404, 401, 200),
    '/api/nothing': (404, 404, 404, 404),
    '/': (200, 200, 200, 200),
    '/outreach/doc?p=x.md': (404, 404, 404, 404),
}
EXPECTED = {(p, services, token): STATUS[p][2 * services + token] for p in PATHS for services in (False, True)
            for token in (False, True)}


class RoutesTest(LiveHub, unittest.TestCase):
    def status(self, path, headers):
        con = http.client.HTTPConnection(*self.httpd.server_address)
        try:
            con.request('GET', path, headers=headers)
            return con.getresponse().status
        finally:
            con.close()

    def statuses(self, services):
        stubs = {name: Stub() if services else None for name in SERVICES}
        with mock.patch.multiple(self.server.Handler, create=True, **stubs):
            return {(p, services, bool(h)): self.status(p, h) for p in PATHS for h in ({}, AGENT)}

    def test_every_route_keeps_its_status(self):
        got = {**self.statuses(False), **self.statuses(True)}
        self.assertEqual({k: v for k, v in got.items() if EXPECTED.get(k) != v},
                         {k: v for k, v in EXPECTED.items() if got.get(k) != v})


if __name__ == '__main__':
    unittest.main()
