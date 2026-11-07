"""
Indian Standard Time (IST, UTC+05:30) date and time helpers.
Conforms to SYSTEM_DESIGN.md Section 3.1, Section 5.2, and PRD.md Section 9.2.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
import re

import config

# Indian Standard Time (UTC+05:30, no DST)
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def ist_today() -> str:
    """Returns today's date in IST formatted as YYYY-MM-DD."""
    return datetime.now(IST).strftime("%Y-%m-%d")


def is_valid_date_str(date_str: Optional[str]) -> bool:
    """Checks whether date_str is a valid YYYY-MM-DD calendar date."""
    if not date_str or not isinstance(date_str, str):
        return False
    if not DATE_REGEX.match(date_str):
        return False
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def date_str_to_ist_epoch(date_str: str, end_of_day: bool = False) -> int:
    """
    Converts YYYY-MM-DD string to epoch milliseconds in IST.
    If end_of_day is False, returns 00:00:00.000 IST.
    If end_of_day is True, returns 23:59:59.999 IST.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt_ist = dt.replace(hour=23, minute=59, second=59, microsecond=999000, tzinfo=IST)
    else:
        dt_ist = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=IST)
    return int(dt_ist.timestamp() * 1000)


def epoch_to_ist_str(epoch_ms: int, include_time: bool = False) -> str:
    """Converts epoch ms to IST date string."""
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=IST)
    if include_time:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d")


def ist_day_of(epoch_ms: int) -> str:
    """Returns calendar day YYYY-MM-DD in IST for a given epoch ms."""
    return epoch_to_ist_str(epoch_ms, include_time=False)


def validate_and_clamp_range(from_str: Optional[str], to_str: Optional[str]) -> tuple[str, str, int, int]:
    """
    Validates and clamps date range per PRD Section 9.2 and SYSTEM_DESIGN.md Section 5.2.
    - from >= 2026-09-14 (clamps up to CUTOFF_DATE)
    - to <= today (IST) (clamps down to ist_today())
    - If from > to after clamping -> raises ValueError("invalid range: from > to")
    
    Returns (clamped_from_str, clamped_to_str, from_epoch_ms, to_epoch_ms).
    """
    if not from_str or not to_str:
        raise ValueError("missing from or to")

    if not is_valid_date_str(from_str) or not is_valid_date_str(to_str):
        raise ValueError("invalid date format")

    today = ist_today()

    # Enforce clamping
    clamped_from = max(from_str, config.CUTOFF_DATE)
    clamped_to = min(to_str, today)

    if clamped_from > clamped_to:
        raise ValueError("invalid range: from > to")

    from_ts = date_str_to_ist_epoch(clamped_from, end_of_day=False)
    to_ts = date_str_to_ist_epoch(clamped_to, end_of_day=True)

    return clamped_from, clamped_to, from_ts, to_ts

