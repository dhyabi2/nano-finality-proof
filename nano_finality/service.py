"""The service: one method per endpoint, no HTTP in sight.

The HTTP layer and the end-to-end check both drive these methods, so a rule
proved here holds on every surface.

The one distinction this module exists to keep straight: `no_samples_yet` (503)
means the sampler has *never* recorded anything, and is a broken service.
`samples: 0` (200) means this particular window was quiet. Conflating them is
how an outage reads as a clean hour, so `finality()` checks `ever_sampled`
before it looks at the window, and never the other way round.
"""

from . import clock as clockmod
from . import errors, method, samples, stats

HONEST_NOTE = (
    "Nano has no fee, so it has no fee-based finality signal. It does not "
    "have one and we do not claim it does. What is measured here is time to "
    "confirmation and how often confirmation failed, against the node named "
    "above, which you can query yourself."
)

LOW_CONFIDENCE_REASON = (
    f"fewer than {samples.LOW_CONFIDENCE_N} samples in this window"
)


class Service:
    def __init__(self, sampler, store, clock, base_url="http://localhost:8080",
                 compare_sampler=None, compare_store=None):
        self.sampler = sampler
        self.store = store
        self.clock = clock
        self.base_url = base_url.rstrip("/")
        self.compare_sampler = compare_sampler
        self.compare_store = compare_store

    # -- config ------------------------------------------------------------
    def _cfg(self, sampler=None, store=None):
        sampler = sampler or self.sampler
        store = store or self.store
        return {
            "source": sampler.source,
            "destination": sampler.destination,
            "amount_raw": str(sampler.amount_raw),
            "interval_s": sampler.interval_s,
            "timeout_ms": sampler.timeout_ms,
            "poll_ms": sampler.poll_ms,
            "node": sampler.node.name,
            "retention_s": store.retention_s,
        }

    # -- the summary -------------------------------------------------------
    def finality(self, window=samples.DEFAULT_WINDOW, store=None, sampler=None):
        store = store or self.store
        sampler = sampler or self.sampler
        seconds = samples.window_seconds(window)
        if seconds is None:
            raise errors.invalid_window(window, sorted(samples.WINDOWS))
        if not store.ever_sampled:
            raise errors.no_samples_yet()

        rows = store.in_window(seconds)
        confirmed = [r for r in rows if r.outcome == samples.CONFIRMED]
        timeouts = [r for r in rows if r.outcome == samples.TIMEOUT]
        failed = [r for r in rows if r.outcome == samples.ERROR]

        body = {
            "window": window,
            "as_of": clockmod.iso_seconds(self.clock.now()),
            "node": sampler.node.name,
            "samples": len(rows),
            "confirmed": len(confirmed),
            "timeouts": len(timeouts),
            "errors": len(failed),
            # The denominator is every row in the window. A percentile without
            # this number beside it is a claim, so the two are built together
            # and there is no code path that emits one without the other.
            "confirmation_rate": stats.confirmation_rate(len(confirmed),
                                                         len(rows)),
            "elapsed_ms": stats.elapsed_summary(
                [r.elapsed_ms for r in confirmed]),
            "timeout_ms": sampler.timeout_ms,
            "method_url": f"{self.base_url}/v1/finality/method",
            "raw_samples_url": (
                f"{self.base_url}/v1/finality/samples?window={window}"),
            "honest_note": HONEST_NOTE,
        }
        if len(rows) < samples.LOW_CONFIDENCE_N:
            # Served anyway, flagged. The window is never widened to reach a
            # nicer n: the reader asked about this hour, not a better one.
            body["low_confidence"] = True
            body["low_confidence_reason"] = LOW_CONFIDENCE_REASON
        return body

    # -- the raw rows ------------------------------------------------------
    def samples_page(self, window=samples.DEFAULT_WINDOW,
                     limit=samples.LIMIT_DEFAULT, cursor=None, store=None):
        store = store or self.store
        seconds = samples.window_seconds(window)
        if seconds is None:
            raise errors.invalid_window(window, sorted(samples.WINDOWS))
        if not isinstance(limit, int) or isinstance(limit, bool) or not (
            samples.LIMIT_MIN <= limit <= samples.LIMIT_MAX
        ):
            raise errors.invalid_limit(samples.LIMIT_MIN, samples.LIMIT_MAX)

        rows = store.in_window(seconds)
        if cursor:
            try:
                after = samples.decode_cursor(cursor)
            except ValueError:
                raise errors.invalid_cursor() from None
            index = next((i for i, r in enumerate(rows) if r.id == after), None)
            if index is None:
                raise errors.invalid_cursor()
            rows = rows[index + 1:]

        page, rest = rows[:limit], rows[limit:]
        return {
            "window": window,
            "samples": [r.to_json() for r in page],
            "next_cursor": samples.encode_cursor(page[-1].id) if page and rest
            else None,
            "method_url": f"{self.base_url}/v1/finality/method",
            "note": (
                "Every sample in the window, including timeouts and errors. "
                "Take any block_hash here to a node we do not run and check "
                "it yourself -- that is what makes the summary checkable "
                "rather than another assertion."
            ),
        }

    # -- the method --------------------------------------------------------
    def method_json(self):
        return method.method_json(self._cfg())

    def method_text(self):
        return method.method_text(self._cfg())

    # -- the second node ---------------------------------------------------
    def compare(self, window=samples.DEFAULT_WINDOW):
        if not self.compare_sampler or not self.compare_store:
            raise errors.not_configured()
        return {
            "window": window,
            "as_of": clockmod.iso_seconds(self.clock.now()),
            "nodes": [
                self.finality(window),
                self.finality(window, store=self.compare_store,
                              sampler=self.compare_sampler),
            ],
            "note": (
                "The same measurement, run against two independently operated "
                "nodes. Two nodes agreeing is worth more than one node "
                "asserting. Where they disagree, both figures are shown and "
                "neither is averaged away."
            ),
        }

    # -- health ------------------------------------------------------------
    def health(self):
        last = self.store.last()
        try:
            reachable = self.sampler.node.reachable()
        except Exception:                      # noqa: BLE001
            reachable = False
        return {
            "ok": True,
            "sampler_running": self.sampler.started_at is not None,
            "last_sample_at": last.sent_at if last else None,
            "samples_24h": self.store.count_since(samples.WINDOWS["24h"]),
            "node": "reachable" if reachable else "unreachable",
        }
