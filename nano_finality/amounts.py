"""Raw amounts, as integers.

1 XNO = 10**30 raw. A float holds about 15-17 significant digits, so a raw
amount put through one loses digits silently. Nothing in this package ever
converts a raw amount to a float: the sampler sends an integer count of raw
and publishes it as the decimal *string* of that integer.
"""

RAW_PER_XNO = 10**30


class AmountError(ValueError):
    """A raw amount that is not a usable integer."""


def parse_raw(text) -> int:
    """A raw *string* (or int) -> int. Refuses floats outright."""
    if isinstance(text, bool):
        raise AmountError("raw amount must be an integer, not a bool")
    if isinstance(text, int):
        value = text
    elif isinstance(text, str):
        s = text.strip()
        if not s or not s.isdigit():
            raise AmountError(f"raw amount must be a whole number of raw: {text!r}")
        value = int(s)
    else:
        raise AmountError(
            "raw amount must be an int or a digit string; a float cannot hold "
            "30 significant digits"
        )
    if value <= 0:
        raise AmountError("raw amount must be positive")
    return value


def format_raw(raw: int) -> str:
    """Integer raw -> its exact decimal string. No float, no rounding."""
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise AmountError("raw amount must be an int")
    if raw < 0:
        raise AmountError("raw amount must not be negative")
    return str(raw)
