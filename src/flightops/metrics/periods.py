"""Reporting periods and their comparison periods.

BTS publishes monthly, so analytical periods are whole reporting months. A period's
comparison baselines are the immediately preceding period of equal length and the
same months one year earlier.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from flightops.data.months import add_months, format_period, month_end, month_key, months_between

PRESETS: dict[str, int | None] = {
    "Latest month": 1,
    "Last 3 months": 3,
    "Last 6 months": 6,
    "Last 12 months": 12,
    "Custom range": None,
}


@dataclass(frozen=True)
class Period:
    start: dt.date  # first day of first month
    end: dt.date  # first day of last month

    def __post_init__(self) -> None:
        if self.start.day != 1 or self.end.day != 1:
            raise ValueError("Period bounds must be month starts")
        if self.start > self.end:
            raise ValueError("Period start is after end")

    @property
    def months(self) -> int:
        return months_between(self.start, self.end)

    @property
    def first_day(self) -> dt.date:
        return self.start

    @property
    def last_day(self) -> dt.date:
        return month_end(self.end)

    @property
    def label(self) -> str:
        return format_period(self.start, self.end)

    @property
    def key(self) -> str:
        return month_key(self.start) if self.months == 1 else f"{month_key(self.start)}..{month_key(self.end)}"

    def previous(self) -> Period:
        """Immediately preceding period of equal length."""
        return Period(add_months(self.start, -self.months), add_months(self.start, -1))

    def prior_year(self) -> Period:
        return Period(add_months(self.start, -12), add_months(self.end, -12))

    def trailing(self, months: int) -> Period:
        """The ``months`` months immediately before this period."""
        return Period(add_months(self.start, -months), add_months(self.start, -1))

    def within(self, first: dt.date, last: dt.date) -> bool:
        return first <= self.start and self.end <= last


def preset_period(preset: str, latest: dt.date, earliest: dt.date) -> Period:
    months = PRESETS.get(preset) or 1
    start = max(add_months(latest, -(months - 1)), earliest)
    return Period(start, latest)
