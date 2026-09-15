"""Route Intelligence: one directional route, benchmarked against its peers."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from flightops.analytics.benchmarks import distance_band, percentile
from flightops.charts import builders as ch
from flightops.charts import theme as t
from flightops.config import MIN_ROUTE_FLIGHTS_PER_MONTH
from flightops.metrics import METRICS
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current
from flightops.utils.format import fmt_int, fmt_ordinal

DOW = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
KPIS = ["flights", "on_time_rate", "avg_arr_delay", "cancellation_rate", "severe_delay_rate"]


def _selectors(f) -> tuple[str, str] | None:
    p = f.period
    routes = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=None, dest=None), ("origin", "dest"))
    if routes.empty:
        return None
    routes = routes.sort_values("flights", ascending=False)
    origins = list(routes.groupby("origin")["flights"].sum().sort_values(ascending=False).index)
    # Follow global filters when they change.
    sync = (f.origin, f.dest)
    if st.session_state.get("_route_sync") != sync:
        st.session_state["_route_sync"] = sync
        if f.origin:
            st.session_state["route_o"] = f.origin
        if f.dest:
            st.session_state["route_d"] = f.dest
    if st.session_state.get("route_o") not in origins:
        st.session_state["route_o"] = routes.iloc[0]["origin"]
    c1, c2, _ = st.columns([1, 1, 1.4])
    with c1:
        origin = st.selectbox("Origin", origins, key="route_o", format_func=data.airport_label)
    dests = list(routes[routes["origin"] == origin]["dest"])
    if st.session_state.get("route_d") not in dests:
        st.session_state["route_d"] = dests[0]
    with c2:
        dest = st.selectbox("Destination", dests, key="route_d", format_func=data.airport_label)
    return origin, dest


def render() -> None:
    f = current()
    p = f.period
    picked = _selectors(f)
    if picked is None:
        ui.page_header("Route Intelligence", "Route performance", "", f)
        ui.empty_state("No routes in scope", "Try a different period or carrier.")
        return
    origin, dest = picked
    ui.page_header("Route Intelligence", f"{origin} → {dest}",
                   f"{data.airport_label(origin)} to {data.airport_label(dest)}, {p.label}"
                   + (f", {data.carrier_label(f.carrier)} only." if f.carrier else ", all marketing carriers."), f)

    scope = f.route(origin=origin, dest=dest)
    cur = data.period_totals(p, scope)
    if not cur:
        ui.empty_state("No flights on this route", "Select another route.")
        return
    prev = data.period_totals(p.previous(), scope)
    yoy = data.period_totals(p.prior_year(), scope)
    carriers = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=origin, dest=dest, carrier=None), ("carrier",))
    cards = ui.compare_cards(KPIS, p, cur, prev, yoy)

    # Peer benchmark: same distance band, minimum volume, same period and carrier scope.
    all_routes = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=None, dest=None), ("route",))
    all_routes = all_routes[all_routes["flights"] >= MIN_ROUTE_FLIGHTS_PER_MONTH * p.months].copy()
    band = distance_band(cur.get("avg_distance"))
    all_routes["band"] = all_routes["avg_distance"].map(distance_band)
    peers = all_routes[all_routes["band"] == band].copy()
    route_id = f"{origin}→{dest}"
    pct = None
    if route_id in set(peers["route"]) and len(peers) >= 5:
        peers["pct"] = percentile(peers, "on_time_rate")
        pct = float(peers.loc[peers["route"] == route_id, "pct"].iloc[0])
        cards.append(ui.text_card("Reliability percentile", fmt_ordinal(round(pct)),
                                  f"vs {len(peers):,} peer routes ({band})",
                                  "Share of peer routes with a lower on-time rate. Peers: directional routes in the same "
                                  f"distance band with ≥{MIN_ROUTE_FLIGHTS_PER_MONTH} flights/month in this period."))
    else:
        cards.append(ui.text_card("Reliability percentile", "—",
                                  f"Needs ≥{MIN_ROUTE_FLIGHTS_PER_MONTH} flights/month to rank"))
    ui.kpi_grid(cards)
    ui.kpi_grid([
        ui.text_card("Carriers serving", str(len(carriers)), ", ".join(carriers.sort_values("flights", ascending=False)["carrier"])),
        ui.kpi_card("avg_sched_block", cur.get("avg_sched_block")),
        ui.kpi_card("avg_actual_block", cur.get("avg_actual_block"),
                    note=f"{cur.get('avg_actual_block', 0) - cur.get('avg_sched_block', 0):+.1f} min vs schedule"),
        ui.text_card("Distance", f"{fmt_int(cur.get('avg_distance'))} mi", band),
        ui.kpi_card("avg_taxi_out", cur.get("avg_taxi_out"), label="Avg taxi-out at origin"),
    ])

    months = data.loaded_months()
    left, right = st.columns([1.3, 1], gap="medium")
    with left, st.container(border=True):
        ui.section("Route trend", "Monthly on-time arrival and average positive arrival delay; network in gray.")
        trend = data.agg("fact_route_monthly", months[0], months[-1], scope, ("month",))
        ref = data.agg("fact_route_monthly", months[0], months[-1], f.route(origin=None, dest=None), ("month",))
        st.plotly_chart(ch.monthly_small_multiples(trend, ["on_time_rate", "avg_arr_delay"], (p.start, p.end),
                                                   reference=ref, label=route_id, height=340),
                        width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("Route reliability benchmark",
                   f"On-time distribution of {len(peers):,} peer routes ({band}, ≥{MIN_ROUTE_FLIGHTS_PER_MONTH} "
                   "flights/month).")
        if pct is not None:
            st.plotly_chart(ch.distribution_strip(peers["on_time_rate"], cur["on_time_rate"], "on_time_rate",
                                                  f"{route_id} · {fmt_ordinal(round(pct))} pct"),
                            width="stretch", config=ch.PLOTLY_CONFIG)
            median = peers["on_time_rate"].median()
            ui.note(f"Peer median on-time: {METRICS['on_time_rate'].format(median)}. "
                    f"This route: {METRICS['on_time_rate'].format(cur['on_time_rate'])}.")
        else:
            ui.empty_state("Not enough volume to benchmark", "The route is below the peer-group volume floor.")
        reverse = data.period_totals(p, f.route(origin=dest, dest=origin))
        if reverse:
            ui.note(f"Reverse direction {dest}→{origin}: on-time {METRICS['on_time_rate'].format(reverse['on_time_rate'])}, "
                    f"{fmt_int(reverse['flights'])} flights. Market {min(origin, dest)}–{max(origin, dest)} combined: "
                    f"{fmt_int(reverse['flights'] + cur['flights'])} flights.")

    left, mid, right = st.columns(3, gap="medium")
    with left, st.container(border=True):
        ui.section("By carrier", "On-time arrival by marketing carrier on this route.")
        if carriers.empty:
            ui.empty_state("No carriers", "")
        else:
            c = carriers.sort_values("on_time_rate", ascending=False).assign(
                name=lambda d: d["carrier"].map(lambda x: f"{x} · {data.carrier_label(x)}"))
            st.plotly_chart(ch.ranked_bars(c, "name", "on_time_rate", color=t.CATEGORICAL[0], text_format="{:.1%}",
                                           hover=[("flights", "Flights", ":,.0f"),
                                                  ("cancellation_rate", "Cancelled", ":.1%")],
                                           highlight=f"{f.carrier} · {data.carrier_label(f.carrier)}" if f.carrier else None),
                            width="stretch", config=ch.PLOTLY_CONFIG)
    profile_note = "All carriers on the route." if f.carrier else None
    with mid, st.container(border=True):
        ui.section("By departure hour", profile_note or "On-time arrival by scheduled departure hour.")
        hours = data.agg("fact_route_profile", p.start, p.end, data.fkey(origin=origin, dest=dest, dimension="hour"),
                         ("bucket",))
        hours = hours[(hours["bucket"] >= 0) & (hours["flights"] >= 8 * p.months)]
        st.plotly_chart(ch.profile(hours, "bucket", "on_time_rate", "Scheduled departure hour", height=300,
                                   tick_labels={h: f"{h:02d}" for h in range(24)}),
                        width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("By day of week", profile_note or "On-time arrival by day of week.")
        dow = data.agg("fact_route_profile", p.start, p.end, data.fkey(origin=origin, dest=dest, dimension="dow"),
                       ("bucket",))
        st.plotly_chart(ch.profile(dow, "bucket", "on_time_rate", "", height=300, tick_labels=DOW),
                        width="stretch", config=ch.PLOTLY_CONFIG)

    with st.container(border=True):
        ui.section("Least reliable busy routes in this period",
                   f"Directional routes with ≥{MIN_ROUTE_FLIGHTS_PER_MONTH * 3} flights/month, lowest on-time first.")
        busy = all_routes[all_routes["flights"] >= MIN_ROUTE_FLIGHTS_PER_MONTH * 3 * p.months]
        worst = busy.sort_values("on_time_rate").head(15)
        table = pd.DataFrame({
            "Route": worst["route"], "Distance band": worst["band"], "Flights": worst["flights"],
            "On-time %": worst["on_time_rate"] * 100, "Avg arr. delay": worst["avg_arr_delay"],
            "Cancelled %": worst["cancellation_rate"] * 100,
        })
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            "Flights": st.column_config.NumberColumn(format="localized"),
            "On-time %": st.column_config.NumberColumn(format="%.1f%%"),
            "Avg arr. delay": st.column_config.NumberColumn(format="%.1f min"),
            "Cancelled %": st.column_config.NumberColumn(format="%.1f%%"),
        })
