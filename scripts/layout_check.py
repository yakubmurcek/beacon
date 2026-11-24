#!/usr/bin/env python3
# pyright: strict
"""Start a throwaway hub with made-up data, then draw and measure every page (scripts/layout_check.mjs).

  python3 scripts/layout_check.py                  # what scripts/check runs
  python3 scripts/layout_check.py --shots DIR      # also keep a picture of every page

Runs on config.example.json and a temporary database, on a free port; nothing real is read or written.
The browser is Google Chrome when the box has it ($CHROME overrides), else Playwright's own Chromium.
"""
import http.client
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait_up(port: int, proc: subprocess.Popen[bytes], seconds: float = 20) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            sys.exit('layout check: the hub did not start')
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
        try:
            conn.request('GET', '/tasks')
            conn.getresponse().read()
            return
        except OSError:
            time.sleep(0.2)
        finally:
            conn.close()
    sys.exit('layout check: the hub did not answer in time')


def main() -> int:
    port = free_port()
    hub = f'http://127.0.0.1:{port}'
    tmp = tempfile.mkdtemp(prefix='beacon-layout-')
    env = {**os.environ, 'BEACON_CONFIG': os.path.join(ROOT, 'config.example.json'), 'USAGE_PORT': str(port),
           'USAGE_DB': os.path.join(tmp, 'usage.db'), 'TASKS_TOKEN': 'layout-check', 'HUB': hub}
    for k in ('TASKS_OWNER', 'USAGE_TOKEN', 'HUB_ALARMS', 'VAPID_PRIVATE_KEY'):
        env.pop(k, None)
    chrome = shutil.which('google-chrome')
    if 'CHROME' not in env and chrome:
        env['CHROME'] = chrome
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, 'hub', 'server.py')], env=env, cwd=tmp,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_up(port, proc)
        subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'demo.py')], env=env, check=True,
                       stdout=subprocess.DEVNULL)
        return subprocess.run(['node', os.path.join(ROOT, 'scripts', 'layout_check.mjs'), hub, *sys.argv[1:]],
                              env=env, cwd=ROOT, check=False).returncode
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
