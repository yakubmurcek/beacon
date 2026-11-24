"""Who is calling, as tailscaled says: the hub's only source of a caller's tailnet identity.

The hub listens on loopback; `tailscale serve` proxies tailnet requests to it and sets X-Forwarded-For to the
caller's tailnet address, replacing whatever the caller sent. The hub asks tailscaled's LocalAPI (whois) who holds
that address. A Tailscale-User-Login header proves nothing: any process that reaches the port can write one, so it
is never read. The trust boundary is the server itself: a process on it can already read the hub's tokens.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import threading
import time
from typing import NamedTuple

SOCKETS = ('/run/tailscale/tailscaled.sock', '/var/run/tailscale/tailscaled.sock')
TAILNET = (ipaddress.ip_network('100.64.0.0/10'), ipaddress.ip_network('fd7a:115c:a1e0::/48'))
CACHE_SECONDS = 60  # a device keeps its address; one LocalAPI call a minute per device, not one per request
LOOPBACK = ('127.0.0.1', '::1', '::ffff:127.0.0.1')


class Caller(NamedTuple):
    login: str  # the tailnet user's login; 'tagged-devices' for a server tagged by the tailnet's admin
    tagged: bool  # a tagged node: one of the tailnet's own servers, not a person's device


def peer_address(client_address: tuple, headers) -> str | None:
    """The caller's tailnet address, when the request came through `tailscale serve` on this box; else None."""
    if not client_address or client_address[0] not in LOOPBACK:
        return None
    forwarded = headers.get_all('X-Forwarded-For') or []
    if len(forwarded) != 1 or ',' in forwarded[0]:
        return None
    try:
        ip = ipaddress.ip_address(forwarded[0].strip())
    except ValueError:
        return None
    return str(ip) if any(ip in net for net in TAILNET) else None


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__('local-tailscaled.sock', timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def ask_tailscaled(ip: str, timeout: float = 2.0) -> Caller | None:
    """One LocalAPI whois; None when tailscaled doesn't know the address or can't be asked."""
    for path in SOCKETS:
        conn = _UnixConnection(path, timeout)
        try:
            conn.request('GET', f'/localapi/v0/whois?addr={ip}')
            resp = conn.getresponse()
            body = resp.read()
        except OSError:
            continue
        finally:
            conn.close()
        if resp.status != 200:
            return None
        try:
            got = json.loads(body)
        except ValueError:
            return None
        login = (got.get('UserProfile') or {}).get('LoginName') or ''
        return Caller(login, bool((got.get('Node') or {}).get('Tags')))
    return None


class Whois:
    """whois(ip) with a short cache; a failed lookup is never cached, so a tailscaled restart heals at once."""

    def __init__(self, lookup=ask_tailscaled, clock=time.monotonic):
        self.lookup, self.clock = lookup, clock
        self.seen: dict[str, tuple[float, Caller]] = {}
        self.lock = threading.Lock()

    def __call__(self, ip: str) -> Caller | None:
        now = self.clock()
        with self.lock:
            hit = self.seen.get(ip)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        caller = self.lookup(ip)
        if caller:
            with self.lock:
                self.seen[ip] = (now, caller)
        return caller
