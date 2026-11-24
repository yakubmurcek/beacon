"""Screenshots a question only links to, fetched onto its card (2026-11-22: "more images, a slideshow").

An agent asks "merge the PR?" and the PR body holds the before/after screenshots, but he would have to open GitHub
to see them. The hub fetches them with the box's own `gh` login (private repos too, no token handled here) and
keeps them like an uploaded picture, so the card shows them as a slideshow.
"""
import re
import struct
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


def size(data):
    """(width, height) in pixels of a png, jpg, gif or webp, or None when the header can't be read."""
    try:
        if data.startswith(b'\x89PNG'):
            return struct.unpack('>II', data[16:24])
        if data.startswith(b'GIF8'):
            return struct.unpack('<HH', data[6:10])
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            chunk = data[12:16]
            if chunk == b'VP8X':
                return 1 + int.from_bytes(data[24:27], 'little'), 1 + int.from_bytes(data[27:30], 'little')
            if chunk == b'VP8 ':
                w, h = struct.unpack('<HH', data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b'VP8L':
                b = int.from_bytes(data[21:25], 'little')
                return 1 + (b & 0x3FFF), 1 + ((b >> 14) & 0x3FFF)
        if data.startswith(b'\xff\xd8'):
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack('>HH', data[i + 5:i + 9])
                    return w, h
                i += 2 + struct.unpack('>H', data[i + 2:i + 4])[0]
    except struct.error:
        return None
    return None


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
