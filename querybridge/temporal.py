"""Exact timestamp conversion for QueryBridge's SQLite query connections."""

import re
from datetime import datetime, timezone


_AWARE_ISO = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:[Zz]|[+-][0-9]{2}:[0-5][0-9])"
)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def utc_microseconds(value: object) -> int | None:
    """Return UTC microseconds since the epoch, or NULL for unsupported input.

    Accept offset-aware ISO date-times with up to six fractional digits. Naive
    values require a timezone policy and are deliberately not interpreted.
    Integer timedelta arithmetic preserves both pre-epoch and subsecond bounds.
    """
    if not isinstance(value, str) or len(value) > 32 or not _AWARE_ISO.fullmatch(value):
        return None
    try:
        instant = datetime.fromisoformat(value.upper())
    except ValueError:
        return None
    delta = instant - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
