# pyright: strict
"""Claude Code sign-ins on the boxes: how long each one has left, and signing one in again from a phone.

A Claude Code login ends about 30 days after sign-in and refreshing never extends it, and a login can't be copied
between boxes (each refresh token works once). So every box needs its own sign-in each month. This turns that into one
tap: the notice links to http://<hub>/machines#/login/<box>, the page starts `claude auth login` on that box, shows its
sign-in link, takes the code claude.ai shows after approving, and confirms the new end date.

Settings `claude_logins`: {box: {"ssh": "user@host", "login": the login command there,
                                 "expiry": a command printing the login's refreshTokenExpiresAt in ms (default: Linux),
                                 "after": a command run there once signed in (restart what needs the login), optional}}

GET  /api/logins                 {"boxes": [{"machine", "ends_at" (epoch|null), "checked_at", "state": "ok"|"soon"|"out"|
                                  "unknown", "flow": {"state": "starting"|"waiting"|"checking"|"done"|"failed",
                                  "url" (his login only), "error", "ends_at", "at"} | null}]}
POST /api/logins/<box>/start     his login only: starts (or keeps) the sign-in on that box
POST /api/logins/<box>/code      his login only: {"code": "..."}, the code claude.ai showed

Each sign-in runs as its own process (`python3 hub/logins.py run <box>`, a transient systemd unit where there is
systemd) that holds the login in a pseudo-terminal over ssh, so a hub restart doesn't lose it. It ends by itself after
LIMIT_SEC. Files live in RUN_DIR/<box>/: status.json (what the page shows) and code (his code, read once, deleted).
"""
import json
import logging
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, cast

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings

log = logging.getLogger('beacon.logins')

Box = dict[str, str]
Status = dict[str, Any]
_SECTION = cast('dict[str, Any]', settings.CONFIG.get('claude_logins') or {})
BOXES: dict[str, Box] = {k: cast('Box', v) for k, v in _SECTION.items() if not k.startswith('_') and isinstance(v, dict)}
RUN_DIR = os.environ.get('LOGINS_DIR') or ('/run/beacon-login' if os.access('/run', os.W_OK)
                                         else os.path.join(os.path.expanduser('~'), '.cache', 'beacon-login'))
LIMIT_SEC = 900           # a sign-in nobody finished ends by itself
WARN_SEC = 3 * 86400      # the notice comes this long before a login ends
CHECK_EVERY_SEC = 1800    # how often the hub reads each box's end date
SSH = ['ssh', '-o', 'ConnectTimeout=10', '-o', 'BatchMode=yes', '-o', 'LogLevel=ERROR']  # no banner lines in the output
LINUX_EXPIRY = ("python3 -c \"import json,os;print(json.load(open(os.path.expanduser('~/.claude/.credentials.json')))"
                "['claudeAiOauth'].get('refreshTokenExpiresAt') or 0)\"")
URL_RE = re.compile(r'https://\S+/oauth/authorize\?\S+')
ANSI_RE = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]')
CODE_RE = re.compile(r'^[A-Za-z0-9_\-#.~]{10,400}$')
RUNNING = ('starting', 'waiting', 'checking')


def box_dir(box: str) -> str:
    return os.path.join(RUN_DIR, box)


def read_status(box: str) -> Status | None:
    try:
        with open(os.path.join(box_dir(box), 'status.json'), encoding='utf-8') as f:
            return cast('Status', json.load(f))
    except (OSError, ValueError):
        return None


def write_status(box: str, **st: Any) -> None:
    os.makedirs(box_dir(box), mode=0o700, exist_ok=True)
    st['at'] = time.time()
    tmp = os.path.join(box_dir(box), 'status.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f)
    os.replace(tmp, os.path.join(box_dir(box), 'status.json'))


def going(flow: Status | None, now: float) -> bool:
    """A sign-in still under way: its process answers, or it was asked for a moment ago and has no process yet."""
    if not flow or flow.get('state') not in RUNNING:
        return False
    if flow.get('pid') is None:
        return now - float(flow.get('at') or 0) < 60
    return alive(flow.get('pid'))


def alive(pid: object) -> bool:
    try:
        os.kill(int(cast('int', pid)), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def remote(box: str, command: str, timeout: int = 60) -> tuple[int, str]:
    """(exit code, output) of one command on a box over ssh."""
    try:
        p = subprocess.run([*SSH, BOXES[box]['ssh'], command], capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return -1, str(e)


def read_expiry(box: str) -> float | None:
    """When the box's login ends (epoch seconds), 0 when it has none, None when the box didn't answer."""
    code, out = remote(box, BOXES[box].get('expiry') or LINUX_EXPIRY, timeout=30)
    digits = [line.strip() for line in out.splitlines() if line.strip().isdigit()]
    if code == 0 and digits:
        return int(digits[-1]) / 1000
    return 0 if code == 0 or 'No such file' in out or 'FileNotFound' in out else None


def state(ends: float | None, now: float) -> str:
    if ends is None:
        return 'unknown'
    if not ends or ends <= now:
        return 'out'
    return 'soon' if ends - now < WARN_SEC else 'ok'


# ------------------------------------------------------------------ the sign-in process

def run(box: str, limit: int = LIMIT_SEC) -> int:
    """Holds `claude auth login` on the box in a pseudo-terminal: its link goes to status.json, his code goes in."""
    import fcntl
    import pty
    import struct
    import termios
    cfg = BOXES[box]
    write_status(box, state='starting', pid=os.getpid())
    pid, fd = pty.fork()
    if pid == 0:  # the child: ssh with a terminal, so the login asks for the code like it does for a person
        os.environ['TERM'] = 'dumb'
        os.execvp('ssh', ['ssh', '-tt', *SSH[1:], cfg['ssh'], cfg['login']])  # noqa: S606 (fixed program, no shell)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 50, 2000, 0, 0))  # wide: the link never wraps
    seen, url, sent, deadline = '', '', False, time.time() + limit
    code_file = os.path.join(box_dir(box), 'code')
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 1)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:  # the login ended
                break
            if not chunk:
                break
            seen = (seen + ANSI_RE.sub('', chunk.decode('utf-8', 'replace')))[-20000:]
            if not url and (m := URL_RE.search(seen)):
                url = m.group(0)
                write_status(box, state='waiting', pid=os.getpid(), url=url)
        if url and not sent and os.path.exists(code_file):
            with open(code_file, encoding='utf-8') as f:
                code = f.read().strip()
            os.remove(code_file)
            os.write(fd, (code + '\r').encode())
            sent = True
            write_status(box, state='checking', pid=os.getpid())
    rc = finish(pid, deadline)
    os.close(fd)  # a login still waiting on it hangs up
    tail = ' '.join(seen.split())[-300:]
    if rc != 0 or not sent:
        why = 'nobody finished it in 15 minutes' if not sent and time.time() >= deadline else (
            'the box gave no sign-in link' if not url else tail or f'the login exited with {rc}')
        write_status(box, state='failed', error=why)
        return 1
    if cfg.get('after'):
        remote(box, cfg['after'], timeout=120)
    ends = read_expiry(box)
    if not ends or ends < time.time() + 7 * 86400:
        write_status(box, state='failed', error=f'signed in, but the login still ends soon ({tail})')
        return 1
    write_status(box, state='done', ends_at=ends)
    return 0


def finish(pid: int, deadline: float) -> int:
    """The login's exit code; one still going after the deadline (plus a moment to save) is stopped."""
    end = max(deadline, time.time()) + 60
    while time.time() < end:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.5)
    os.kill(pid, 15)
    os.waitpid(pid, 0)
    return -15


# ------------------------------------------------------------------ the hub's side

class Logins:
    def __init__(self, notify: Callable[[dict[str, Any]], object] | None = None, base: str = '',
                 spawn: Callable[[str], object] | None = None,
                 expiry: Callable[[str], float | None] = read_expiry) -> None:
        self.notify, self.base, self.expiry = notify, base, expiry
        self.spawn: Callable[[str], object] = spawn or self._spawn
        self.ends: dict[str, tuple[float | None, float]] = {}  # box -> (ends_at epoch | 0 | None, checked_at)
        self.lock = threading.Lock()

    def view(self, with_url: bool = False) -> dict[str, Any]:
        now = time.time()
        return {'now': now, 'boxes': [self._box(box, now, with_url) for box in BOXES]}

    def _box(self, box: str, now: float, with_url: bool) -> dict[str, Any]:
        ends, checked = self.ends.get(box, (None, None))
        flow = read_status(box)
        if flow and flow.get('state') in RUNNING and not going(flow, now):
            flow = dict(flow, state='failed', error='the sign-in stopped')
        if flow and flow.get('state') in ('done', 'failed') and now - float(flow.get('at') or 0) > 3600:
            flow = None  # an old finished sign-in is not news
        if flow and flow.get('state') == 'done' and flow.get('ends_at'):
            ends = float(flow['ends_at'])
        hidden = ('pid',) if with_url else ('pid', 'url')
        return {'machine': box, 'ends_at': ends or None, 'checked_at': checked, 'state': state(ends, now),
                'flow': {k: v for k, v in flow.items() if k not in hidden} if flow else None}

    def start(self, box: str) -> Status | None:
        if box not in BOXES:
            raise KeyError(box)
        with self.lock:
            flow = read_status(box)
            if going(flow, time.time()):
                return flow
            shutil.rmtree(box_dir(box), ignore_errors=True)
            write_status(box, state='starting', pid=None)
            self.spawn(box)
            return read_status(box)

    def code(self, box: str, code: object) -> dict[str, bool]:
        if box not in BOXES:
            raise KeyError(box)
        text = str(code or '').strip()
        if not CODE_RE.match(text):
            raise ValueError('code: paste the whole code claude.ai showed')
        flow = read_status(box)
        if not flow or flow.get('state') != 'waiting':
            raise ValueError('no sign-in is waiting for a code: start it again')
        fd = os.open(os.path.join(box_dir(box), 'code'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        return {'ok': True}

    @staticmethod
    def _spawn(box: str) -> None:
        cmd = [sys.executable, os.path.abspath(__file__), 'run', box]
        env = {k: os.environ[k] for k in ('BEACON_CONFIG', 'LOGINS_DIR', 'HOME') if k in os.environ}
        if shutil.which('systemd-run'):
            subprocess.run(['systemd-run', '--quiet', '--collect', f'--unit=beacon-claude-login-{box}',
                            f'--description=Beacon: Claude sign-in on {box} (ends itself in 15 min)',
                            f'--property=RuntimeMaxSec={LIMIT_SEC + 120}',
                            *[f'--setenv={k}={v}' for k, v in env.items()], *cmd], check=True, timeout=20)
        else:
            subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # -------------------------------------------------------------- the watch: end dates and the notice

    def check(self, now: float | None = None) -> None:
        """Reads every box's end date; one ending within WARN_SEC (or gone) gets a notice that opens its sign-in."""
        now = now or time.time()
        for box in BOXES:
            ends = self.expiry(box)
            if ends is None:  # asleep or offline: keep what we knew
                continue
            self.ends[box] = (ends, now)
            st = state(ends, now)
            if self.notify and st in ('soon', 'out'):
                when = time.strftime('%a %-d %b %H:%M', time.localtime(ends)) if ends else ''
                self.notify({'title': f'🔑 Claude on {box} ' + (f'signs out {when}' if st == 'soon' else 'is signed out'),
                             'body': 'Tap, open the sign-in link, paste the code. About a minute.',
                             'link': f'{self.base}/machines#/login/{box}', 'level': 'normal',
                             'key': f'claude-login:{box}', 'every': 86400, 'source': '🔑 Claude logins'})

    def watch(self) -> None:
        def loop() -> None:
            while True:
                try:
                    self.check()
                except Exception:  # the next round tries again; the log says why
                    log.exception('claude login check failed')
                time.sleep(CHECK_EVERY_SEC)
        threading.Thread(target=loop, name='claude-logins', daemon=True).start()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'run' and sys.argv[2] in BOXES:
        sys.exit(run(sys.argv[2]))
    sys.exit(f'usage: {sys.argv[0]} run <box>   (boxes: {", ".join(BOXES) or "none in the settings"})')
