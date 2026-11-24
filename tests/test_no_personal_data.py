"""The code holds nothing of one deployment: no owner name, email, machine name or host from the box's own settings.

Runs where a deployment's settings file exists (~/.config/beacon/config.json); elsewhere (CI, a fresh clone) there
is nothing to compare against and it skips.
"""
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.expanduser('~/.config/beacon/config.json')
SKIP_DIRS = ('web/vendor/', 'web/fonts/', 'docs/')
COMMON = {'nothing', 'iphone', 'ipad', 'raspberry', 'phone', 'laptop', 'desktop'}  # device names that are plain words


def words(cfg):
    owner = cfg.get('owner') or {}
    out = set(owner.get('names') or []) | {owner.get('name') or ''} | set(owner.get('emails') or [])
    out |= set((cfg.get('machines') or {}))
    https = (cfg.get('hub') or {}).get('https') or ''
    if https:
        out.add(re.sub(r'^https?://', '', https).split('.', 1)[-1])  # the tailnet's own name
    example = json.load(open(os.path.join(ROOT, 'config.example.json')))
    out -= set(example.get('machines') or {})
    return sorted(w for w in out if len(w) >= 4 and w.lower() not in COMMON)


class NoPersonalDataTest(unittest.TestCase):
    def test_code_names_no_one(self):
        if not os.path.isfile(LOCAL):
            self.skipTest('no deployment settings on this box')
        with open(LOCAL, encoding='utf-8') as f:
            pattern = re.compile(r'\b(' + '|'.join(map(re.escape, words(json.load(f)))) + r')\b', re.I)
        files = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        hits = []
        for rel in files:
            if rel.startswith(SKIP_DIRS) or rel.endswith(('.png', '.jpg', '.woff2', '.woff')):
                continue
            try:
                with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
                    for i, line in enumerate(f, 1):
                        m = pattern.search(line)
                        if m:
                            hits.append(f'{rel}:{i}: {m.group(0)}')
            except (UnicodeDecodeError, FileNotFoundError):
                continue
        self.assertEqual(hits, [], 'names from this box\'s settings belong in the settings, not the code '
                                   '(write "the owner", a settings key, or a made-up name)')


if __name__ == '__main__':
    unittest.main()
