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
from flightops.metrics.definitions import Unit
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
    """Oriented, robust-scaled distance from the network value: positive = worse."""
    sign = 1 if METRICS[metric_key].direction is Direction.LOWER_IS_BETTER else -1
    diff = (values - reference) * sign
    spread = np.nanpercentile(np.abs(diff), 90) or 1.0
    return diff / spread


def render() -> None:
    f = current()
    p = f.period
    ui.page_header("Network Map", "Where operational friction concentrates",
                   "Airports sized by departures and colored against the network value for the chosen metric. "
                   "Select an airport to draw its busiest routes.", f)
    if f.origin or f.dest:
        ui.note("The map always shows every airport; origin/destination filters are ignored here "
                "(the carrier filter applies). Use the focus selector to highlight one airport.")

    airports = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=None, dest=None), ("origin",))
    if airports.empty:
        ui.empty_state("No flights in scope", "Try a different period or carrier.")
        return
    dims = data.airports().rename(columns={"airport": "origin"})
    airports = airports.merge(dims[["origin", "airport_name", "city", "latitude", "longitude"]], on="origin", how="left")
    airports = airports.dropna(subset=["latitude", "longitude"])
    airports = airports[airports["longitude"].between(-180, -60)]  # U.S. incl. AK/HI/PR/territories west of -60
    airports = airports.sort_values("flights", ascending=False).head(MAP_TOP_AIRPORTS).copy()
    network = data.period_totals(p, f.route(origin=None, dest=None))

    c1, c2 = st.columns([1.3, 1])
    with c1:
        metric_key = st.segmented_control("Color airports by", list(MAP_METRICS), format_func=MAP_METRICS.get,
                                          default="on_time_rate", key="map_metric") or "on_time_rate"
    # Map clicks feed the focus selector before it is drawn.
    event = st.session_state.get("netmap")
    if event and event.get("selection", {}).get("objects", {}).get("airports"):
        picked = event["selection"]["objects"]["airports"][0]["origin"]
        if picked != st.session_state.get("_map_last_pick"):
            st.session_state["map_focus"] = picked
            st.session_state["_map_last_pick"] = picked
    options = [NONE, *airports["origin"]]
    if st.session_state.get("map_focus") not in options:
        st.session_state["map_focus"] = NONE
    with c2:
        focus = st.selectbox("Focus airport", options, key="map_focus",
                             format_func=lambda c: c if c == NONE else data.airport_label(c))
    focus = None if focus == NONE else focus

    metric = METRICS[metric_key]
    if metric_key == "delay_minutes":
        share = airports["delay_minutes"] / airports["delay_minutes"].max()
        airports["color"] = [sequential_rgb(s) for s in np.sqrt(share)]
        legend = "Brighter = more reported delay minutes (absolute, volume-driven)."
    else:
        scores = _score(airports[metric_key], network[metric_key], metric_key)
        airports["color"] = [diverging_rgb(s) for s in scores]
        legend = (f"Red = worse than the network {metric.short.lower()} ({metric.format(network[metric_key])}), "
                  "blue = better, gray = near network.")
    airports["radius"] = np.sqrt(airports["flights"] / p.months) * 330
    if focus:
        airports["color"] = [c if o == focus else [*c[:3]] for o, c in zip(airports["origin"], airports["color"], strict=True)]
    airports["flights_fmt"] = airports["flights"].map(fmt_int)
    airports["otp_fmt"] = airports["on_time_rate"].map(fmt_pct)
    airports["delay_fmt"] = airports["avg_arr_delay"].map(fmt_minutes)
    airports["canc_fmt"] = airports["cancellation_rate"].map(fmt_pct)
    airports["per100_fmt"] = airports["delay_min_per_100"].map(lambda v: f"{v:,.0f}")
    airports["driver"] = airports.apply(_primary_cause, axis=1)
    airports["label"] = airports["origin"] + " · " + airports["city"].fillna("")

    arcs = _arcs(f, airports, focus, network)
    tooltip = ("<b>{label}</b><br/>{airport_name}<br/>Departures: {flights_fmt}<br/>On-time: {otp_fmt}"
               "<br/>Avg positive arr. delay: {delay_fmt}<br/>Cancellation rate: {canc_fmt}"
               "<br/>Delay min / 100 flights: {per100_fmt}<br/>Primary reported delay driver: {driver}")
    if arcs is not None and not arcs.empty:
        tooltip += "<br/>{route_tip}"
    focus_xy = None
    if focus:
        row = airports[airports["origin"] == focus].iloc[0]
        focus_xy = (row["longitude"], row["latitude"])
    deck = network_deck(airports, arcs, tooltip, focus_xy)
    with st.container(border=True):
        st.pydeck_chart(deck, height=560, on_select="rerun", selection_mode="single-object", key="netmap")
        route_note = (f"Arcs: {focus}'s top routes by flights, colored by route on-time vs network."
                      if focus else f"Arcs: the {MAP_TOP_ROUTES} busiest airport-pair markets (both directions).")
        ui.note(f"{legend} Bubble area ∝ departures. Showing the top {len(airports)} airports by volume. {route_note} "
                "Click a bubble to focus.")

    left, right = st.columns([1, 1.35], gap="medium")
    with left, st.container(border=True):
        _ranking_table(airports, metric_key, p)
    with right, st.container(border=True):
        _hour_heatmap(f)


def _arcs(f, airports: pd.DataFrame, focus: str | None, network: dict) -> pd.DataFrame | None:
    p = f.period
    coords = airports.set_index("origin")[["longitude", "latitude"]]
    if focus:
        routes = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=focus, dest=None), ("origin", "dest"))
        routes = routes.sort_values("flights", ascending=False).head(40)
    else:
        routes = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=None, dest=None), ("market",))
        routes = routes.sort_values("flights", ascending=False).head(MAP_TOP_ROUTES)
        routes[["origin", "dest"]] = routes["market"].str.split("–", expand=True)
    routes = routes[routes["origin"].isin(coords.index) & routes["dest"].isin(coords.index)].copy()
    if routes.empty:
        return None
    routes["src_lon"] = routes["origin"].map(coords["longitude"])
    routes["src_lat"] = routes["origin"].map(coords["latitude"])
    routes["dst_lon"] = routes["dest"].map(coords["longitude"])
    routes["dst_lat"] = routes["dest"].map(coords["latitude"])
    top = routes["flights"].max()
    if focus:
        scores = _score(routes["on_time_rate"], network["on_time_rate"], "on_time_rate")
        routes["color"] = [[*diverging_rgb(s), 210] for s in scores]
        routes["width"] = 1 + 7 * routes["flights"] / top
    else:
        routes["color"] = [[76, 147, 234, 70]] * len(routes)
        routes["width"] = 0.6 + 3.5 * routes["flights"] / top
    routes["route_tip"] = [
        f"Route {o}–{d}: {fmt_int(n)} flights, on-time {fmt_pct(r)}"
        for o, d, n, r in zip(routes["origin"], routes["dest"], routes["flights"], routes["on_time_rate"], strict=True)
    ]
    for col in ("label", "airport_name", "flights_fmt", "otp_fmt", "delay_fmt", "canc_fmt", "per100_fmt", "driver"):
        routes[col] = ""
    routes["label"] = routes["route_tip"]
    routes["route_tip"] = ""
    return routes


def _ranking_table(airports: pd.DataFrame, metric_key: str, p) -> None:
    metric = METRICS[metric_key]
    ui.section("Airport ranking", f"Mapped airports with ≥{MIN_AIRPORT_DEPARTURES_PER_MONTH:,} departures/month, "
                                  f"worst {metric.short.lower()} first.")
    df = airports[airports["flights"] >= MIN_AIRPORT_DEPARTURES_PER_MONTH * p.months].copy()
    ascending = metric.direction is Direction.HIGHER_IS_BETTER
    df = df.sort_values(metric_key, ascending=ascending)
    scale = 100 if metric.unit is Unit.RATE else 1
    table = pd.DataFrame({
        "Airport": df["label"],
        "Departures": df["flights"],
        metric.short: df[metric_key] * scale,
        "Primary driver": df["driver"],
    })
    fmt = "%.1f%%" if metric.unit is Unit.RATE else ("%.1f min" if metric.unit is Unit.MINUTES else "localized")
    st.dataframe(table, hide_index=True, width="stretch", height=430, column_config={
        "Departures": st.column_config.NumberColumn(format="localized"),
        metric.short: st.column_config.NumberColumn(format=fmt),
    })


def _hour_heatmap(f) -> None:
    p = f.period
    ui.section("When the operating day breaks down",
               f"Departure 15+ delay rate by scheduled departure hour for the {HEATMAP_TOP_AIRPORTS} busiest airports. "
               "Cells with fewer than 20 departures per month are blank.")
    top = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=None, dest=None), ("origin",))
    top_codes = list(top.sort_values("flights", ascending=False).head(HEATMAP_TOP_AIRPORTS)["origin"])
    hourly = data.agg("fact_origin_hourly", p.start, p.end, f.origin_side(origin=top_codes), ("origin", "dep_hour"))
    hourly = hourly[(hourly["dep_hour"] >= 5)]
    hourly.loc[hourly["flights"] < 20 * p.months, "dep_delay15_rate"] = np.nan
    pivot = hourly.pivot(index="origin", columns="dep_hour", values="dep_delay15_rate").reindex(top_codes)
    counts = hourly.pivot(index="origin", columns="dep_hour", values="flights").reindex(top_codes)
    pivot.columns = [f"{h:02d}" for h in pivot.columns]
    counts.columns = pivot.columns
    zmax = float(np.nanpercentile(pivot.to_numpy(), 97)) if pivot.notna().any().any() else None
    fig = ch.heatmap(pivot, "dep_delay15_rate", x_title="Scheduled departure hour (local)", zmin=0, zmax=zmax,
                     counts=counts, height=560)
    st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)
