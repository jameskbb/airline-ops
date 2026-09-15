"""Cached data access and the session query store.

* ``store()`` is one shared DuckDB-backed store per process (``st.cache_resource``).
* ``_execute()`` memoizes query results (``st.cache_data`` returns copies, so pages
  can modify frames safely).
* ``run()`` is what pages call. It records the query in the session's query store
  before returning the cached result. Logging stays outside cached functions,
  because cached functions do not replay side effects on cache hits.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from flightops.analytics import signals as sig
from flightops.analytics.facts import build_brief_facts
from flightops.config import MIN_AIRPORT_DEPARTURES_PER_MONTH, MIN_CARRIER_FLIGHTS_PER_MONTH
from flightops.data.months import add_months
from flightops.data.query import Query
from flightops.data.store import Store
from flightops.metrics.periods import Period

LOG_KEY = "query_log"
LOG_LIMIT = 400


@st.cache_resource(show_spinner=False)
def store() -> Store:
    return Store()


@st.cache_data(show_spinner=False, max_entries=1024)
def _execute(query: Query) -> pd.DataFrame:
    return store().run(query)


@st.cache_data(show_spinner=False, max_entries=256)
def _rows(query: Query, limit: int) -> pd.DataFrame:
    return store().run(query, limit=limit)


@st.cache_data(show_spinner=False, max_entries=256)
def _count(query: Query) -> int:
    return store().count(query)


# -- query store ------------------------------------------------------------------
def log(query: Query, label: str) -> None:
    """Record a query in this session's query store (specs only, never results)."""
    entries: dict = st.session_state.setdefault(LOG_KEY, {})
    entry = entries.pop(query.id, None) or {"query": query, "labels": [], "pages": []}
    page = st.session_state.get("_page", "")
    if label not in entry["labels"]:
        entry["labels"].append(label)
    if page and page not in entry["pages"]:
        entry["pages"].append(page)
    entries[query.id] = entry  # re-insert: most recently used last
    while len(entries) > LOG_LIMIT:
        entries.pop(next(iter(entries)))


def logged() -> list[dict]:
    """Query store entries, most recently used first."""
    return list(reversed(st.session_state.get(LOG_KEY, {}).values()))


def run(query: Query, label: str) -> pd.DataFrame:
    log(query, label)
    return _execute(query)


def totals(query: Query, label: str) -> dict:
    """Single-row aggregate as a dict; empty when nothing matches."""
    df = run(query, label)
    return {} if df.empty else df.iloc[0].to_dict()


def source_rows(query: Query, limit: int) -> pd.DataFrame:
    return _rows(query, limit)


def row_count(query: Query) -> int:
    return _count(query)


def sql(query: Query) -> str:
    return query.display_sql(store().measures[query.table])


# -- reference data --------------------------------------------------------------------
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
def dimension(name: str) -> pd.DataFrame:
    return store().table(name)


@st.cache_data(show_spinner=False)
def _airport_labels() -> tuple[dict[str, str], dict[str, str]]:
    df = dimension("dim_airport")
    return ({r.airport: r.airport_name or r.airport for r in df.itertuples()},
            {r.airport: r.city or r.airport for r in df.itertuples()})


def airport_label(code: str, long: bool = False) -> str:
    names, cities = _airport_labels()
    if long:
        return f"{code} · {names.get(code, code)}"
    return f"{code} · {cities[code]}" if code in cities else code


@st.cache_data(show_spinner=False)
def _carrier_labels() -> dict[str, str]:
    return {r.carrier: r.carrier_short for r in dimension("dim_carrier").itertuples()}


def carrier_label(code: str) -> str:
    return _carrier_labels().get(code, code)


def options(column: str, period: Period, **filters) -> list[str]:
    """Values of ``column`` with flights in the period, busiest first (for cascading filters)."""
    query = Query.build("fact_route_monthly", period.start, period.end, by=(column,), **filters)
    df = run(query, f"Filter options: {column}")
    return list(df.sort_values("flights", ascending=False)[column])


# -- signals ---------------------------------------------------------------------------
def signal_queries(period: Period) -> dict[str, Query]:
    first = max(loaded_months()[0], add_months(period.start, -(sig.HISTORY_MONTHS + 1)))
    monthly = lambda table, *by: Query.build(table, first, period.end, by=(*by, "month"))  # noqa: E731
    return {
        "network by month": monthly("fact_route_monthly"),
        "airports by month": monthly("fact_route_monthly", "origin"),
        "airport taxi-out by month": monthly("fact_origin_hourly", "origin"),
        "carriers by month": monthly("fact_route_monthly", "carrier"),
        "routes by month": monthly("fact_route_monthly", "route"),
        "airports in period": Query.build("fact_route_monthly", period.start, period.end, by=("origin",)),
        "network in period": Query.build("fact_route_monthly", period.start, period.end),
    }


def signals_for(period: Period) -> list[sig.Signal]:
    for name, query in signal_queries(period).items():
        log(query, f"Signals input: {name}")
    return _detect_signals(period.start, period.end)


@st.cache_data(show_spinner="Scanning for operational signals…")
def _detect_signals(start: dt.date, end: dt.date) -> list[sig.Signal]:
    period = Period(start, end)
    frames = {name: _execute(q) for name, q in signal_queries(period).items()}
    current = frames["network in period"]
    inputs = sig.SignalInputs(
        network=frames["network by month"],
        airports=frames["airports by month"],
        airport_hourly=frames["airport taxi-out by month"],
        carriers=frames["carriers by month"],
        routes=frames["routes by month"],
        airport_current=frames["airports in period"],
        network_current={} if current.empty else current.iloc[0].to_dict(),
    )
    return sig.detect_all(inputs, period, carrier_label=carrier_label)


# -- latest operations brief ------------------------------------------------------------
def brief_queries(period: Period, carrier: str | None, origin: str | None, dest: str | None) -> dict[str, Query]:
    scope = {"carrier": carrier, "origin": origin, "dest": dest}
    q = lambda p, by=(), **over: Query.build("fact_route_monthly", p.start, p.end, by=by, **(scope | over))  # noqa: E731
    queries = {
        "current": q(period),
        "previous": q(period.previous()),
        "prior year": q(period.prior_year()),
        "carriers": q(period, ("carrier",), carrier=None),
    }
    if in_range(period.trailing(3)):
        queries["carriers trailing 3 months"] = q(period.trailing(3), ("carrier",), carrier=None)
    if not origin:
        queries["airports"] = q(period, ("origin",))
    return queries


def brief_facts(period: Period, carrier: str | None, origin: str | None, dest: str | None, scope: str) -> dict:
    for name, query in brief_queries(period, carrier, origin, dest).items():
        log(query, f"Operations brief input: {name}")
    signals = signals_for(period) if not any((carrier, origin, dest)) else []
    top = next((s for s in signals if s.direction == "deterioration"), None)
    return _brief_facts(period.start, period.end, carrier, origin, dest, scope, top)


@st.cache_data(show_spinner=False)
def _brief_facts(start, end, carrier, origin, dest, scope, _top_signal) -> dict:
    period = Period(start, end)
    frames = {name: _execute(q) for name, q in brief_queries(period, carrier, origin, dest).items()}
    as_dict = lambda df: {} if df is None or df.empty else df.iloc[0].to_dict()  # noqa: E731
    in_scope = lambda p: in_range(p)  # noqa: E731
    facts = build_brief_facts(
        period, scope, as_dict(frames["current"]),
        as_dict(frames["previous"]) if in_scope(period.previous()) else {},
        as_dict(frames["prior year"]) if in_scope(period.prior_year()) else {},
        frames.get("airports", pd.DataFrame()), frames["carriers"],
        frames.get("carriers trailing 3 months", pd.DataFrame()),
        MIN_AIRPORT_DEPARTURES_PER_MONTH * period.months, MIN_CARRIER_FLIGHTS_PER_MONTH * period.months,
        top_signal=_top_signal, airport_names=_airport_labels()[0], carrier_names=_carrier_labels(),
    )
    return facts.to_dict()
