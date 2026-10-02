"""Percentiles and the confirmation rate.

Every figure this module returns is derived from the samples it is handed.
There is no constant in this file that could be mistaken for a confirmation
time, and `test_percentiles_are_measured_not_constant` enforces that by
scanning the package for exactly that.

Percentile definition, stated once and published verbatim at
`/v1/finality/method`: linear interpolation between the two closest ranks of
the sorted confirmed samples --

    h  = (n - 1) * p / 100
    Pp = v[floor(h)] + (h - floor(h)) * (v[ceil(h)] - v[floor(h)])

rounded to the nearest whole millisecond. `h` is a Fraction, so the rank is
exact and does not drift with floating point. This is the definition numpy and
pandas use by default. It is written down because a competitor using
nearest-rank will get a different p99 from the same data, and the reader has
to be able to tell why rather than guess which of us is wrong.

The consequence the spec cares about: with 99 samples at 400ms and one at
8000ms, interpolation puts p99 above 400 and nearest-rank puts it at 400. The
slow tail is real and it shows.
"""

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

RATE_DP = 5            # decimal places in confirmation_rate; a format, not a measurement
PERCENT = 100          # per-cent, the unit of p50/p90/p95/p99
RANKS = (50, 90, 95, 99)


def percentile(values, p):
    """Linear-interpolation percentile over an unsorted list of ints.

    Returns None for an empty list -- never 0, which would read as a
    measurement of zero milliseconds.
    """
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return int(ordered[0])
    h = Fraction(n - 1) * Fraction(p, PERCENT)
    lo = h.numerator // h.denominator
    if h == lo:
        return int(ordered[lo])
    exact = Fraction(ordered[lo]) + (h - lo) * Fraction(ordered[lo + 1] - ordered[lo])
    return _round_half_up(exact)


def _round_half_up(value: Fraction) -> int:
    """Exact Fraction -> nearest whole millisecond, .5 away from zero."""
    as_decimal = Decimal(value.numerator) / Decimal(value.denominator)
    return int(as_decimal.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def elapsed_summary(confirmed_ms):
    """The `elapsed_ms` block. All six figures, or all six null."""
    if not confirmed_ms:
        return {k: None for k in ("p50", "p90", "p95", "p99", "max", "min")}
    out = {f"p{r}": percentile(confirmed_ms, r) for r in RANKS}
    out["max"] = int(max(confirmed_ms))
    out["min"] = int(min(confirmed_ms))
    return out


def confirmation_rate(confirmed: int, total: int) -> str:
    """confirmed/total as a decimal *string*, five places, never a float.

    The division is done in Decimal, so 1434/1436 renders "0.99861" and not
    0.9986072423398329 -- a float here is how a rate starts disagreeing with
    itself between two readers.
    """
    if total <= 0:
        return "0." + "0" * RATE_DP
    quantum = Decimal(1).scaleb(-RATE_DP)
    value = (Decimal(confirmed) / Decimal(total)).quantize(
        quantum, rounding=ROUND_HALF_UP
    )
    return f"{value:.{RATE_DP}f}"
