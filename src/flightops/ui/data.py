"""Cached data access for pages.

``st.cache_resource`` holds one DuckDB-backed store per process; query results
are memoized with ``st.cache_data`` keyed on small hashable arguments, so reruns
triggered by widget changes reuse results instead of rescanning Parquet.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from flightops.analytics import signals as sig
from flightops.analytics.facts import build_brief_facts
from flightops.config import MIN_AIRPORT_DEPARTURES_PER_MONTH, MIN_CARRIER_FLIGHTS_PER_MONTH
from flightops.data.months import add_months
from flightops.data.store import Store
from flightops.metrics.periods import Period

FilterKey = tuple[tuple[str, object], ...]


def fkey(**filters) -> FilterKey:
    """Hashable, order-independent filter key; ``None`` values are dropped."""
    items = []
    for k, v in sorted(filters.items()):
        if v is None:
            continue
        items.append((k, tuple(v) if isinstance(v, list | set) else v))
    return tuple(items)


@st.cache_resource(show_spinner=False)
def store() -> Store:
    return Store()


@st.cache_data(show_spinner=False, max_entries=1024)
def agg(table: str, start: dt.date, end: dt.date, filters: FilterKey = (), by: tuple[str, ...] = ()) -> pd.DataFrame:
    return store().aggregate(table, start, end, dict(filters), by)


@st.cache_data(show_spinner=False, max_entries=1024)
def totals(table: str, start: dt.date, end: dt.date, filters: FilterKey = ()) -> dict:
    return store().totals(table, start, end, dict(filters))


def period_totals(period: Period | None, filters: FilterKey = (), table: str = "fact_route_monthly") -> dict:
    if period is None or not in_range(period):
        return {}
    return totals(table, period.start, period.end, filters)


@st.cache_data(show_spinner=False)
def loaded_months() -> list[dt.date]:
    return store().loaded_months()


def in_range(period: Period) -> bool:
    months = loaded_months()
    return period.within(months[0], months[-1])


@st.cache_data(show_spinner=False)
def metadata() -> dict:
    return store().metadata()


@st.cache_data(show_spinner=False)
def month_reports() -> pd.DataFrame:
    return store().month_reports()


@st.cache_data(show_spinner=False)
def airports() -> pd.DataFrame:
    return store().airports()


@st.cache_data(show_spinner=False)
def carriers() -> pd.DataFrame:
    return store().carriers()


@st.cache_data(show_spinner=False)
def airport_names() -> dict[str, str]:
    df = airports()
    return {r.airport: (r.airport_name or r.airport) for r in df.itertuples()}


@st.cache_data(show_spinner=False)
def airport_cities() -> dict[str, str]:
    df = airports()
    return {r.airport: (r.city or r.airport) for r in df.itertuples()}


@st.cache_data(show_spinner=False)
def carrier_names() -> dict[str, str]:
    df = carriers()
    return {r.carrier: r.carrier_short for r in df.itertuples()}


def airport_label(code: str, long: bool = False) -> str:
    if long:
        return f"{code} · {airport_names().get(code, code)}"
    city = airport_cities().get(code)
    return f"{code} · {city}" if city else code


def carrier_label(code: str) -> str:
    return carrier_names().get(code, code)


@st.cache_data(show_spinner=False)
def distinct(column: str, start: dt.date, end: dt.date, filters: FilterKey = ()) -> list[str]:
    df = store().distinct("fact_route_monthly", column, start, end, dict(filters))
    return list(df[column])


@st.cache_data(show_spinner="Scanning for operational signals…")
def signals_for(start: dt.date, end: dt.date) -> list[sig.Signal]:
    period = Period(start, end)
    months = loaded_months()
    first = max(months[0], add_months(start, -(sig.HISTORY_MONTHS + 1)))
    s = store()
    inputs = sig.SignalInputs(
        network=s.aggregate("fact_route_monthly", first, end, by=["month"], metrics=False),
        airports=s.aggregate("fact_route_monthly", first, end, by=["origin", "month"], metrics=False),
        airport_hourly=s.aggregate("fact_origin_hourly", first, end, by=["origin", "month"], metrics=False),
        carriers=s.aggregate("fact_route_monthly", first, end, by=["carrier", "month"], metrics=False),
        routes=s.aggregate("fact_route_monthly", first, end, by=["route", "month"], metrics=False),
        airport_current=s.aggregate("fact_route_monthly", start, end, by=["origin"]),
        network_current=s.totals("fact_route_monthly", start, end),
    )
    return sig.detect_all(inputs, period, airport_label=lambda c: c, carrier_label=carrier_label)


@st.cache_data(show_spinner=False)
def brief_facts(start: dt.date, end: dt.date, filters: FilterKey, scope: str) -> dict:
    period = Period(start, end)
    f = dict(filters)
    current = period_totals(period, filters)
    previous = period_totals(period.previous(), filters)
    prior_year = period_totals(period.prior_year(), filters)
    origin_filters = fkey(**{k: v for k, v in f.items() if k != "origin"})
    airports_df = agg("fact_route_monthly", start, end, origin_filters, ("origin",)) if "origin" not in f else pd.DataFrame()
    trailing = period.trailing(3)
    carrier_filters = fkey(**{k: v for k, v in f.items() if k != "carrier"})
    carriers_cur = agg("fact_route_monthly", start, end, carrier_filters, ("carrier",))
    carriers_trail = (agg("fact_route_monthly", trailing.start, trailing.end, carrier_filters, ("carrier",))
                      if in_range(trailing) else pd.DataFrame())
    top_signal = None
    if not f:
        signals = [s for s in signals_for(start, end) if s.direction == "deterioration"]
        top_signal = signals[0] if signals else None
    facts = build_brief_facts(
        period, scope, current, previous, prior_year, airports_df, carriers_cur, carriers_trail,
        MIN_AIRPORT_DEPARTURES_PER_MONTH * period.months, MIN_CARRIER_FLIGHTS_PER_MONTH * period.months,
        top_signal=top_signal, airport_names=airport_names(), carrier_names=carrier_names(),
    )
    return facts.to_dict()
