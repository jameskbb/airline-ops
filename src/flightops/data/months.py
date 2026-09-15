"""Reporting-month helpers. A month is represented as a ``datetime.date`` on day 1."""

from __future__ import annotations

import datetime as dt
import re

_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


def parse_month(value: str) -> dt.date:
    """Parse ``YYYY-MM`` into the first day of that month."""
    match = _MONTH_RE.match(value.strip())
    if not match:
        raise ValueError(f"Expected YYYY-MM, got {value!r}")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"Month out of range in {value!r}")
    return dt.date(year, month, 1)


def month_key(month: dt.date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def add_months(month: dt.date, n: int) -> dt.date:
    index = month.year * 12 + (month.month - 1) + n
    return dt.date(index // 12, index % 12 + 1, 1)


def month_range(start: dt.date, end: dt.date) -> list[dt.date]:
    """Inclusive list of months from ``start`` to ``end``."""
    if start > end:
        return []
    months, current = [], start.replace(day=1)
    while current <= end:
        months.append(current)
        current = add_months(current, 1)
    return months


def months_between(start: dt.date, end: dt.date) -> int:
    """Number of months in the inclusive span."""
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def month_end(month: dt.date) -> dt.date:
    return add_months(month, 1) - dt.timedelta(days=1)


def format_month(month: dt.date, style: str = "long") -> str:
    return month.strftime("%B %Y" if style == "long" else "%b %Y")


def format_period(start: dt.date, end: dt.date) -> str:
    if start == end:
        return format_month(start)
    if start.year == end.year:
        return f"{start.strftime('%b')}–{end.strftime('%b %Y')}"
    return f"{start.strftime('%b %Y')}–{end.strftime('%b %Y')}"
