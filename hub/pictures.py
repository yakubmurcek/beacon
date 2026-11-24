"""Screenshots a question only links to, fetched onto its card (2026-11-22: "more images, a slideshow").

An agent asks "merge the PR?" and the PR body holds the before/after screenshots, but he would have to open GitHub
to see them. The hub fetches them with the box's own `gh` login (private repos too, no token handled here) and
keeps them like an uploaded picture, so the card shows them as a slideshow.
"""
import re
import subprocess
from urllib.parse import quote

PR = re.compile(r'https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)')
BLOB = re.compile(r'https://github\.com/([\w.-]+)/([\w.-]+)/(?:blob|raw)/([^\s)"\'<>?#]+?\.(png|jpe?g|webp|gif))'
                  r'(?:\?[^\s)"\'<>]*)?', re.I)
MAGIC = ((b'\x89PNG', 'png'), (b'\xff\xd8', 'jpg'), (b'GIF8', 'gif'))
HARVEST_MAX = 8  # pictures a card fetches for itself; PR bodies can hold 20+


def gh(path, raw=False):
    """`gh api <path>` stdout as bytes, else None (no gh, no login, not found)."""
    cmd = ['gh', 'api', *(['-H', 'Accept: application/vnd.github.raw'] if raw else []), path]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def kind(data):
    for sig, ext in MAGIC:
        if data.startswith(sig):
            return ext
    return 'webp' if data[:4] == b'RIFF' and data[8:12] == b'WEBP' else None


def label_of(path):
    """docs/img/before/1-onboarding.png → "Before · Onboarding"; pass4/a01-welcome.png → "Welcome"."""
    parts = path.split('/')
    name = re.sub(r'^[a-z]?\d+[-_ ]+', '', parts[-1].rsplit('.', 1)[0], flags=re.I)
    name = re.sub(r'[-_]+', ' ', name).strip()
    name = name[:1].upper() + name[1:]
    dirs = {p.lower() for p in parts[:-1]}
    return f'Before · {name}' if 'before' in dirs else f'After · {name}' if 'after' in dirs else name


def fetch(owner, repo, rest, ref=None):
    """(bytes, ext, path) of `blob/<ref>/<path>`: a branch name can hold slashes, so a known ref goes first."""
    segs = rest.split('/')
    tries = ([(ref, rest[len(ref) + 1:])] if ref and rest.startswith(ref + '/') else []) + \
        [('/'.join(segs[:k]), '/'.join(segs[k:])) for k in range(1, min(4, len(segs)))]
    for r, p in tries:
        data = gh(f'repos/{owner}/{repo}/contents/{quote(p)}?ref={quote(r, safe="")}', raw=True)
        ext = data and kind(data)
        if ext:
            return data, ext, p
    return None


def harvest(text, room=HARVEST_MAX):
    """[(bytes, ext, label)] of the GitHub pictures in `text` and in the bodies of the PRs it links, in order."""
    found = [(m.group(1), m.group(2), m.group(3), None) for m in BLOB.finditer(text)]
    for owner, repo, num in dict.fromkeys(PR.findall(text)):
        raw = gh(f'repos/{owner}/{repo}/pulls/{num}')
        try:
            import json
            pr = json.loads(raw or b'{}')
        except ValueError:
            continue
        ref = (pr.get('head') or {}).get('ref')
        found += [(m.group(1), m.group(2), m.group(3), ref) for m in BLOB.finditer(pr.get('body') or '')]
    out, seen = [], set()
    for owner, repo, rest, ref in found:
        if len(out) >= room or (owner, repo, rest) in seen:
            continue
        seen.add((owner, repo, rest))
        got = fetch(owner, repo, rest, ref)
        if got:
            out.append((got[0], got[1], label_of(got[2])))
    return out
