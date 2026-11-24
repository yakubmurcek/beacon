"""The code holds nothing of one deployment: no owner name, email, machine name, host or project of the box's own settings.

The words to keep out are committed only as salted hashes (tests/fixtures/personal-words.json), so the check runs
everywhere, CI included, without the repo spelling them out. Every word, email and dotted name in the tracked files is
hashed the same way and looked up.

On a box with the deployment's settings (~/.config/beacon/config.json) a second test checks the fixture still holds
every word those settings name; `python3 tests/test_no_personal_data.py --update` rewrites it from them.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.expanduser('~/.config/beacon/config.json')
FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'personal-words.json')
SKIP_DIRS = ('web/vendor/', 'web/fonts/', 'docs/', 'tests/fixtures/')
BINARY = ('.png', '.jpg', '.woff2', '.woff', '.ico')
COMMON = {'nothing', 'iphone', 'ipad', 'raspberry', 'phone', 'laptop', 'desktop'}  # device names that are plain words
TOKEN = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\w+(?:\.\w+)*', re.UNICODE)


def digest(word, salt):
    return hashlib.sha256((salt + word.lower()).encode()).hexdigest()


def words(cfg):
    """The words a deployment's settings name that no code should: owner names and emails, machines, the tailnet.
    Short or plain words are left out (they would match ordinary text)."""
    owner = cfg.get('owner') or {}
    out = set(owner.get('names') or []) | {owner.get('name') or ''} | set(owner.get('emails') or [])
    out |= set(cfg.get('machines') or {})
    https = (cfg.get('hub') or {}).get('https') or ''
    host = re.sub(r'^https?://', '', https).split('/', 1)[0]
    if '.' in host:  # beacon.<tailnet>.ts.net: the tailnet's own name; a bare short name has none
        out.add(host.split('.', 1)[-1])
    with open(os.path.join(ROOT, 'config.example.json'), encoding='utf-8') as f:
        example = json.load(f)
    out -= set(example.get('machines') or {})
    return sorted(w for w in out if len(w) >= 4 and w.lower() not in COMMON)


def candidates(line):
    """Every word, email and dotted name on a line, and each run of two to four words (a name with a space)."""
    toks = TOKEN.findall(line)
    out = set(toks)
    for t in toks:  # a host's own domain inside a longer name: hub.example.ts.net holds example.ts.net
        parts = t.split('@')[-1].split('.')
        out |= {'.'.join(parts[i:]) for i in range(1, len(parts) - 1)}
    for i in range(len(toks)):
        for n in (2, 3, 4):
            if i + n <= len(toks):
                out.add(' '.join(toks[i:i + n]))
    return out


def load_fixture():
    with open(FIXTURE, encoding='utf-8') as f:
        return json.load(f)


def tracked_files():
    files = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [f for f in files if not f.startswith(SKIP_DIRS) and not f.endswith(BINARY)]


class NoPersonalDataTest(unittest.TestCase):
    def test_code_names_no_one(self):
        fx = load_fixture()
        salt, banned = fx['salt'], set(fx['hashes'])
        hits = []
        for rel in tracked_files():
            try:
                with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
                    for i, line in enumerate(f, 1):
                        if any(digest(c, salt) in banned for c in candidates(line)):
                            hits.append(f'{rel}:{i}')
            except (UnicodeDecodeError, FileNotFoundError):
                continue
        self.assertEqual(hits, [], "names from the deployment's settings belong in the settings, not the code "
                                   '(write "the owner", a settings key, or a made-up name)')

    def test_a_planted_name_is_caught(self):
        fx = load_fixture()
        salt = fx['salt']
        planted = {digest('Ada Lovelace', salt), digest('ada@example.org', salt)}
        found = [c for c in candidates("owner = 'Ada Lovelace'  # ada@example.org") if digest(c, salt) in planted]
        self.assertEqual(sorted(found), ['Ada Lovelace', 'ada@example.org'])

    @unittest.skipUnless(os.path.isfile(LOCAL), "only where the deployment's settings are")
    def test_the_fixture_holds_every_word_of_the_deployment(self):
        fx = load_fixture()
        with open(LOCAL, encoding='utf-8') as f:
            missing = [w for w in words(json.load(f)) if digest(w, fx['salt']) not in set(fx['hashes'])]
        self.assertEqual(len(missing), 0, f'{len(missing)} word(s) not in the fixture: run '
                                          'python3 tests/test_no_personal_data.py --update and commit it')


def update():
    """Rewrite the fixture from this box's settings (keeps the salt, so old hashes stay comparable)."""
    with open(LOCAL, encoding='utf-8') as f:
        found = words(json.load(f))
    try:
        salt = load_fixture()['salt']
    except (OSError, ValueError, KeyError):
        salt = os.urandom(16).hex()
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    with open(FIXTURE, 'w', encoding='utf-8') as f:
        json.dump({'_about': 'Salted SHA-256 of the words no tracked file may contain (tests/test_no_personal_data.py).',
                   'salt': salt, 'hashes': sorted({digest(w, salt) for w in found})}, f, indent=1)
        f.write('\n')
    print(f'{len(found)} words hashed into {os.path.relpath(FIXTURE, ROOT)}')


if __name__ == '__main__':
    if sys.argv[1:] == ['--update']:
        update()
    else:
        unittest.main()
