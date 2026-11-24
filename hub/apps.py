"""His other tools as hub pages (2026-11-22): the hub passes them through under its own name.

Power, budget and Lighthouse keep running where their data is (the home server: the Shelly plugs are on the
home LAN, budget's SQLite, Lighthouse's sources) and the hub forwards to them over the tailnet by
the machine's own name, never by the app's old tailnet name, so those names can be retired.

  POWER_URL       hal-power (default: settings `apps.power`), read by /api/power
  BUDGET_URL      budget (Next.js, basePath /budget), served at /budget/...
  LIGHTHOUSE_URL  Lighthouse (Next.js, basePath /lighthouse), served at /lighthouse/...
"""
import http.client
import json
import os
import select
import socket
import urllib.parse

from settings import APPS

PREFIXES = {
    '/budget': os.environ.get('BUDGET_URL', APPS['budget']),
    '/lighthouse': os.environ.get('LIGHTHOUSE_URL', APPS['lighthouse']),
}
POWER_URL = os.environ.get('POWER_URL', APPS['power'])
HOP = {'connection', 'keep-alive', 'proxy-connection', 'transfer-encoding', 'te', 'trailer', 'upgrade'}
OWN = {'host', 'x-hub-host'}  # set by the hub itself, never taken from the client
SENT = {'date', 'server'}  # send_response writes the hub's own
MAX_BODY = 60 * 1024 * 1024  # a few years of bank CSVs in one drop


def backend_for(path):
    """The backend base URL when the path belongs to a passed-through app, else None."""
    for prefix, base in PREFIXES.items():
        if path == prefix or path.startswith(prefix + '/') or path.startswith(prefix + '?'):
            return base
    return None


def proxy(handler, base, path=None):
    """Forward the handler's request to base (+ path, default the request's own) and stream the answer back."""
    u = urllib.parse.urlsplit(base)
    target = path or handler.path
    if handler.headers.get('Upgrade', '').lower() == 'websocket':
        return tunnel(handler, u.hostname, u.port or 80, target)
    length = handler.headers.get('Content-Length') or ''
    length = int(length) if length.isdigit() else 0
    if length > MAX_BODY:
        return handler.send_json(413, {'error': 'body size'})
    body = handler.rfile.read(length) if length else None
    headers = {k: v for k, v in handler.headers.items() if k.lower() not in HOP | OWN}
    # The hub's own name: Tailscale Serve on the home server overwrites X-Forwarded-Host, so the apps read this one
    headers['X-Hub-Host'] = handler.headers.get('Host', '')
    headers['Host'] = u.netloc  # Tailscale Serve on the machine answers only to its own name
    headers['X-Forwarded-Proto'] = 'http'
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=300)
    try:
        conn.request(handler.command, target, body=body, headers=headers)
        res = conn.getresponse()
    except (OSError, http.client.HTTPException):
        conn.close()
        return handler.send_json(502, {'error': f'{u.hostname} is not answering'})
    try:
        handler.send_response(res.status, res.reason)
        for k, v in res.getheaders():
            if k.lower() not in HOP | SENT:
                handler.send_header(k, v)
        handler.send_header('Connection', 'close')  # the answer ends when the socket does (streams have no length)
        handler.end_headers()
        handler.close_connection = True
        if handler.command != 'HEAD':
            while True:
                chunk = res.read1(65536)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
    except (OSError, http.client.HTTPException):
        pass  # the app went quiet or cut the stream mid-answer: the page ends there, the hub carries on
    finally:
        conn.close()


def fetch_json(base, path, timeout=10):
    """One JSON GET from a backend (the hub caches it: /api/power)."""
    u = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        conn.request('GET', path, headers={'Host': u.netloc, 'Accept': 'application/json'})
        res = conn.getresponse()
        body = res.read()
        if res.status != 200:
            raise http.client.HTTPException(f'{u.hostname} answered {res.status}')
        return json.loads(body)
    finally:
        conn.close()


def tunnel(handler, host, port, target):
    """A websocket (Next dev's live reload): replay the handshake to the backend, then pipe bytes both ways."""
    try:
        up = socket.create_connection((host, port), timeout=10)
    except OSError:
        return handler.send_json(502, {'error': f'{host} is not answering'})
    fwd = [(k, v) for k, v in handler.headers.items() if k.lower() not in OWN]
    fwd += [('Host', f'{host}:{port}'), ('X-Hub-Host', handler.headers.get('Host', ''))]
    head = f'{handler.command} {target} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fwd) + '\r\n'
    up.sendall(head.encode('latin-1'))
    down = handler.connection
    handler.close_connection = True
    socks = [down, up]
    try:
        up.settimeout(None)
        down.settimeout(None)
        while True:
            ready, _, broken = select.select(socks, [], socks, 600)
            if broken or not ready:
                break
            for s in ready:
                data = s.recv(65536)
                if not data:
                    return
                (up if s is down else down).sendall(data)
    except OSError:
        pass
    finally:
        up.close()
