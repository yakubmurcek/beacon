"""Stale-while-revalidate for the hub's slow reads (2026-11-22: AI jobs took 5 s, a day of usage 0.7 s).

A read younger than `fresh` seconds is served as it is. An older one is still served at once, and one
background thread rebuilds it for the next visitor, so a page never waits on a rebuild it did not need.
Only a key never built before, or one past `stale` seconds, is built while the visitor waits.
"""
import threading
import time
import traceback


class SWR:
    def __init__(self, limit=64):
        self.limit = limit
        self._vals = {}         # key -> (built_at, value)
        self._building = set()  # keys a background thread is rebuilding
        self._locks = {}        # key -> lock, so two cold visitors build it once
        self._lock = threading.Lock()

    def get(self, key, build, fresh=15, stale=600):
        now = time.time()
        with self._lock:
            hit = self._vals.get(key)
            if hit and now - hit[0] < fresh:
                return hit[1]
            if hit and now - hit[0] < stale:
                if key not in self._building:
                    self._building.add(key)
                    threading.Thread(target=self._rebuild, args=(key, build), daemon=True).start()
                return hit[1]
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:  # cold: build it now, once, even if several visitors arrive together
            hit = self._vals.get(key)
            if hit and time.time() - hit[0] < fresh:
                return hit[1]
            return self._put(key, build())

    def _rebuild(self, key, build):
        try:
            self._put(key, build())
        except Exception:  # the old value stays; the next visitor tries again
            traceback.print_exc()
        finally:
            with self._lock:
                self._building.discard(key)

    def _put(self, key, val):
        with self._lock:
            self._vals[key] = (time.time(), val)
            if len(self._vals) > self.limit:  # drop the oldest builds (past dates someone looked at once)
                for k, _ in sorted(self._vals.items(), key=lambda x: x[1][0])[:len(self._vals) - self.limit]:
                    self._vals.pop(k, None)
                    self._locks.pop(k, None)
        return val

    def warm(self, key, build):
        """Build a key in the background now (at startup), so the first visitor finds it ready."""
        with self._lock:
            if key in self._building:
                return
            self._building.add(key)
        threading.Thread(target=self._rebuild, args=(key, build), daemon=True).start()
