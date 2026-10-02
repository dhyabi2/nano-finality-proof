"""Samples, their store, and the windows the summary is computed over.

Two rules live here rather than in the HTTP layer, so they hold no matter who
calls:

  * a timeout or an error is stored exactly like a confirmation. There is no
    code path that drops one, and `prune` treats all three outcomes the same.
  * the store knows whether it has *ever* held a sample (`ever_sampled`),
    which is a different question from whether a given window is empty. The
    first is a broken service; the second is a quiet hour.
"""

import base64
import binascii
import json
import os
import secrets
import threading

CONFIRMED = "confirmed"
TIMEOUT = "timeout"
ERROR = "error"
OUTCOMES = (CONFIRMED, TIMEOUT, ERROR)

WINDOWS = {"1h": 3600, "24h": 86_400, "7d": 604_800, "30d": 2_592_000}
DEFAULT_WINDOW = "24h"

RETENTION_S = WINDOWS["30d"]       # the spec's floor: at least 30 days
LOW_CONFIDENCE_N = 30
LIMIT_MIN, LIMIT_MAX, LIMIT_DEFAULT = 1, 1000, 100


def new_sample_id() -> str:
    return "smp_" + secrets.token_hex(8)


class Sample:
    __slots__ = ("id", "sent_at", "confirmed_at", "elapsed_ms", "block_hash",
                 "amount_raw", "node", "outcome", "error", "timeout_ms",
                 "sent_epoch")

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "sent_at": self.sent_at,
            "confirmed_at": self.confirmed_at,
            "elapsed_ms": self.elapsed_ms,
            "block_hash": self.block_hash,
            "amount_raw": self.amount_raw,
            "node": self.node,
            "outcome": self.outcome,
            "error": self.error,
            "timeout_ms": self.timeout_ms,
        }

    @classmethod
    def from_json(cls, row: dict, sent_epoch: float):
        return cls(sent_epoch=sent_epoch, **{k: row.get(k) for k in cls.__slots__
                                             if k != "sent_epoch"})


class SampleStore:
    """In-process, append-only within the retention window.

    `path` makes it durable: rows are appended as JSON Lines and reloaded on
    start, because a figure is only worth quoting after 24 hours of sampling
    and a restart inside that window must not reset the clock.
    """

    def __init__(self, clock, path=None, retention_s=RETENTION_S):
        self.clock = clock
        self.path = path
        self.retention_s = retention_s
        self._rows = []
        self._ever = False
        self._lock = threading.RLock()
        if path and os.path.exists(path):
            self._load()

    # -- durability --------------------------------------------------------
    def _load(self):
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    sample = Sample.from_json(row, float(row["sent_epoch"]))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue    # a torn final line from a killed process
                self._rows.append(sample)
                self._ever = True
        self._rows.sort(key=lambda s: s.sent_epoch)

    def _append(self, sample):
        if not self.path:
            return
        row = sample.to_json()
        row["sent_epoch"] = sample.sent_epoch
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -- writes ------------------------------------------------------------
    def add(self, sample):
        with self._lock:
            self._rows.append(sample)
            self._ever = True
            self._append(sample)
            self.prune()
            return sample

    def prune(self):
        """Drop rows past the retention window -- every outcome alike.

        Nothing here looks at `outcome`. A prune that treated a timeout
        differently from a confirmation would flatter the rate, which is the
        bug this whole service exists to not have.
        """
        cutoff = self.clock.now() - self.retention_s
        with self._lock:
            self._rows = [s for s in self._rows if s.sent_epoch >= cutoff]

    # -- reads -------------------------------------------------------------
    @property
    def ever_sampled(self) -> bool:
        """True once anything has been recorded, even if the window is empty.

        Deliberately sticky: it survives a prune, because a store that sampled
        for a month and then went quiet is not the same thing as a sampler
        that has never run.
        """
        with self._lock:
            return self._ever

    def all_rows(self):
        with self._lock:
            return list(self._rows)

    def in_window(self, window_s):
        cutoff = self.clock.now() - window_s
        return [s for s in self.all_rows() if s.sent_epoch >= cutoff]

    def last(self):
        rows = self.all_rows()
        return rows[-1] if rows else None

    def count_since(self, seconds):
        return len(self.in_window(seconds))


def window_seconds(name):
    """Window name -> seconds, or None if this service does not measure it."""
    return WINDOWS.get(name)


def encode_cursor(sample_id: str) -> str:
    return base64.urlsafe_b64encode(sample_id.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    padding = "=" * (-len(cursor) % 4)
    try:
        value = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValueError("cursor is not decodable") from None
    if not value.startswith("smp_"):
        raise ValueError("cursor does not name a sample")
    return value
