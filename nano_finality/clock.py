"""Time, injectable, so the sampler's tests advance it instead of sleeping.

The whole suite runs against `FakeClock`, which is why 12 tests covering a
30-second timeout and a 30-day retention window finish in well under a second.
"""

import datetime
import time


class Clock:
    def now(self) -> float:
        """Wall clock, for the timestamps that go on a sample."""
        return time.time()

    def mono_ms(self) -> int:
        """A monotonic millisecond counter, for durations only.

        Durations are measured on this and never on `now()`: an NTP step
        during a sample would otherwise turn up as a confirmation time, and
        one published p99 of -400ms would cost more credibility than the
        whole service buys.
        """
        return time.monotonic_ns() // 1_000_000

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now_iso(self) -> str:
        return self.iso(self.now())

    def iso(self, epoch: float) -> str:
        """RFC3339 UTC with millisecond precision, as the spec's Sample requires."""
        dt = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class FakeClock(Clock):
    """Virtual time. `sleep` advances it; nothing ever blocks.

    Time is carried as an integer count of milliseconds, not a float of
    seconds. Thirty additions of 0.05 come to 1.4999999999999998, which made
    a 1500ms latency measure as 1550ms and would have put drift into every
    figure the tests assert. Milliseconds are the resolution the service
    publishes, so they are the resolution the test clock keeps.
    """

    def __init__(self, start=1_800_000_000.0):
        self._ms = int(round(float(start) * 1000))

    def now(self) -> float:
        return self._ms / 1000.0

    def mono_ms(self) -> int:
        return self._ms

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> float:
        self._ms += int(round(float(seconds) * 1000))
        return self.now()


def iso_seconds(epoch: float) -> str:
    """RFC3339 UTC to the second, for `as_of` -- which is not a measurement."""
    import datetime

    return (
        datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
