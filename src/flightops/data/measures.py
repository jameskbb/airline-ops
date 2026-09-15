"""Additive measures stored in every fact table.

Facts only hold *additive* quantities (counts and minute sums). Every ratio or
average is derived later by ``flightops.metrics`` from summed measures, so any
aggregate (a day, a quarter, a carrier at an airport) re-derives KPIs correctly
instead of averaging averages.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Measure:
    name: str
    sql: str  # aggregate expression over the typed staging table
    description: str
    core: bool = False  # included in the compact profile table


MEASURES: tuple[Measure, ...] = (
    Measure("flights", "count(*)", "Scheduled operations (non-duplicate records)", core=True),
    Measure("cancelled", "sum(cancelled)", "Cancelled operations", core=True),
    Measure("cancelled_carrier", "count_if(cancelled = 1 AND cancel_code = 'A')", "Cancellations coded A (carrier)"),
    Measure("cancelled_weather", "count_if(cancelled = 1 AND cancel_code = 'B')", "Cancellations coded B (weather)"),
    Measure("cancelled_nas", "count_if(cancelled = 1 AND cancel_code = 'C')", "Cancellations coded C (NAS)"),
    Measure("cancelled_security", "count_if(cancelled = 1 AND cancel_code = 'D')", "Cancellations coded D (security)"),
    Measure("diverted", "sum(diverted)", "Diverted operations", core=True),
    Measure("arr_eligible", "count(arr_del15)", "Completed, non-diverted flights with an arrival delay flag", core=True),
    Measure("arr_del15", "sum(arr_del15)", "Arrivals 15+ minutes late", core=True),
    Measure("arr_del60", "count_if(arr_delay_min >= 60)", "Arrivals 60+ minutes late", core=True),
    Measure("arr_delay_sum", "sum(arr_delay)", "Signed arrival schedule variance, minutes (early is negative)"),
    Measure("arr_delay_min_sum", "sum(arr_delay_min)", "Positive arrival delay minutes (early = 0)", core=True),
    Measure("dep_eligible", "count(dep_del15)", "Departed flights with a departure delay flag", core=True),
    Measure("dep_del15", "sum(dep_del15)", "Departures 15+ minutes late", core=True),
    Measure("dep_delay_sum", "sum(dep_delay)", "Signed departure schedule variance, minutes"),
    Measure("dep_delay_min_sum", "sum(dep_delay_min)", "Positive departure delay minutes (early = 0)", core=True),
    Measure("taxi_out_sum", "sum(taxi_out)", "Taxi-out minutes"),
    Measure("taxi_out_n", "count(taxi_out)", "Flights with taxi-out reported"),
    Measure("block_n", "count(actual_elapsed)", "Flights with actual elapsed time"),
    Measure(
        "sched_block_sum",
        "sum(crs_elapsed) FILTER (WHERE actual_elapsed IS NOT NULL)",
        "Scheduled block minutes for flights with actual elapsed time",
    ),
    Measure("actual_block_sum", "sum(actual_elapsed)", "Actual elapsed (gate-to-gate) minutes"),
    Measure("distance_sum", "sum(distance)", "Scheduled distance, miles"),
    Measure("cause_flights", "count(delay_carrier)", "Flights with reported delay cause minutes", core=True),
    Measure("delay_carrier_min", "sum(delay_carrier)", "Reported carrier delay minutes", core=True),
    Measure("delay_weather_min", "sum(delay_weather)", "Reported extreme-weather delay minutes", core=True),
    Measure("delay_nas_min", "sum(delay_nas)", "Reported National Aviation System delay minutes", core=True),
    Measure("delay_security_min", "sum(delay_security)", "Reported security delay minutes", core=True),
    Measure("delay_late_aircraft_min", "sum(delay_late_aircraft)", "Reported late-arriving aircraft delay minutes", core=True),
)

MEASURE_NAMES: tuple[str, ...] = tuple(m.name for m in MEASURES)
CORE_MEASURE_NAMES: tuple[str, ...] = tuple(m.name for m in MEASURES if m.core)

# Per-grain measure sets. Grains carry only what their consumers need so the
# committed analytical layer stays small; block time, distance and cancellation
# reason codes live on the monthly route grain.
_MONTHLY_ONLY = {
    "cancelled_carrier", "cancelled_weather", "cancelled_nas", "cancelled_security",
    "block_n", "sched_block_sum", "actual_block_sum", "distance_sum",
}
DAILY_MEASURE_NAMES: tuple[str, ...] = tuple(n for n in MEASURE_NAMES if n not in _MONTHLY_ONLY)
HOURLY_MEASURE_NAMES: tuple[str, ...] = (*CORE_MEASURE_NAMES, "taxi_out_sum", "taxi_out_n")
PROFILE_MEASURE_NAMES: tuple[str, ...] = (
    "flights", "cancelled", "diverted", "arr_eligible", "arr_del15", "arr_del60", "arr_delay_min_sum",
)

CAUSE_MEASURES: dict[str, str] = {
    "carrier": "delay_carrier_min",
    "weather": "delay_weather_min",
    "nas": "delay_nas_min",
    "security": "delay_security_min",
    "late_aircraft": "delay_late_aircraft_min",
}

CAUSE_LABELS: dict[str, str] = {
    "carrier": "Carrier",
    "weather": "Extreme Weather",
    "nas": "NAS",
    "security": "Security",
    "late_aircraft": "Late Aircraft",
}


def aggregate_sql(names: tuple[str, ...] = MEASURE_NAMES) -> str:
    """SELECT-list fragment computing the named measures from staging rows."""
    by_name = {m.name: m for m in MEASURES}
    return ",\n    ".join(f"CAST(coalesce({by_name[n].sql}, 0) AS INTEGER) AS {n}" for n in names)


def rollup_sql(names: tuple[str, ...] = MEASURE_NAMES, prefix: str = "") -> str:
    """SELECT-list fragment re-summing stored measures (for any coarser grain)."""
    return ",\n    ".join(f"CAST(sum({prefix}{n}) AS BIGINT) AS {n}" for n in names)
