"""Airport Performance: an operating profile for one airport."""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from flightops.analytics.benchmarks import hotspots, hub_tier, percentile
from flightops.charts import builders as ch
from flightops.charts import theme as t
from flightops.config import (
    MIN_AIRPORT_DEPARTURES_PER_MONTH,
    MIN_CARRIER_AIRPORT_FLIGHTS_PER_MONTH,
    MIN_ROUTE_FLIGHTS_PER_MONTH,
)
from flightops.data.measures import CAUSE_LABELS, CAUSE_MEASURES
from flightops.data.months import add_months
from flightops.metrics import Period
from flightops.metrics.definitions import cause_mix
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current
from flightops.utils.format import fmt_ordinal, fmt_pct

DOW = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
HOURS = {h: f"{h:02d}" for h in range(24)}
KPIS = ["flights", "on_time_rate", "cancellation_rate", "avg_dep_delay", "avg_arr_delay", "avg_taxi_out",
        "severe_delay_rate"]


def _select_airport(f, ranked: list[str]) -> str:
    if f.origin and st.session_state.get("_airport_sync") != f.origin:  # follow the global origin filter
        st.session_state["airport_sel"] = st.session_state["_airport_sync"] = f.origin
    if st.session_state.get("airport_sel") not in ranked:
        st.session_state["airport_sel"] = ranked[0]
    return st.selectbox("Airport", ranked, key="airport_sel", format_func=lambda c: data.airport_label(c, long=True))


def render() -> None:
    f = current()
    p = f.period
    all_q = f.query(by=("origin",), origin=None, dest=None)
    everyone = data.run(all_q, "Airport list and peer tiers")
    ui.page_header("Airport Performance",
                   f"Departure-side operating profile for {p.label}"
                   + (f", {data.carrier_label(f.carrier)} flights only." if f.carrier else ", all marketing carriers."), f)
    if everyone.empty:
        ui.empty_state("No flights in scope", "Try a different period or carrier.")
        return
    col, _ = st.columns([1, 2])
    with col:
        code = _select_airport(f, list(everyone.sort_values("flights", ascending=False)["origin"]))

    queries, values = ui.comparisons(lambda period: f.query(period=period, origin=code, dest=None), p,
                                     f"{code} KPIs")
    cur = values["current"]
    extra = []
    peers = everyone.assign(share=lambda d: d["flights"] / d["flights"].sum())
    peers["tier"] = peers["share"].map(hub_tier)
    tier = peers.loc[peers["origin"] == code, "tier"].iloc[0]
    peers = peers[(peers["tier"] == tier) & (peers["flights"] >= MIN_AIRPORT_DEPARTURES_PER_MONTH * p.months)].copy()
    if code in set(peers["origin"]) and len(peers) >= 3:
        peers["pct"] = percentile(peers, "on_time_rate")
        mine = peers.set_index("origin").loc[code]
        rank = int((peers["on_time_rate"] > mine["on_time_rate"]).sum()) + 1
        extra.append(lambda: ui.stat_card(
            "On-time percentile", fmt_ordinal(round(mine["pct"])), f"#{rank} of {len(peers)} {tier.lower()}s",
            help="Share of same-tier airports this airport beats on on-time arrival. Tiers apply FAA hub cut points "
                 "(1%, 0.25%, 0.05%) to each airport's share of scheduled departures in scope. Source: the "
                 "'Airport list and peer tiers' query in the Data Explorer."))
    mix = cause_mix(cur)
    if cur.get("delay_minutes"):
        top = max(mix, key=mix.get)
        extra.append(lambda: ui.stat_card("Largest delay cause", CAUSE_LABELS[top],
                                          f"{fmt_pct(mix[top])} of reported delay minutes",
                                          help="Cause with the most reported delay minutes on flights departing here."))
    ui.metric_row(KPIS, queries, values, labels={"flights": "Departures"}, extra=extra)

    months = data.loaded_months()
    history = Period(months[0], months[-1])
    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        trend_q = f.query(period=history, by=("month",), origin=code, dest=None)
        ref_q = f.query(period=history, by=("month",), origin=None, dest=None)
        ui.section("Performance trend", f"{code} departures vs the network (gray), monthly.",
                   {code: trend_q, "Network": ref_q}, ("on_time_rate", "avg_taxi_out"))
        st.plotly_chart(ch.monthly_small_multiples(data.run(trend_q, f"{code} trend"), ["on_time_rate", "avg_taxi_out"],
                                                   (p.start, p.end), reference=data.run(ref_q, "Network trend"),
                                                   label=code, height=340),
                        width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        hour_q = f.query("fact_origin_hourly", by=("dep_hour",), origin=code)
        hour_ref_q = f.query("fact_origin_hourly", by=("dep_hour",), origin=None)
        ui.section("Reliability through the operating day",
                   "Departure 15+ delay rate by scheduled departure hour (hours with ≥20 departures/month), with "
                   "scheduled volume below.", {code: hour_q, "Network": hour_ref_q}, ("dep_delay15_rate",))
        hourly = data.run(hour_q, f"{code} by hour")
        hourly = hourly[(hourly["dep_hour"] >= 0) & (hourly["flights"] >= 20 * p.months)]
        ref = data.run(hour_ref_q, "Network by hour")
        st.plotly_chart(ch.profile(hourly, "dep_hour", "dep_delay15_rate", "Scheduled departure hour",
                                   reference=ref[ref["dep_hour"].isin(hourly["dep_hour"])], height=340,
                                   tick_labels=HOURS), width="stretch", config=ch.PLOTLY_CONFIG)

    with st.container(border=True):
        last12 = Period(max(months[0], add_months(months[-1], -11)), months[-1])
        grid_q = f.query("fact_origin_hourly", period=last12, by=("month", "dep_hour"), origin=code)
        ui.section("Delay build-up by month and hour",
                   f"Departure 15+ delay rate at {code}, last 12 loaded months × scheduled departure hour. Cells under "
                   "20 departures are blank.", {"Month × hour": grid_q}, ("dep_delay15_rate",))
        grid = data.run(grid_q, f"{code} month × hour")
        grid = grid[grid["dep_hour"] >= 5]
        grid.loc[grid["flights"] < 20, "dep_delay15_rate"] = np.nan
        grid["label"] = pd.to_datetime(grid["month"]).dt.strftime("%b %Y")
        order = list(dict.fromkeys(grid.sort_values("month")["label"]))
        pivot = grid.pivot(index="label", columns="dep_hour", values="dep_delay15_rate").reindex(order)
        counts = grid.pivot(index="label", columns="dep_hour", values="flights").reindex(order)
        pivot.columns = counts.columns = [f"{h:02d}" for h in pivot.columns]
        if pivot.notna().any().any():
            st.plotly_chart(ch.heatmap(pivot, "dep_delay15_rate", zmin=0, zmax=float(np.nanpercentile(pivot, 97)),
                                       counts=counts, x_title="Scheduled departure hour (local)", height=360),
                            width="stretch", config=ch.PLOTLY_CONFIG)

    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        dow_q = f.query("fact_origin_daily", by=("dow",), origin=code)
        dow_ref_q = f.query("fact_origin_daily", by=("dow",), origin=None)
        ui.section("Day-of-week pattern", "On-time arrival for departures by day of week; network in gray.",
                   {code: dow_q, "Network": dow_ref_q}, ("on_time_rate",))
        st.plotly_chart(ch.profile(data.run(dow_q, f"{code} by day of week"), "dow", "on_time_rate", "",
                                   reference=data.run(dow_ref_q, "Network by day of week"), height=300, tick_labels=DOW),
                        width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("Delay cause mix", "Reported delay-cause minutes for delayed arrivals of flights departing here.",
                   {"Selected period": queries["current"]}, ("delay_minutes",))
        st.plotly_chart(ch.cause_bars({m: cur.get(m, 0) for m in CAUSE_MEASURES.values()}, height=300),
                        width="stretch", config=ch.PLOTLY_CONFIG)

    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        carriers_q = f.query(by=("carrier",), origin=code, dest=None, carrier=None)
        ui.section("Top carriers at this airport",
                   f"Marketing carriers by departures; ≥{MIN_CARRIER_AIRPORT_FLIGHTS_PER_MONTH} departures/month.",
                   {"Carriers": carriers_q})
        df = data.run(carriers_q, f"{code} carriers")
        df = df[df["flights"] >= MIN_CARRIER_AIRPORT_FLIGHTS_PER_MONTH * p.months].sort_values("flights", ascending=False)
        df["label"] = [f"{c} · {data.carrier_label(c)}" for c in df["carrier"]]
        df["share"] = (df["flights"] / df["flights"].sum() * 100).round(1)
        ui.metric_table(df, [("label", "Carrier"), ("flights", "Departures"), ("share", "Share (%)"),
                             ("on_time_rate", "On-time"), ("cancellation_rate", "Cancelled"),
                             ("avg_dep_delay", "Dep. delay")])
    with right, st.container(border=True):
        routes_q = f.query(by=("route",), origin=code, dest=None)
        ui.section("Most problematic routes",
                   "Routes from this airport with the most delayed arrivals beyond the airport's own 15+ delay rate; "
                   f"≥{MIN_ROUTE_FLIGHTS_PER_MONTH} flights/month.", {"Routes": routes_q, "Airport": queries["current"]},
                   ("delay15_rate",))
        ranked = hotspots(data.run(routes_q, f"{code} routes"), cur, p.months, MIN_ROUTE_FLIGHTS_PER_MONTH, top=8)
        if ranked.empty:
            ui.empty_state("No outlier routes", "Every qualifying route performs at or better than the airport rate.")
        else:
            fig = ch.ranked_bars(ranked, "route", "excess_delayed", color=t.CATEGORICAL[1], text_format="{:,.0f}",
                                 hover=[("flights", "Flights", ":,.0f"), ("on_time_rate", "On-time", ":.1%")])
            fig.update_xaxes(title="Excess delayed arrivals", tickformat="~s")
            st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)

    with st.container(border=True):
        dest_q = f.query(by=("dest",), origin=code, dest=None)
        ui.section("Best and worst destinations",
                   f"On-time arrival by destination from {code}; ≥{MIN_ROUTE_FLIGHTS_PER_MONTH} flights/month.",
                   {"Destinations": dest_q}, ("on_time_rate",))
        df = data.run(dest_q, f"{code} destinations")
        df = df[df["flights"] >= MIN_ROUTE_FLIGHTS_PER_MONTH * p.months].sort_values("on_time_rate", ascending=False)
        if len(df) < 4:
            ui.empty_state("Too few qualifying destinations", "Widen the period to compare destinations.")
            return
        n = min(6, len(df) // 2)
        hover = [("flights", "Flights", ":,.0f"), ("avg_arr_delay", "Avg arr. delay (min)", ":.1f")]
        left, right = st.columns(2)
        with left:
            st.caption("Most reliable")
            st.plotly_chart(ch.ranked_bars(df.head(n), "dest", "on_time_rate", color=t.CATEGORICAL[0],
                                           text_format="{:.1%}", hover=hover), width="stretch", config=ch.PLOTLY_CONFIG)
        with right:
            st.caption("Least reliable")
            st.plotly_chart(ch.ranked_bars(df.tail(n).iloc[::-1], "dest", "on_time_rate", color=t.CATEGORICAL[1],
                                           text_format="{:.1%}", hover=hover), width="stretch", config=ch.PLOTLY_CONFIG)
