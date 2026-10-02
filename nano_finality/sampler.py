"""The sampler: one minimal self-payment, timed.

The timing definition, applied here and published verbatim at
`/v1/finality/method`:

    elapsed_ms is measured from the moment the send RPC is **dispatched** to
    the moment the node **first reports the block confirmed**.

Not from block construction, not from local signing, not from the moment the
send RPC returns. A competitor measuring from a different start point gets a
different number, and the reader has to be able to see which.

`t0` is taken on the line before `node.send(...)` for that reason, and it is
the only place in this package a start time is taken.
"""

import os

from . import amounts, samples
from .node import NodeError

DEFAULT_INTERVAL_S = 60
DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_POLL_MS = 50
DEFAULT_AMOUNT_RAW = 1          # one raw, the smallest unit there is


class Sampler:
    def __init__(self, node, store, clock, source, destination,
                 timeout_ms=DEFAULT_TIMEOUT_MS, poll_ms=DEFAULT_POLL_MS,
                 amount_raw=DEFAULT_AMOUNT_RAW, interval_s=DEFAULT_INTERVAL_S):
        self.node = node
        self.store = store
        self.clock = clock
        self.source = source
        self.destination = destination
        self.timeout_ms = int(timeout_ms)
        self.poll_ms = int(poll_ms)
        self.amount_raw = amounts.parse_raw(amount_raw)
        self.interval_s = float(interval_s)
        self.started_at = None

    def run_once(self):
        """Take one sample. Always records a row -- confirmed, timeout or error."""
        if self.started_at is None:
            self.started_at = self.clock.now()

        sent_epoch = self.clock.now()
        base = {
            "id": samples.new_sample_id(),
            "sent_at": self.clock.iso(sent_epoch),
            "sent_epoch": sent_epoch,
            "amount_raw": amounts.format_raw(self.amount_raw),
            "node": self.node.name,
            "timeout_ms": self.timeout_ms,
            "confirmed_at": None,
            "elapsed_ms": None,
            "block_hash": None,
            "error": None,
        }

        # --- the clock starts here, on the line before the dispatch ---
        t0_ms = self.clock.mono_ms()
        try:
            block_hash = self.node.send(self.source, self.destination,
                                        self.amount_raw)
        except NodeError as exc:
            base["error"] = str(exc)
            return self.store.add(samples.Sample(outcome=samples.ERROR, **base))

        base["block_hash"] = block_hash.upper()
        deadline_ms = t0_ms + self.timeout_ms
        while True:
            try:
                is_confirmed = self.node.confirmed(block_hash)
            except NodeError as exc:
                base["error"] = str(exc)
                return self.store.add(samples.Sample(outcome=samples.ERROR, **base))
            now_ms = self.clock.mono_ms()
            if is_confirmed:
                # --- and stops here, the first time the node says confirmed ---
                base["confirmed_at"] = self.clock.iso(self.clock.now())
                base["elapsed_ms"] = now_ms - t0_ms
                return self.store.add(samples.Sample(
                    outcome=samples.CONFIRMED, **base))
            if now_ms >= deadline_ms:
                # The block may well confirm a second later. It is recorded a
                # timeout anyway and it stays in the denominator: a cutoff
                # that quietly waits a bit longer for a slow sample is how a
                # confirmation rate becomes 100%.
                base["error"] = f"not confirmed within {self.timeout_ms}ms"
                return self.store.add(samples.Sample(
                    outcome=samples.TIMEOUT, **base))
            self.clock.sleep(self.poll_ms / 1000.0)

    def run_forever(self, stop_event):        # pragma: no cover - entry point
        while not stop_event.is_set():
            try:
                self.run_once()
            except Exception:                  # noqa: BLE001 - the loop outlives any one sample
                pass
            stop_event.wait(self.interval_s)


def from_env(node, store, clock):              # pragma: no cover - entry point
    """Build a sampler from the environment. Nothing is read from a file."""
    source = os.environ.get("FINALITY_SOURCE_ACCOUNT")
    destination = os.environ.get("FINALITY_DEST_ACCOUNT")
    if not source or not destination:
        raise SystemExit(
            "FINALITY_SOURCE_ACCOUNT and FINALITY_DEST_ACCOUNT are not set; "
            "refusing to start. The sampler pays itself and needs both."
        )
    return Sampler(
        node, store, clock, source, destination,
        timeout_ms=int(os.environ.get("FINALITY_SAMPLE_TIMEOUT_MS",
                                      DEFAULT_TIMEOUT_MS)),
        poll_ms=int(os.environ.get("FINALITY_POLL_INTERVAL_MS", DEFAULT_POLL_MS)),
        amount_raw=os.environ.get("FINALITY_SAMPLE_AMOUNT_RAW",
                                  DEFAULT_AMOUNT_RAW),
        interval_s=float(os.environ.get("FINALITY_SAMPLE_INTERVAL_S",
                                        DEFAULT_INTERVAL_S)),
    )
