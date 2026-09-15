"""Number formatting used everywhere in the UI and narrative.

Examples: ``2,418,391`` · ``78.4%`` · ``18.6 min`` · ``+3.2 pts`` · ``14.2M``.
"""

from __future__ import annotations

import math

DASH = "—"


def _missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def fmt_int(value) -> str:
    return DASH if _missing(value) else f"{round(value):,}"


def fmt_compact(value, digits: int = 1) -> str:
    if _missing(value):
        return DASH
    magnitude = abs(value)
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            return f"{value / threshold:.{digits}f}{suffix}"
    return f"{value:,.0f}"


def fmt_pct(value, digits: int = 1) -> str:
    """Format a 0–1 rate as a percentage."""
    return DASH if _missing(value) else f"{value * 100:.{digits}f}%"


def fmt_minutes(value, digits: int = 1) -> str:
    return DASH if _missing(value) else f"{value:.{digits}f} min"


def fmt_score(value, digits: int = 1) -> str:
    return DASH if _missing(value) else f"{value:.{digits}f}"


def fmt_ratio(value, digits: int = 2) -> str:
    return DASH if _missing(value) else f"{value:.{digits}f}×"


def fmt_signed(value, digits: int = 1, suffix: str = "") -> str:
    if _missing(value):
        return DASH
    rounded = round(value, digits)
    if rounded == 0:
        rounded = 0.0  # avoid "-0.0"
    return f"{rounded:+.{digits}f}{suffix}"


def fmt_pts(value, digits: int = 1) -> str:
    """Percentage-point change; ``value`` is already in points."""
    return fmt_signed(value, digits, " pts")


def fmt_pct_change(value, digits: int = 1) -> str:
    """Relative change; ``value`` is a fraction (0.041 → +4.1%)."""
    return DASH if _missing(value) else fmt_signed(value * 100, digits, "%")


def fmt_ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
