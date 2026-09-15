"""Network Map: where friction sits geographically, and how it builds through the day."""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from flightops.charts import builders as ch
from flightops.charts.maps import diverging_rgb, network_deck, sequential_rgb
from flightops.config import (
    HEATMAP_TOP_AIRPORTS,
    MAP_TOP_AIRPORTS,
    MAP_TOP_ROUTES,
    MIN_AIRPORT_DEPARTURES_PER_MONTH,
)
from flightops.data.measures import CAUSE_LABELS, CAUSE_MEASURES
from flightops.metrics import METRICS, Direction
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current
from flightops.utils.format import fmt_int, fmt_minutes, fmt_pct

MAP_METRICS = {
    "on_time_rate": "On-time %",
    "avg_arr_delay": "Avg delay",
    "cancellation_rate": "Cancellation %",
    "delay_min_per_100": "Delay min / 100 flights",
    "delay_minutes": "Total delay minutes",
}
NONE = "Network overview"


def _primary_cause(row) -> str:
    values = {c: row[m] for c, m in CAUSE_MEASURES.items()}
    cause = max(values, key=values.get)
    return CAUSE_LABELS[cause] if values[cause] > 0 else "—"


def _score(values: pd.Series, reference: float, metric_key: str) -> pd.Series:
    """Distance from the network value, oriented so positive = worse, scaled by the 90th percentile."""
    sign = 1 if METRICS[metric_key].direction is Direction.LOWER_IS_BETTER else -1
    diff = (values - reference) * sign
    return diff / (np.nanpercentile(np.abs(diff), 90) or 1.0)


def render() -> None:
    f = current()
    p = f.period
    ui.page_header("Network Map",
                   "Airports sized by departures and colored against the network value for the chosen metric. "
                   "Select an airport to draw its busiest routes.", f)
    airports_q = f.query(by=("origin",), origin=None, dest=None)
    network_q = f.query(origin=None, dest=None)
    airports = data.run(airports_q, "Network map airports")
    if airports.empty:
        ui.empty_state("No flights in scope", "Try a different period or carrier.")
        return
    dims = data.dimension("dim_airport").rename(columns={"airport": "origin"})
    airports = airports.merge(dims[["origin", "airport_name", "city", "latitude", "longitude"]], on="origin")
    airports = airports.dropna(subset=["latitude", "longitude"]).nlargest(MAP_TOP_AIRPORTS, "flights")
    network = data.totals(network_q, "Network map reference")

    c1, c2 = st.columns([1.3, 1])
    with c1:
        metric_key = st.segmented_control("Color airports by", list(MAP_METRICS), format_func=MAP_METRICS.get,
                                          default="on_time_rate", key="map_metric") or "on_time_rate"
    event = st.session_state.get("netmap")  # a bubble click feeds the focus selector before it is drawn
    picked = (event or {}).get("selection", {}).get("objects", {}).get("airports")
    if picked and picked[0]["origin"] != st.session_state.get("_map_last_pick"):
        st.session_state["map_focus"] = st.session_state["_map_last_pick"] = picked[0]["origin"]
    options = [NONE, *airports["origin"]]
    if st.session_state.get("map_focus") not in options:
        st.session_state["map_focus"] = NONE
    with c2:
        focus = st.selectbox("Focus airport", options, key="map_focus",
                             format_func=lambda c: c if c == NONE else data.airport_label(c))
    focus = None if focus == NONE else focus

    metric = METRICS[metric_key]
    if metric_key == "delay_minutes":
        airports["color"] = [sequential_rgb(s) for s in np.sqrt(airports["delay_minutes"] / airports["delay_minutes"].max())]
        legend = "Brighter = more reported delay minutes (absolute, so volume-driven)."
    else:
        airports["color"] = [diverging_rgb(s) for s in _score(airports[metric_key], network[metric_key], metric_key)]
        legend = (f"Red = worse than the network {metric.short.lower()} ({metric.format(network[metric_key])}), "
                  "blue = better, gray = near the network value.")
    airports["radius"] = np.sqrt(airports["flights"] / p.months) * 330
    airports["flights_fmt"] = airports["flights"].map(fmt_int)
    airports["otp_fmt"] = airports["on_time_rate"].map(fmt_pct)
    airports["delay_fmt"] = airports["avg_arr_delay"].map(fmt_minutes)
    airports["canc_fmt"] = airports["cancellation_rate"].map(fmt_pct)
    airports["per100_fmt"] = airports["delay_min_per_100"].map(lambda v: f"{v:,.0f}")
    airports["driver"] = airports.apply(_primary_cause, axis=1)
    airports["label"] = airports["origin"] + " · " + airports["city"].fillna("")

    routes_q = (f.query(by=("origin", "dest"), origin=focus, dest=None) if focus
                else f.query(by=("market",), origin=None, dest=None))
    arcs = _arcs(data.run(routes_q, "Network map route arcs"), airports, focus, network)
    tooltip = ("<b>{label}</b><br/>{airport_name}<br/>Departures: {flights_fmt}<br/>On-time: {otp_fmt}"
               "<br/>Avg positive arr. delay: {delay_fmt}<br/>Cancellation rate: {canc_fmt}"
               "<br/>Delay min / 100 flights: {per100_fmt}<br/>Primary reported delay driver: {driver}")
    focus_row = airports[airports["origin"] == focus].iloc[0] if focus else None
    with st.container(border=True):
        ui.section("Airport network", legend, {"Airports": airports_q, "Route arcs": routes_q,
                                               "Network reference": network_q}, (metric_key,))
        st.pydeck_chart(network_deck(airports, arcs, tooltip,
                                     (focus_row["longitude"], focus_row["latitude"]) if focus else None),
                        height=560, on_select="rerun", selection_mode="single-object", key="netmap")
        ui.note(f"Bubble area ∝ departures; top {len(airports)} airports by volume. "
                + (f"Arcs: {focus}'s 40 busiest routes, colored by on-time vs network." if focus
                   else f"Arcs: the {MAP_TOP_ROUTES} busiest airport-pair markets.") + " Click a bubble to focus.")

    left, right = st.columns([1, 1.35], gap="medium")
    with left, st.container(border=True):
        ui.section("Airport ranking", f"Mapped airports with ≥{MIN_AIRPORT_DEPARTURES_PER_MONTH:,} departures/month, "
                                      f"worst {metric.short.lower()} first.", {"Airports": airports_q}, (metric_key,))
        ranked = airports[airports["flights"] >= MIN_AIRPORT_DEPARTURES_PER_MONTH * p.months]
        ranked = ranked.sort_values(metric_key, ascending=metric.direction is Direction.HIGHER_IS_BETTER)
        ui.metric_table(ranked, [("label", "Airport"), ("flights", "Departures"), (metric_key, metric.short),
                                 ("driver", "Primary driver")], height=430)
    with right, st.container(border=True):
        _hour_heatmap(f)


def _arcs(routes: pd.DataFrame, airports: pd.DataFrame, focus: str | None, network: dict) -> pd.DataFrame | None:
    coords = airports.set_index("origin")[["longitude", "latitude"]]
    if focus:
        routes = routes.nlargest(40, "flights")
    else:
        routes = routes.nlargest(MAP_TOP_ROUTES, "flights")
        routes[["origin", "dest"]] = routes["market"].str.split("–", expand=True)
    routes = routes[routes["origin"].isin(coords.index) & routes["dest"].isin(coords.index)].copy()
    if routes.empty:
        return None
    for end, col in (("src", "origin"), ("dst", "dest")):
        routes[f"{end}_lon"] = routes[col].map(coords["longitude"])
        routes[f"{end}_lat"] = routes[col].map(coords["latitude"])
    top = routes["flights"].max()
    if focus:
        routes["color"] = [[*diverging_rgb(s), 210] for s in _score(routes["on_time_rate"], network["on_time_rate"],
                                                                     "on_time_rate")]
        routes["width"] = 1 + 7 * routes["flights"] / top
    else:
        routes["color"] = [[76, 147, 234, 70]] * len(routes)
        routes["width"] = 0.6 + 3.5 * routes["flights"] / top
    # Arc tooltips reuse the airport tooltip template, so fill its fields.
    routes["label"] = [f"Route {o}–{d}: {fmt_int(n)} flights, on-time {fmt_pct(r)}" for o, d, n, r in
                       zip(routes["origin"], routes["dest"], routes["flights"], routes["on_time_rate"], strict=True)]
    for col in ("airport_name", "flights_fmt", "otp_fmt", "delay_fmt", "canc_fmt", "per100_fmt", "driver"):
        routes[col] = ""
    return routes


def _hour_heatmap(f) -> None:
    p = f.period
    top_q = f.query(by=("origin",), origin=None, dest=None)
    top_codes = list(data.run(top_q, "Heatmap airport selection").nlargest(HEATMAP_TOP_AIRPORTS, "flights")["origin"])
    hourly_q = f.query("fact_origin_hourly", by=("origin", "dep_hour"), origin=top_codes)
    ui.section("When the operating day breaks down",
               f"Departure 15+ delay rate by scheduled departure hour for the {HEATMAP_TOP_AIRPORTS} busiest airports. "
               "Cells with fewer than 20 departures per month are blank.",
               {"Airport selection": top_q, "Hourly performance": hourly_q}, ("dep_delay15_rate",))
    hourly = data.run(hourly_q, "Airport × hour heatmap")
    hourly = hourly[hourly["dep_hour"] >= 5]
    hourly.loc[hourly["flights"] < 20 * p.months, "dep_delay15_rate"] = np.nan
    pivot = hourly.pivot(index="origin", columns="dep_hour", values="dep_delay15_rate").reindex(top_codes)
    counts = hourly.pivot(index="origin", columns="dep_hour", values="flights").reindex(top_codes)
    pivot.columns = counts.columns = [f"{h:02d}" for h in pivot.columns]
    zmax = float(np.nanpercentile(pivot.to_numpy(), 97)) if pivot.notna().any().any() else None
    st.plotly_chart(ch.heatmap(pivot, "dep_delay15_rate", x_title="Scheduled departure hour (local)", zmin=0,
                               zmax=zmax, counts=counts, height=560), width="stretch", config=ch.PLOTLY_CONFIG)
