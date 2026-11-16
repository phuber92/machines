"""A retained snapshot, monotonic refresh schedule, and atomic cache."""

import json
import os
import time

from github import RequestError, valid_snapshot


def ticks_ms():
    return time.ticks_ms()  # type: ignore[attr-defined]


def ticks_diff(end, start):
    return time.ticks_diff(end, start)  # type: ignore[attr-defined]


class Updater:
    def __init__(self, fetch, username, days=112, cache_path="contributions.json"):
        self.fetch = fetch
        self.username = username
        self.days = days
        self.cache_path = cache_path
        self.snapshot = None
        self.last_success = None
        self._last_tick = ticks_ms()
        self._elapsed = 0
        self._success_at = None
        self._next_refresh = 0
        self._backoff = 60
        self._save_pending = False
        self._load()

    def _now(self):
        tick = ticks_ms()
        self._elapsed += max(0, ticks_diff(tick, self._last_tick))
        self._last_tick = tick
        return self._elapsed

    @property
    def stale(self):
        now = self._now()
        return self._success_at is None or now - self._success_at >= 300_000

    def _load(self):
        try:
            with open(self.cache_path, "rb") as source:
                body = source.read(8193)
            if len(body) > 8192:
                raise ValueError()
            record = json.loads(body)
            snapshot = record["snapshot"]
            if (
                record["version"] != 1
                or record["username"] != self.username
                or record["days"] != self.days
                or not valid_snapshot(snapshot, self.days)
            ):
                raise ValueError()
            self.snapshot = snapshot
            print("ghcontrib: cached snapshot restored (stale)")
        except OSError as error:
            if error.args and error.args[0] != 2:
                print("ghcontrib: cache unavailable")
        except (ValueError, KeyError, TypeError):
            print("ghcontrib: invalid cache ignored")

    def _save(self):
        temporary = self.cache_path + ".tmp"
        try:
            with open(temporary, "w") as target:
                json.dump(
                    {
                        "version": 1,
                        "username": self.username,
                        "days": self.days,
                        "snapshot": self.snapshot,
                    },
                    target,
                )
            os.rename(temporary, self.cache_path)
        finally:
            try:
                os.remove(temporary)
            except OSError:
                pass

    def step(self):
        """Run at most one due refresh. Call at least once per second."""
        started = self._now()
        if started < self._next_refresh:
            return False
        try:
            snapshot, minimum_delay = self.fetch()
            if not valid_snapshot(snapshot, self.days):
                raise ValueError()
        except Exception as error:
            now = self._now()
            delay = max(self._backoff, getattr(error, "retry_after", 0))
            self._backoff = min(900, self._backoff * 2)
            self._next_refresh = now + delay * 1000
            # Raw exception messages can contain credentials or response content.
            reason = (
                str(error) if isinstance(error, RequestError) else type(error).__name__
            )
            print("ghcontrib: refresh failed ({}); retry in {}s".format(reason, delay))
            return False

        self._success_at = self._now()
        self.last_success = int(time.time())
        self._backoff = 60
        self._next_refresh = max(
            started + 60_000, self._success_at + max(1, minimum_delay) * 1000
        )
        changed = snapshot != self.snapshot
        self.snapshot = snapshot
        if changed:
            print("ghcontrib: snapshot updated")
        if changed or self._save_pending:
            try:
                self._save()
                self._save_pending = False
            except (OSError, MemoryError):
                self._save_pending = True
                print("ghcontrib: cache write failed; keeping snapshot in RAM")
        return True

    def run(self):
        was_stale = self.stale
        while True:
            self.step()
            stale = self.stale
            if stale != was_stale:
                print(
                    "ghcontrib: snapshot stale" if stale else "ghcontrib: snapshot fresh"
                )
                was_stale = stale
            time.sleep(1)
