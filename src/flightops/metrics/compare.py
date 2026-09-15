"""Direction-aware comparisons between a current value and a reference value."""

from __future__ import annotations

import math
from dataclasses import dataclass

from flightops.metrics.definitions import Direction, MetricDef, Unit
from flightops.utils.format import fmt_pct_change, fmt_pts, fmt_signed

# Changes smaller than these are reported as flat rather than favorable/unfavorable.
FLAT_TOLERANCE = {
    Unit.RATE: 0.05,  # points
    Unit.SCORE: 0.05,
    Unit.MINUTES: 0.05,  # minutes
    Unit.COUNT: 0.001,  # fraction
    Unit.RATIO: 0.001,
}


@dataclass(frozen=True)
class Delta:
    current: float
    reference: float
    change: float  # pts for rates/scores, minutes for averages, fraction for counts
    kind: str  # "pts" | "min" | "pct"
    favorable: bool | None  # None = neutral metric or flat change
    text: str


def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def compare(metric: MetricDef, current, reference) -> Delta | None:
    """Compare two values of ``metric``. Returns ``None`` if either value is missing.

    * Rates change in percentage points (never misleading relative % on a rate).
    * Scores change in points; minute averages in minutes; counts in relative %.
    """
    if _missing(current) or _missing(reference):
        return None
    current, reference = float(current), float(reference)
    if metric.unit is Unit.RATE:
        change, kind, text = (current - reference) * 100, "pts", fmt_pts((current - reference) * 100)
    elif metric.unit is Unit.SCORE:
        change, kind, text = current - reference, "pts", fmt_pts(current - reference)
    elif metric.unit is Unit.MINUTES:
        change, kind, text = current - reference, "min", fmt_signed(current - reference, 1, " min")
    else:
        if reference == 0:
            return None
        change = (current - reference) / abs(reference)
        kind, text = "pct", fmt_pct_change(change)

    favorable: bool | None
    if metric.direction is Direction.NEUTRAL or abs(change) < FLAT_TOLERANCE[metric.unit]:
        favorable = None
    elif metric.direction is Direction.HIGHER_IS_BETTER:
        favorable = change > 0
    else:
        favorable = change < 0
    return Delta(current, reference, change, kind, favorable, text)
