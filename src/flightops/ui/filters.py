"""Global, persistent, cascading filters rendered in the sidebar."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from flightops.data.months import format_month
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

    def route(self, **overrides) -> data.FilterKey:
        """Filter key for route-grain tables (supports every dimension)."""
        values = {"carrier": self.carrier, "origin": self.origin, "dest": self.dest}
        values.update(overrides)
        return data.fkey(**values)

    def origin_side(self, **overrides) -> data.FilterKey:
        """Filter key for departure-side tables, which have no destination column."""
        values = {"carrier": self.carrier, "origin": self.origin}
        values.update(overrides)
        return data.fkey(**values)

    @property
    def any_dimension(self) -> bool:
        return any((self.carrier, self.origin, self.dest))

    @property
    def scope(self) -> str:
        parts = []
        if self.carrier:
            parts.append(data.carrier_label(self.carrier))
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
            ("Carrier", data.carrier_label(self.carrier) if self.carrier else "All carriers"),
            ("Origin", self.origin or "All"),
            ("Destination", self.dest or "All"),
        ]


def _reset() -> None:
    for key in KEYS:
        st.session_state.pop(key, None)


def _ensure(key: str, options: list[str]) -> None:
    if st.session_state.get(key, ALL) not in options:
        st.session_state[key] = ALL


def render_sidebar() -> Filters:
    months = data.loaded_months()
    labels = [format_month(m, "short") for m in months]
    with st.sidebar:
        st.markdown('<div class="fo-side-label">Reporting period</div>', unsafe_allow_html=True)
        preset = st.selectbox("Reporting period", list(PRESETS), key="f_preset", label_visibility="collapsed")
        if preset == "Custom range":
            default = (labels[max(0, len(labels) - 3)], labels[-1])
            start_label, end_label = st.select_slider(
                "Month range", options=labels, value=st.session_state.get("f_range", default), key="f_range",
                label_visibility="collapsed",
            )
            period = Period(months[labels.index(start_label)], months[labels.index(end_label)])
        else:
            period = preset_period(preset, months[-1], months[0])

        st.markdown('<div class="fo-side-label">Scope</div>', unsafe_allow_html=True)
        carrier_opts = [ALL, *data.distinct("carrier", period.start, period.end)]
        _ensure("f_carrier", carrier_opts)
        carrier = st.selectbox(
            "Marketing carrier", carrier_opts, key="f_carrier",
            format_func=lambda c: "All carriers" if c == ALL else f"{c} · {data.carrier_label(c)}",
        )
        carrier_v = None if carrier == ALL else carrier

        origin_opts = [ALL, *data.distinct("origin", period.start, period.end, data.fkey(carrier=carrier_v))]
        _ensure("f_origin", origin_opts)
        origin = st.selectbox(
            "Origin airport", origin_opts, key="f_origin",
            format_func=lambda c: "All origins" if c == ALL else data.airport_label(c),
            help="Options are ordered by departures and limited to airports served by the selected carrier.",
        )
        origin_v = None if origin == ALL else origin

        dest_opts = [ALL, *data.distinct("dest", period.start, period.end,
                                         data.fkey(carrier=carrier_v, origin=origin_v))]
        _ensure("f_dest", dest_opts)
        dest = st.selectbox(
            "Destination airport", dest_opts, key="f_dest",
            format_func=lambda c: "All destinations" if c == ALL else data.airport_label(c),
        )
        dest_v = None if dest == ALL else dest

        if any((carrier_v, origin_v, dest_v)) or preset != "Latest month":
            st.button("Reset filters", on_click=_reset, width="stretch", type="tertiary")

    return Filters(period=period, preset=preset, carrier=carrier_v, origin=origin_v, dest=dest_v)


def current() -> Filters:
    """Filters computed by the entrypoint for this run."""
    return st.session_state["_filters"]
