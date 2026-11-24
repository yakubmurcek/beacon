"""Vendored JS is invisible to every dependency scanner, so each file is on record: package, version, source, sha256."""

import hashlib
import json
import os
import unittest

VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'vendor')


class VendorManifest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(VENDOR, 'MANIFEST.json')) as f:
            self.files = json.load(f)['files']

    def test_every_vendored_file_is_on_record(self):
        here = {name for name in os.listdir(VENDOR) if name != 'MANIFEST.json'}
        self.assertEqual(here, set(self.files), 'add or remove the file in web/vendor/MANIFEST.json')

    def test_each_file_is_the_one_on_record(self):
        for name, entry in self.files.items():
            with self.subTest(name=name), open(os.path.join(VENDOR, name), 'rb') as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), entry['sha256'],
                                 f'{name} changed: update it from {entry["source"]} and its hash on record')

    def test_each_entry_names_its_source(self):
        for name, entry in self.files.items():
            with self.subTest(name=name):
                self.assertTrue(entry['package'] and entry['version'])
                self.assertIn(f"{entry['package']}@{entry['version']}/", entry['source'])


if __name__ == '__main__':
    unittest.main()
