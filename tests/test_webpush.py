"""Web Push (hub/webpush.py): what it sends decrypts on the phone's side, and the VAPID header verifies."""
import base64
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tasks import ROOT

sys.path.insert(0, os.path.join(ROOT, 'hub'))
import webpush
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from store import Store
from webpush import WebPush, b64d, b64e, hkdf, raw_public


def decrypt(body, phone_key, auth):
    """The browser's side of RFC 8291."""
    salt, n = body[:16], body[20]
    as_public, ct = body[21:21 + n], body[21 + n:]
    shared = phone_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = hkdf(auth, shared, b'WebPush: info\x00' + raw_public(phone_key) + as_public, 32)
    cek = hkdf(salt, ikm, b'Content-Encoding: aes128gcm\x00', 16)
    nonce = hkdf(salt, ikm, b'Content-Encoding: nonce\x00', 12)
    plain = AESGCM(cek).decrypt(nonce, ct, None)
    return plain[:plain.rindex(b'\x02')]


class WebPushTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.private, self.public = webpush.new_keys()
        self.wp = WebPush(Store(os.path.join(self.tmp.name, 'u.db')), self.private, 'https://hub.example')
        self.phone = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.sub = {'endpoint': 'https://push.example/p/abc', 'keys': {'p256dh': b64e(raw_public(self.phone)),
                                                                      'auth': b64e(self.auth)}}

    def test_payload_decrypts_on_the_phone(self):
        body = webpush.encrypt(b'{"title": "hi"}', self.sub['keys']['p256dh'], self.sub['keys']['auth'])
        self.assertEqual(decrypt(body, self.phone, self.auth), b'{"title": "hi"}')

    def test_vapid_header_verifies_with_the_public_key(self):
        head = webpush.vapid_header('https://web.push.apple.com/x/y', self.wp.key, 'https://hub.example', now=1000)
        token, k = head[len('vapid t='):].split(', k=')
        self.assertEqual(k, self.public)
        h, c, sig = token.split('.')
        self.assertEqual(json.loads(b64d(c)), {'aud': 'https://web.push.apple.com', 'exp': 1000 + 12 * 3600,
                                               'sub': 'https://hub.example'})
        raw = b64d(sig)
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(k))
        pub.verify(encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big')),
                   f'{h}.{c}'.encode(), ec.ECDSA(hashes.SHA256()))

    def test_send_reaches_every_phone_and_drops_gone_ones(self):
        self.wp.subscribe(self.sub, 'iPhone')
        self.wp.subscribe(dict(self.sub, endpoint='https://push.example/p/old'), 'old')
        got = []

        def post(sub, payload, urgency):
            got.append((sub['endpoint'], urgency, json.loads(payload)))
            return (410, 'gone') if sub['endpoint'].endswith('old') else (201, None)
        self.wp.post = post
        ok, channel, err = self.wp.send('urgent', '🔴 box down', 'look', 'https://hub.example/machines')
        self.assertEqual((ok, channel, err), (True, 'webpush', None))
        self.assertEqual({g[1] for g in got}, {'high'})
        self.assertEqual(got[0][2]['url'], 'https://hub.example/machines')
        self.assertTrue(got[0][2]['urgent'])
        self.assertEqual(self.wp.count(), 1)

    def test_refuses_a_bad_subscription_and_says_why_nothing_went(self):
        with self.assertRaises(ValueError):
            self.wp.subscribe({'endpoint': 'http://x', 'keys': {}})
        self.assertFalse(self.wp.send('digest', 't', '', '')[0])
        self.assertFalse(WebPush(self.wp.store).ready())
        self.assertEqual(base64.urlsafe_b64decode(self.public + '=')[0], 4)  # an uncompressed P-256 point


if __name__ == '__main__':
    unittest.main()


class ContactTest(unittest.TestCase):
    def test_a_short_tailnet_name_gives_way_to_the_owners_mail(self):
        self.assertEqual(webpush.contact('https://hub', ['me@example.com']), 'mailto:me@example.com')
        self.assertEqual(webpush.contact('https://hub.example.com', ['me@example.com']), 'https://hub.example.com')
        self.assertEqual(webpush.contact('http://hub', []), 'https://example.com')
