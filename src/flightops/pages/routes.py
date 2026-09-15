"""Route Intelligence: one directional route, benchmarked against its peers."""

from __future__ import annotations

import streamlit as st

from flightops.analytics.benchmarks import distance_band, percentile
from flightops.charts import builders as ch
from flightops.charts import theme as t
from flightops.config import MIN_ROUTE_FLIGHTS_PER_MONTH
from flightops.metrics import METRICS, Period
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current
from flightops.utils.format import fmt_int, fmt_ordinal

DOW = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
HOURS = {h: f"{h:02d}" for h in range(24)}
KPIS = ["flights", "on_time_rate", "avg_arr_delay", "cancellation_rate", "severe_delay_rate"]
DETAIL_KPIS = ["avg_sched_block", "avg_actual_block", "avg_distance", "avg_taxi_out"]


def _selectors(f, routes) -> tuple[str, str]:
    routes = routes.sort_values("flights", ascending=False)
    origins = list(routes.groupby("origin")["flights"].sum().sort_values(ascending=False).index)
    if st.session_state.get("_route_sync") != (f.origin, f.dest):  # follow the global filters when they change
        st.session_state["_route_sync"] = (f.origin, f.dest)
        if f.origin:
            st.session_state["route_o"] = f.origin
        if f.dest:
            st.session_state["route_d"] = f.dest
    if st.session_state.get("route_o") not in origins:
        st.session_state["route_o"] = routes.iloc[0]["origin"]
    c1, c2, _ = st.columns([1, 1, 1.4])
    origin = c1.selectbox("Origin", origins, key="route_o", format_func=data.airport_label)
    dests = list(routes[routes["origin"] == origin]["dest"])
    if st.session_state.get("route_d") not in dests:
        st.session_state["route_d"] = dests[0]
    dest = c2.selectbox("Destination", dests, key="route_d", format_func=data.airport_label)
    return origin, dest


def render() -> None:
    f = current()
    p = f.period
    ui.page_header("Route Intelligence",
                   "One directional route: reliability, schedule vs actual, and a benchmark against peer routes of "
                   f"similar length, {p.label}" + (f", {data.carrier_label(f.carrier)} only." if f.carrier else "."), f)
    routes_q = f.query(by=("route", "origin", "dest"), origin=None, dest=None)
    routes = data.run(routes_q, "Route list and peer groups")
    if routes.empty:
        ui.empty_state("No routes in scope", "Try a different period or carrier.")
        return
    origin, dest = _selectors(f, routes)
    route_id = f"{origin}→{dest}"

    queries, values = ui.comparisons(lambda period: f.query(period=period, origin=origin, dest=dest), p,
                                     f"{route_id} KPIs")
    cur = values["current"]
    if not cur:
        ui.empty_state("No flights on this route", "Select another route.")
        return

    # Peer group: directional routes in the same distance band that clear the volume floor.
    peers = routes[routes["flights"] >= MIN_ROUTE_FLIGHTS_PER_MONTH * p.months].copy()
    band = distance_band(cur.get("avg_distance"))
    peers = peers[peers["avg_distance"].map(distance_band) == band]
    pct = None
    if route_id in set(peers["route"]) and len(peers) >= 5:
        peers["pct"] = percentile(peers, "on_time_rate")
        pct = float(peers.set_index("route").loc[route_id, "pct"])
    peer_help = (f"Share of peer routes with a lower on-time rate. Peers: directional routes in the {band} band "
                 f"with ≥{MIN_ROUTE_FLIGHTS_PER_MONTH} flights/month in this period and scope. Source: the 'Route "
                 "list and peer groups' query in the Data Explorer.")
    extra = [lambda: ui.stat_card("Reliability percentile", fmt_ordinal(round(pct)) if pct is not None else "—",
                                  f"vs {len(peers):,} peer routes ({band})" if pct is not None
                                  else f"Needs ≥{MIN_ROUTE_FLIGHTS_PER_MONTH} flights/month", help=peer_help)]
    ui.metric_row(KPIS, queries, values, extra=extra)

    carriers_q = f.query(by=("carrier",), origin=origin, dest=dest, carrier=None)
    carriers = data.run(carriers_q, f"{route_id} carriers")
    ui.metric_row(DETAIL_KPIS, queries, values, labels={"avg_taxi_out": "Avg taxi-out at origin"}, extra=[
        lambda: ui.stat_card("Carriers serving", str(len(carriers)),
                             ", ".join(carriers.sort_values("flights", ascending=False)["carrier"]),
                             help="Marketing carriers with at least one scheduled flight on the route in the period."),
    ])

    months = data.loaded_months()
    history = Period(months[0], months[-1])
    left, right = st.columns([1.3, 1], gap="medium")
    with left, st.container(border=True):
        trend_q = f.query(period=history, by=("month",), origin=origin, dest=dest)
        ref_q = f.query(period=history, by=("month",), origin=None, dest=None)
        ui.section("Route trend", "Monthly on-time arrival and average positive arrival delay; network in gray.",
                   {route_id: trend_q, "Network": ref_q}, ("on_time_rate", "avg_arr_delay"))
        st.plotly_chart(ch.monthly_small_multiples(data.run(trend_q, f"{route_id} trend"),
                                                   ["on_time_rate", "avg_arr_delay"], (p.start, p.end),
                                                   reference=data.run(ref_q, "Network trend"), label=route_id,
                                                   height=340), width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("Route reliability benchmark",
                   f"On-time distribution of {len(peers):,} peer routes ({band}, ≥{MIN_ROUTE_FLIGHTS_PER_MONTH} "
                   "flights/month).", {"Peer routes": routes_q}, ("on_time_rate",))
        if pct is None:
            ui.empty_state("Not enough volume to benchmark", "The route is below the peer-group volume floor.")
        else:
            st.plotly_chart(ch.distribution_strip(peers["on_time_rate"], cur["on_time_rate"], "on_time_rate",
                                                  f"{route_id} · {fmt_ordinal(round(pct))} pct"),
                            width="stretch", config=ch.PLOTLY_CONFIG)
            otp = METRICS["on_time_rate"]
            ui.note(f"Peer median on-time {otp.format(peers['on_time_rate'].median())}; this route "
                    f"{otp.format(cur['on_time_rate'])}.")
        reverse = routes[routes["route"] == f"{dest}→{origin}"]
        if not reverse.empty:
            r = reverse.iloc[0]
            ui.note(f"Reverse direction {dest}→{origin}: on-time {METRICS['on_time_rate'].format(r['on_time_rate'])}, "
                    f"{fmt_int(r['flights'])} flights. Market {min(origin, dest)}–{max(origin, dest)} combined: "
                    f"{fmt_int(r['flights'] + cur['flights'])} flights.")

    left, mid, right = st.columns(3, gap="medium")
    with left, st.container(border=True):
        ui.section("By carrier", "On-time arrival by marketing carrier on this route.", {"Carriers": carriers_q},
                   ("on_time_rate",))
        c = carriers.sort_values("on_time_rate", ascending=False)
        c["name"] = [f"{x} · {data.carrier_label(x)}" for x in c["carrier"]]
        st.plotly_chart(ch.ranked_bars(c, "name", "on_time_rate", color=t.CATEGORICAL[0], text_format="{:.1%}",
                                       hover=[("flights", "Flights", ":,.0f"), ("cancellation_rate", "Cancelled", ":.1%")],
                                       highlight=f"{f.carrier} · {data.carrier_label(f.carrier)}" if f.carrier else None),
                        width="stretch", config=ch.PLOTLY_CONFIG)
    for column, dimension, title, labels, axis in ((mid, "hour", "By departure hour", HOURS, "Scheduled departure hour"),
                                                   (right, "dow", "By day of week", DOW, "")):
        with column, st.container(border=True):
            q = f.query("fact_route_profile", by=("bucket",), origin=origin, dest=dest, dimension=dimension)
            ui.section(title, "On-time arrival; all carriers on the route (this table has no carrier column)."
                       if f.carrier else "On-time arrival; hours with ≥8 flights/month.", {"Profile": q}, ("on_time_rate",))
            df = data.run(q, f"{route_id} by {dimension}")
            df = df[(df["bucket"] >= 0) & (df["flights"] >= (8 if dimension == "hour" else 1) * p.months)]
            st.plotly_chart(ch.profile(df, "bucket", "on_time_rate", axis, height=300, tick_labels=labels),
                            width="stretch", config=ch.PLOTLY_CONFIG)

    with st.container(border=True):
        floor = MIN_ROUTE_FLIGHTS_PER_MONTH * 3
        ui.section("Least reliable busy routes in this period",
                   f"Directional routes with ≥{floor} flights/month, lowest on-time first.",
                   {"All routes": routes_q}, ("on_time_rate",))
        busy = routes[routes["flights"] >= floor * p.months].nsmallest(15, "on_time_rate").copy()
        busy["band"] = busy["avg_distance"].map(distance_band)
        ui.metric_table(busy, [("route", "Route"), ("band", "Distance band"), ("flights", "Flights"),
                               ("on_time_rate", "On-time"), ("avg_arr_delay", "Avg arr. delay"),
                               ("cancellation_rate", "Cancelled")])
