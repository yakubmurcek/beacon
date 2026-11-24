"""Every tailnet name (Tailscale Service) on every box, for the Names part of http://beacon/machines.

The hub only keeps and shows it. scripts/tailnet_check.py (a timer on the home server, which holds the
Tailscale API key and can reach every name) builds the list from the Tailscale API, each box's
`tailscale serve` (the collectors' host.serve block) and a probe of every name over the tailnet,
POSTs it here, and files a question on /decisions for anything stale or doubled (task 1803).

POST /api/names (TASKS_TOKEN): the checker's snapshot, stored as is.
GET  /api/names: {"at": epoch | null, "names": [...], "flags": [...]} (see tailnet_check.py).
"""
import json
import time

SCHEMA = 'CREATE TABLE IF NOT EXISTS names_snapshot (id INTEGER PRIMARY KEY CHECK (id = 1), at REAL, data TEXT)'


class Names:
    def __init__(self, store):
        self.store = store
        with store.connect() as con:
            con.execute(SCHEMA)

    def save(self, body):
        if not isinstance(body, dict) or not isinstance(body.get('names'), list):
            raise ValueError('expected {"names": [...]}')
        body = dict(body, at=float(body.get('at') or time.time()))
        with self.store.connect() as con:
            con.execute('INSERT OR REPLACE INTO names_snapshot(id, at, data) VALUES (1, ?, ?)',
                        (body['at'], json.dumps(body)))
        return {'ok': True, 'names': len(body['names'])}

    def view(self):
        with self.store.connect() as con:
            r = con.execute('SELECT data FROM names_snapshot WHERE id = 1').fetchone()
        return json.loads(r['data']) if r else {'at': None, 'names': [], 'flags': []}
