"""Web Push to Beacon's own home-screen app (the PWA): the channel notices reach the phone through (task 1967).

The page subscribes once (web/inbox.js, web/sw.js); the hub keeps each subscription and sends every push to all of
them, encrypted as RFC 8291 (aes128gcm) and signed with the deployment's VAPID key (RFC 8292). A subscription the
push service calls gone (404/410) is dropped.

Needs the `cryptography` package (ECDH, AES-GCM, ES256) and VAPID_PRIVATE_KEY in the hub's environment (the raw
P-256 private number, base64url; `python3 hub/webpush.py` prints a fresh pair). Without either, `ready()` is False
and the hub keeps its other channel.
"""
import base64
import hashlib
import hmac
import http.client
import json
import os
import struct
import time
import urllib.parse

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # a box without it shows the channel as off, never crashes the hub
    ec = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subs (
  endpoint TEXT PRIMARY KEY,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  device TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  last_ok REAL,
  last_error TEXT
);
"""
TTL = 24 * 3600


def b64d(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def hkdf(salt, ikm, info, length):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b'\x01', hashlib.sha256).digest()[:length]


def raw_public(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def private_key(text):
    return ec.derive_private_key(int.from_bytes(b64d(text), 'big'), ec.SECP256R1())


def new_keys():
    key = ec.generate_private_key(ec.SECP256R1())
    return b64e(key.private_numbers().private_value.to_bytes(32, 'big')), b64e(raw_public(key))


def encrypt(payload, p256dh, auth):
    """RFC 8291: one aes128gcm record for the subscription's keys."""
    ua_public = b64d(p256dh)
    local = ec.generate_private_key(ec.SECP256R1())
    as_public = raw_public(local)
    shared = local.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = hkdf(b64d(auth), shared, b'WebPush: info\x00' + ua_public + as_public, 32)
    salt = os.urandom(16)
    cek = hkdf(salt, ikm, b'Content-Encoding: aes128gcm\x00', 16)
    nonce = hkdf(salt, ikm, b'Content-Encoding: nonce\x00', 12)
    body = AESGCM(cek).encrypt(nonce, payload + b'\x02', None)
    return salt + struct.pack('!IB', 4096, len(as_public)) + as_public + body


def vapid_header(endpoint, key, subject, now=None):
    u = urllib.parse.urlparse(endpoint)
    head = b64e(json.dumps({'typ': 'JWT', 'alg': 'ES256'}).encode())
    claims = b64e(json.dumps({'aud': f'{u.scheme}://{u.netloc}', 'exp': int((now or time.time()) + 12 * 3600),
                              'sub': subject}).encode())
    r, s = decode_dss_signature(key.sign(f'{head}.{claims}'.encode(), ec.ECDSA(hashes.SHA256())))
    sig = b64e(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))
    return f'vapid t={head}.{claims}.{sig}, k={b64e(raw_public(key))}'


def contact(url, emails=()):
    """The JWT's contact (sub): a public https URL or a mailto. Apple refuses a short tailnet name such as
    https://hub (403 BadJwtToken, 2026-11-24), so a dotless host gives way to the owner's mail."""
    host = urllib.parse.urlparse(url or '').hostname or ''
    if url.startswith('https://') and '.' in host:
        return url
    mail = next((e for e in emails if '@' in (e or '')), '')
    return f'mailto:{mail}' if mail else 'https://example.com'


class WebPush:
    def __init__(self, store, key_text=None, subject=''):
        self.store = store
        self.key = private_key(key_text) if ec and key_text else None
        self.subject = subject or 'https://example.com'  # the push services want a contact URL; the hub's own
        with store.connect() as con:
            con.executescript(SCHEMA)

    def ready(self):
        return self.key is not None

    def public_key(self):
        return b64e(raw_public(self.key)) if self.key else ''

    def subscribe(self, sub, device=''):
        endpoint = str((sub or {}).get('endpoint') or '')
        keys = (sub or {}).get('keys') or {}
        if not endpoint.startswith('https://') or not keys.get('p256dh') or not keys.get('auth'):
            raise ValueError('subscription: endpoint and keys.p256dh, keys.auth required')
        with self.store.connect() as con:
            con.execute('INSERT INTO push_subs (endpoint, p256dh, auth, device, created_at) VALUES (?, ?, ?, ?, ?) '
                        'ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth, '
                        'device = excluded.device', (endpoint, keys['p256dh'], keys['auth'], str(device)[:120], time.time()))
        return {'devices': self.count()}

    def count(self):
        with self.store.connect() as con:
            return con.execute('SELECT COUNT(*) FROM push_subs').fetchone()[0]

    def send(self, kind, title, body, link):
        """One notice to every subscribed device: (ok, 'webpush', error). Ok when at least one took it."""
        if not self.key:
            return False, 'webpush', 'no VAPID key'
        with self.store.connect() as con:
            subs = [dict(r) for r in con.execute('SELECT * FROM push_subs')]
        if not subs:
            return False, 'webpush', 'no phone subscribed: open Beacon from the home screen, Inbox, Turn on'
        payload = json.dumps({'title': title, 'body': body, 'url': link, 'urgent': kind == 'urgent',
                              'tag': f'beacon-{kind}-{int(time.time())}'}).encode()
        oks, errors = 0, []
        for s in subs:
            status, err = self.post(s, payload, 'high' if kind == 'urgent' else 'normal')
            with self.store.connect() as con:
                if status in (404, 410):  # unsubscribed or expired on the phone
                    con.execute('DELETE FROM push_subs WHERE endpoint = ?', (s['endpoint'],))
                elif 200 <= status < 300:
                    con.execute('UPDATE push_subs SET last_ok = ?, last_error = NULL WHERE endpoint = ?',
                                (time.time(), s['endpoint']))
                else:
                    con.execute('UPDATE push_subs SET last_error = ? WHERE endpoint = ?', (err, s['endpoint']))
            oks += 200 <= status < 300
            if err:
                errors.append(err)
        return oks > 0, 'webpush', None if oks else '; '.join(errors)[:200]

    def post(self, sub, payload, urgency):
        u = urllib.parse.urlparse(sub['endpoint'])
        try:
            body = encrypt(payload, sub['p256dh'], sub['auth'])
            con = http.client.HTTPSConnection(u.netloc, timeout=20)
            try:
                con.request('POST', u.path + (f'?{u.query}' if u.query else ''), body, {
                    'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream', 'TTL': str(TTL),
                    'Urgency': urgency, 'Authorization': vapid_header(sub['endpoint'], self.key, self.subject)})
                r = con.getresponse()
                text = r.read()[:200].decode('utf-8', 'replace')
            finally:
                con.close()
        except (OSError, ValueError) as e:
            return 0, str(e)[:200]
        return r.status, None if 200 <= r.status < 300 else f'{r.status} {text}'.strip()


if __name__ == '__main__':
    private, public = new_keys()
    print(f'VAPID_PRIVATE_KEY={private}\n# public (the page reads it from /api/notify/vapid): {public}')
