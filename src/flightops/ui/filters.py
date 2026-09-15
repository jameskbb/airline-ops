"""Global, persistent, cascading filters rendered in the sidebar by the entrypoint."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from flightops.data.months import format_month
from flightops.data.query import Query
from flightops.metrics.periods import PRESETS, Period, preset_period
from flightops.ui import data

ALL = "All"
KEYS = ("f_preset", "f_range", "f_carrier", "f_origin", "f_dest")


@dataclass(frozen=True)
class Filters:
    period: Period
    preset: str
    carrier: str | None
    origin: str | None
    dest: str | None

    def query(self, table: str = "fact_route_monthly", by: tuple[str, ...] = (), period: Period | None = None,
              **overrides) -> Query:
        """A query scoped to these filters. ``overrides`` replace or clear (``None``) a filter."""
        scope = {"carrier": self.carrier, "origin": self.origin, "dest": self.dest} | overrides
        p = period or self.period
        return Query.build(table, p.start, p.end, by=tuple(by), **scope)

    @property
    def scope(self) -> str:
        parts = [data.carrier_label(self.carrier)] if self.carrier else []
        if self.origin and self.dest:
            parts.append(f"{self.origin}→{self.dest}")
        elif self.origin:
            parts.append(f"departures from {self.origin}")
        elif self.dest:
            parts.append(f"arrivals to {self.dest}")
        return " · ".join(parts) if parts else "the U.S. network"

    @property
    def chips(self) -> list[tuple[str, str]]:
        return [
            ("Period", self.period.label),
            ("Carrier", data.carrier_label(self.carrier) if self.carrier else "All"),
            ("Origin", self.origin or "All"),
            ("Destination", self.dest or "All"),
        ]


def _reset() -> None:
    for key in KEYS:
        st.session_state.pop(key, None)


def _select(label: str, key: str, options: list[str], all_label: str, fmt, help: str | None = None) -> str | None:
    options = [ALL, *options]
    if st.session_state.get(key, ALL) not in options:
        st.session_state[key] = ALL  # a narrower upstream filter removed the previous choice
    value = st.selectbox(label, options, key=key, help=help,
                         format_func=lambda v: all_label if v == ALL else fmt(v))
    return None if value == ALL else value


def render_sidebar() -> Filters:
    months = data.loaded_months()
    labels = [format_month(m, "short") for m in months]
    with st.sidebar:
        preset = st.selectbox("Reporting period", list(PRESETS), key="f_preset",
                              help="BTS publishes monthly, so periods are whole reporting months.")
        if preset == "Custom range":
            start_label, end_label = st.select_slider("Month range", options=labels, key="f_range",
                                                      value=st.session_state.get("f_range", (labels[-3], labels[-1])))
            period = Period(months[labels.index(start_label)], months[labels.index(end_label)])
        else:
            period = preset_period(preset, months[-1], months[0])

        carrier = _select("Marketing carrier", "f_carrier", data.options("carrier", period), "All carriers",
                          lambda c: f"{c} · {data.carrier_label(c)}",
                          help="The branded network the flight was sold under, including regional partners.")
        origin = _select("Origin airport", "f_origin", data.options("origin", period, carrier=carrier),
                         "All origins", data.airport_label,
                         help="Ordered by departures; limited to airports the selected carrier serves.")
        dest = _select("Destination airport", "f_dest",
                       data.options("dest", period, carrier=carrier, origin=origin),
                       "All destinations", data.airport_label)
        if carrier or origin or dest or preset != "Latest month":
            st.button("Reset filters", on_click=_reset, width="stretch", type="tertiary")
    return Filters(period=period, preset=preset, carrier=carrier, origin=origin, dest=dest)


def current() -> Filters:
    """Filters computed by the entrypoint for this run."""
    return st.session_state["_filters"]
