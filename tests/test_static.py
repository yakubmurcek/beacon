"""Pages, files and short links: what serve_static answers for each kind of path, pinned before it was split up."""
import gzip
import http.client
import os
import unittest

from test_tasks import ROOT, LiveHub

HEADERS = ('Location', 'Content-Type', 'Cache-Control', 'Content-Encoding', 'Vary')
# (path, request headers) -> (status, the headers above that it sets, whether it sends an ETag)
CASES = [
    ('/questions', {}, (301, {'Location': '/decisions'}, False)),
    ('/outcomes', {}, (302, {'Location': '/team'}, False)),
    ('/talk', {}, (302, {'Location': '/labs?talk'}, False)),
    ('/system', {}, (302, {'Location': '/usage'}, False)),
    ('/home', {}, (302, {'Location': '/power'}, False)),
    ('/decisions/', {}, (302, {'Location': '/decisions'}, False)),
    ('/machines/?a=1', {}, (302, {'Location': '/machines?a=1'}, False)),
    ('/cli/tasks', {}, (200, {'Content-Type': 'text/x-python; charset=utf-8', 'Cache-Control': 'no-cache'}, False)),
    ('/cli/config.example.json', {}, (200, {'Content-Type': 'application/json', 'Cache-Control': 'no-cache'}, False)),
    ('/config.js', {}, (200, {'Content-Type': 'application/javascript; charset=utf-8', 'Cache-Control': 'no-cache'}, False)),
    ('/', {}, (200, {'Content-Type': 'text/html', 'Cache-Control': 'no-cache', 'Vary': 'Accept-Encoding'}, True)),
    ('/', {'Host': 'beacon'}, (200, {'Content-Type': 'text/html', 'Cache-Control': 'no-cache', 'Vary': 'Accept-Encoding'}, True)),
    ('/new', {'Host': 'beacon.example.ts.net'}, (200, {'Content-Type': 'text/html', 'Cache-Control': 'no-cache',
                                                       'Vary': 'Accept-Encoding'}, True)),
    ('/new', {}, (404, {'Content-Type': 'text/html;charset=utf-8'}, False)),
    ('/decisions', {'Accept-Encoding': 'gzip'}, (200, {'Content-Type': 'text/html', 'Cache-Control': 'no-cache',
                                                       'Content-Encoding': 'gzip', 'Vary': 'Accept-Encoding'}, True)),
    ('/app.css?v=1', {}, (200, {'Content-Type': 'text/css', 'Cache-Control': 'public, max-age=31536000, immutable',
                                'Vary': 'Accept-Encoding'}, True)),
    ('/fonts/inter-latin.woff2', {}, (200, {'Content-Type': 'font/woff2',
                                            'Cache-Control': 'public, max-age=31536000, immutable'}, True)),
    ('/icon-192.png', {}, (200, {'Content-Type': 'image/png', 'Cache-Control': 'no-cache'}, True)),
    ('/nothing-here.js', {}, (404, {'Content-Type': 'text/html;charset=utf-8'}, False)),
    ('/../hub/server.py', {}, (404, {'Content-Type': 'text/html;charset=utf-8'}, False)),
    ('/%2e%2e/hub/server.py', {}, (404, {'Content-Type': 'text/html;charset=utf-8'}, False)),
]


class StaticTest(LiveHub, unittest.TestCase):
    def get(self, path, headers):
        con = http.client.HTTPConnection(*self.httpd.server_address, timeout=10)
        try:
            con.request('GET', path, headers=headers)
            res = con.getresponse()
            return res, res.read()
        finally:
            con.close()

    def test_every_kind_of_path_keeps_its_answer(self):
        for path, headers, want in CASES:
            with self.subTest(path=path, headers=headers):
                res, _ = self.get(path, headers)
                got = {h: res.getheader(h) for h in HEADERS if res.getheader(h) is not None}
                self.assertEqual((res.status, got, res.getheader('ETag') is not None), want)

    def test_a_file_is_sent_as_it_is_and_a_known_etag_is_a_304(self):
        res, body = self.get('/app.css', {'Accept-Encoding': 'gzip'})
        with open(os.path.join(ROOT, 'web', 'app.css'), 'rb') as f:
            self.assertEqual(gzip.decompress(body) if res.getheader('Content-Encoding') else body, f.read())
        again, body = self.get('/app.css', {'If-None-Match': res.getheader('ETag')})
        self.assertEqual((again.status, body, again.getheader('Cache-Control')), (304, b'', 'no-cache'))
        res, body = self.get('/cli/tasks', {})
        with open(os.path.join(ROOT, 'client', 'tasks'), 'rb') as f:
            self.assertEqual(body, f.read())

    def test_head_sends_the_headers_without_the_body(self):
        con = http.client.HTTPConnection(*self.httpd.server_address, timeout=10)
        try:
            con.request('HEAD', '/config.js')
            res = con.getresponse()
            self.assertEqual((res.status, res.read()), (200, b''))
            self.assertGreater(int(res.getheader('Content-Length')), 0)
        finally:
            con.close()


if __name__ == '__main__':
    unittest.main()
