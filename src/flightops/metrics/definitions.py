"""KPI definitions.

Each metric declares its formula over additive measures, its unit, and its
*direction* (whether an increase is good, bad or neutral). Pages, signals, the
narrative engine and the Methodology page all read from this registry, so a
definition changes in one place only.

Population rules (DOT conventions):
* Scheduled flights = every non-duplicate record in the BTS reporting population.
* Arrival metrics use completed, non-diverted flights (``ArrDel15`` is populated),
  so cancelled flights are never counted as arrival delays and diversions are
  reported separately through the diversion rate.
* Delay-cause minutes are reported only for flights arriving 15+ minutes late.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from flightops.data.measures import CAUSE_MEASURES
from flightops.utils.format import fmt_compact, fmt_int, fmt_minutes, fmt_pct, fmt_ratio, fmt_score


class Direction(Enum):
    HIGHER_IS_BETTER = "higher"
    LOWER_IS_BETTER = "lower"
    NEUTRAL = "neutral"


class Unit(Enum):
    COUNT = "count"
    RATE = "rate"  # 0–1, displayed as %, compared in percentage points
    MINUTES = "minutes"  # compared as absolute minutes
    SCORE = "score"  # 0–100 index, compared in points
    RATIO = "ratio"


Values = Mapping[str, object]


def safe_div(numerator, denominator):
    """Division that yields NaN (never inf or an exception) for zero denominators."""
    if isinstance(numerator, pd.Series | np.ndarray) or isinstance(denominator, pd.Series | np.ndarray):
        num = _as_float_array(numerator)
        den = _as_float_array(denominator)
        if isinstance(den, pd.Series | np.ndarray):
            den = pd.Series(den, index=getattr(den, "index", None)) if not isinstance(den, pd.Series) else den
            den = den.where(den != 0)
        elif den == 0 or np.isnan(den):
            den = np.nan
        return num / den
    try:
        numerator, denominator = float(numerator), float(denominator)
    except (TypeError, ValueError):
        return float("nan")
    if denominator == 0 or np.isnan(denominator) or np.isnan(numerator):
        return float("nan")
    return numerator / denominator


def _as_float_array(value):
    """Series → float Series (index preserved); ndarray → float ndarray; scalar → float (NaN if invalid)."""
    if isinstance(value, pd.Series):
        return pd.to_numeric(value, errors="coerce").astype(float)
    if isinstance(value, np.ndarray):
        return pd.to_numeric(pd.Series(value), errors="coerce").astype(float).to_numpy()
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _cause_total(v: Values):
    return sum(v[m] for m in CAUSE_MEASURES.values())


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    unit: Unit
    direction: Direction
    formula: Callable[[Values], object]
    requires: tuple[str, ...]
    definition: str
    calculation: str
    short_label: str = ""

    @property
    def short(self) -> str:
        return self.short_label or self.label

    def format(self, value, compact: bool = False) -> str:
        if self.unit is Unit.RATE:
            return fmt_pct(value)
        if self.unit is Unit.MINUTES:
            return fmt_minutes(value)
        if self.unit is Unit.SCORE:
            return fmt_score(value)
        if self.unit is Unit.RATIO:
            return fmt_ratio(value)
        return fmt_compact(value) if compact else fmt_int(value)

    def available(self, columns: Iterable[str]) -> bool:
        cols = set(columns)
        return all(r in cols for r in self.requires)


_CAUSES = tuple(CAUSE_MEASURES.values())

_DEFINITIONS: tuple[MetricDef, ...] = (
    MetricDef(
        "flights", "Scheduled Flights", Unit.COUNT, Direction.NEUTRAL,
        lambda v: v["flights"], ("flights",),
        "Scheduled operations in the BTS reporting population for the selected filters.",
        "count(records), excluding records BTS flags as duplicate code-share reports",
        "Flights",
    ),
    MetricDef(
        "completion_rate", "Completion Rate", Unit.RATE, Direction.HIGHER_IS_BETTER,
        lambda v: safe_div(v["flights"] - v["cancelled"], v["flights"]), ("flights", "cancelled"),
        "Share of scheduled operations that were not cancelled. Diverted flights count as operated.",
        "(scheduled − cancelled) ÷ scheduled",
        "Completion",
    ),
    MetricDef(
        "on_time_rate", "On-Time Arrival", Unit.RATE, Direction.HIGHER_IS_BETTER,
        lambda v: safe_div(v["arr_eligible"] - v["arr_del15"], v["arr_eligible"]), ("arr_eligible", "arr_del15"),
        "Share of completed, non-diverted flights arriving less than 15 minutes after schedule (DOT convention).",
        "(completed arrivals − ArrDel15) ÷ completed arrivals; cancelled and diverted flights excluded",
        "On-time",
    ),
    MetricDef(
        "delay15_rate", "15+ Min Arrival Delay Rate", Unit.RATE, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["arr_del15"], v["arr_eligible"]), ("arr_eligible", "arr_del15"),
        "Share of completed, non-diverted flights arriving 15 or more minutes late.",
        "sum(ArrDel15) ÷ completed arrivals",
        "15+ delay",
    ),
    MetricDef(
        "severe_delay_rate", "Severe Delay Rate", Unit.RATE, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["arr_del60"], v["arr_eligible"]), ("arr_eligible", "arr_del60"),
        "Share of completed, non-diverted flights arriving 60 or more minutes late.",
        "count(ArrDelayMinutes ≥ 60) ÷ completed arrivals",
        "Severe 60+",
    ),
    MetricDef(
        "cancellation_rate", "Cancellation Rate", Unit.RATE, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["cancelled"], v["flights"]), ("flights", "cancelled"),
        "Cancelled operations as a share of scheduled operations.",
        "sum(Cancelled) ÷ scheduled",
        "Cancelled",
    ),
    MetricDef(
        "diversion_rate", "Diversion Rate", Unit.RATE, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["diverted"], v["flights"]), ("flights", "diverted"),
        "Diverted operations as a share of scheduled operations.",
        "sum(Diverted) ÷ scheduled",
        "Diverted",
    ),
    MetricDef(
        "avg_arr_delay", "Avg Positive Arrival Delay", Unit.MINUTES, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["arr_delay_min_sum"], v["arr_eligible"]), ("arr_delay_min_sum", "arr_eligible"),
        "Average minutes late at arrival across completed flights; early arrivals count as 0.",
        "sum(ArrDelayMinutes) ÷ completed arrivals",
        "Avg arr. delay",
    ),
    MetricDef(
        "avg_arr_variance", "Avg Arrival Schedule Variance", Unit.MINUTES, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["arr_delay_sum"], v["arr_eligible"]), ("arr_delay_sum", "arr_eligible"),
        "Average signed difference between actual and scheduled arrival; early arrivals are negative.",
        "sum(ArrDelay) ÷ completed arrivals",
        "Arr. variance",
    ),
    MetricDef(
        "avg_dep_delay", "Avg Positive Departure Delay", Unit.MINUTES, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["dep_delay_min_sum"], v["dep_eligible"]), ("dep_delay_min_sum", "dep_eligible"),
        "Average minutes late at gate departure; early departures count as 0.",
        "sum(DepDelayMinutes) ÷ departed flights",
        "Avg dep. delay",
    ),
    MetricDef(
        "avg_dep_variance", "Avg Departure Schedule Variance", Unit.MINUTES, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["dep_delay_sum"], v["dep_eligible"]), ("dep_delay_sum", "dep_eligible"),
        "Average signed difference between actual and scheduled gate departure.",
        "sum(DepDelay) ÷ departed flights",
        "Dep. variance",
    ),
    MetricDef(
        "dep_delay15_rate", "15+ Min Departure Delay Rate", Unit.RATE, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["dep_del15"], v["dep_eligible"]), ("dep_del15", "dep_eligible"),
        "Share of departed flights leaving the gate 15 or more minutes late.",
        "sum(DepDel15) ÷ departed flights",
        "Dep. 15+ delay",
    ),
    MetricDef(
        "avg_taxi_out", "Avg Taxi-Out", Unit.MINUTES, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["taxi_out_sum"], v["taxi_out_n"]), ("taxi_out_sum", "taxi_out_n"),
        "Average minutes from gate departure to wheels-off, for flights with taxi-out reported.",
        "sum(TaxiOut) ÷ count(TaxiOut)",
        "Taxi-out",
    ),
    MetricDef(
        "delay_minutes", "Total Delay Minutes", Unit.COUNT, Direction.LOWER_IS_BETTER,
        _cause_total, _CAUSES,
        "Reported delay-cause minutes on flights arriving 15+ minutes late (carrier, weather, NAS, security, late aircraft).",
        "sum(CarrierDelay + WeatherDelay + NASDelay + SecurityDelay + LateAircraftDelay)",
        "Delay min",
    ),
    MetricDef(
        "delay_min_per_100", "Delay Minutes per 100 Flights", Unit.COUNT, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(_cause_total(v) * 100, v["flights"]), (*_CAUSES, "flights"),
        "Reported delay-cause minutes normalized by scheduled volume, which makes airports and carriers of different size comparable.",
        "total delay minutes ÷ scheduled × 100",
        "Delay min / 100",
    ),
    MetricDef(
        "avg_delay_per_delayed", "Avg Delay per Delayed Flight", Unit.MINUTES, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(_cause_total(v), v["cause_flights"]), (*_CAUSES, "cause_flights"),
        "Average reported delay minutes among flights arriving 15+ minutes late.",
        "total delay minutes ÷ flights with reported causes",
        "Min per delayed",
    ),
    MetricDef(
        "propagation_index", "Delay Propagation Index", Unit.RATE, Direction.LOWER_IS_BETTER,
        lambda v: safe_div(v["delay_late_aircraft_min"], _cause_total(v)), _CAUSES,
        "Share of reported delay minutes coded as late-arriving aircraft, i.e. delay associated with an earlier "
        "flight arriving late. It does not reconstruct aircraft rotations.",
        "LateAircraftDelay minutes ÷ total delay minutes",
        "Propagation",
    ),
    MetricDef(
        "reliability_score", "Operational Reliability Score", Unit.SCORE, Direction.HIGHER_IS_BETTER,
        lambda v: safe_div((v["arr_eligible"] - v["arr_del15"]) * 100, v["flights"]),
        ("arr_eligible", "arr_del15", "flights"),
        "Of every 100 scheduled flights, how many operated and arrived on time. Cancellations and diversions "
        "count against the score, so it penalizes airlines that protect on-time rate by cancelling.",
        "on-time arrivals ÷ scheduled flights × 100",
        "Reliability",
    ),
    MetricDef(
        "avg_sched_block", "Avg Scheduled Block Time", Unit.MINUTES, Direction.NEUTRAL,
        lambda v: safe_div(v["sched_block_sum"], v["block_n"]), ("sched_block_sum", "block_n"),
        "Average scheduled gate-to-gate minutes for completed flights.",
        "sum(CRSElapsedTime) ÷ completed flights",
        "Sched. block",
    ),
    MetricDef(
        "avg_actual_block", "Avg Actual Elapsed Time", Unit.MINUTES, Direction.NEUTRAL,
        lambda v: safe_div(v["actual_block_sum"], v["block_n"]), ("actual_block_sum", "block_n"),
        "Average actual gate-to-gate minutes for completed flights.",
        "sum(ActualElapsedTime) ÷ completed flights",
        "Actual block",
    ),
    MetricDef(
        "avg_distance", "Avg Distance", Unit.COUNT, Direction.NEUTRAL,
        lambda v: safe_div(v["distance_sum"], v["flights"]), ("distance_sum", "flights"),
        "Average scheduled flight distance in statute miles.",
        "sum(Distance) ÷ scheduled",
        "Distance (mi)",
    ),
)

METRICS: dict[str, MetricDef] = {m.key: m for m in _DEFINITIONS}


def get_metric(key: str) -> MetricDef:
    try:
        return METRICS[key]
    except KeyError as exc:
        raise KeyError(f"Unknown metric {key!r}") from exc


def compute(key: str, values: Values):
    """Compute one metric from a mapping (or DataFrame row/columns) of summed measures."""
    metric = get_metric(key)
    return metric.formula(values)


def add_metrics(df: pd.DataFrame, keys: Iterable[str] | None = None) -> pd.DataFrame:
    """Return ``df`` with a column per metric whose required measures are present."""
    out = df.copy()
    for key in keys or METRICS:
        metric = METRICS[key]
        if metric.available(out.columns):
            out[key] = pd.to_numeric(metric.formula(out), errors="coerce") if len(out) else pd.Series(dtype=float)
    return out


def cause_mix(values: Values) -> dict[str, float]:
    """Delay-cause share of total reported delay minutes (0–1 per cause)."""
    total = _cause_total(values)
    return {cause: safe_div(values[measure], total) for cause, measure in CAUSE_MEASURES.items()}
