"""How many of this machine's newest Claude Code sessions carry a name (custom-title) in their log.
python3 scripts/names_check.py [N] [--since EPOCH]  (task 1789's Done-test)"""
import datetime
import glob
import os
import re
import sys

n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 10
since = float(sys.argv[sys.argv.index('--since') + 1]) if '--since' in sys.argv else 0
logs = [p for root in glob.glob(os.path.expanduser('~/.claude*/projects'))
        for p in glob.glob(os.path.join(root, '*', '*.jsonl'))]


def started(path):
    """When the session began: the first timestamp in its log (a file's ctime is its last change)."""
    with open(path, 'rb') as f:
        for raw in f:
            m = re.search(rb'"timestamp":"([^"]+)"', raw)
            if m:
                return datetime.datetime.fromisoformat(m.group(1).decode().replace('Z', '+00:00')).timestamp()
    return 0


logs = [(started(p), p) for p in logs if os.path.getsize(p) > 2000 and os.path.getmtime(p) >= since]
logs = [p for t, p in sorted(logs, reverse=True) if t >= since]
named = 0
for p in logs[:n]:
    with open(p, 'rb') as f:
        data = f.read()
    title = None
    for raw in data.splitlines():
        if b'"custom-title"' in raw:
            title = raw.split(b'"customTitle":"', 1)[-1].split(b'"', 1)[0].decode('utf-8', 'replace')
    named += bool(title)
    print('NAMED ' if title else 'none  ', title or '', '|', os.path.basename(os.path.dirname(p)), os.path.basename(p)[:8])
print(f'{named}/{min(n, len(logs))} named')
