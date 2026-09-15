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
from flightops.metrics.definitions import cause_mix
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current
from flightops.utils.format import fmt_ordinal, fmt_pct

DOW = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
KPIS = ["flights", "on_time_rate", "cancellation_rate", "avg_dep_delay", "avg_arr_delay", "avg_taxi_out",
        "severe_delay_rate"]


def _select_airport(f, ranked: list[str]) -> str:
    # Follow the global origin filter when it changes; otherwise keep the page selection.
    if f.origin and st.session_state.get("_airport_sync") != f.origin:
        st.session_state["airport_sel"] = f.origin
        st.session_state["_airport_sync"] = f.origin
    if st.session_state.get("airport_sel") not in ranked:
        st.session_state["airport_sel"] = ranked[0]
    return st.selectbox("Airport", ranked, key="airport_sel", format_func=lambda c: data.airport_label(c, long=True))


def render() -> None:
    f = current()
    p = f.period
    all_airports = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=None, dest=None), ("origin",))
    if all_airports.empty:
        ui.page_header("Airport Performance", "Airport operating profile", "", f)
        ui.empty_state("No flights in scope", "Try a different period or carrier.")
        return
    ranked = list(all_airports.sort_values("flights", ascending=False)["origin"])
    col, _ = st.columns([1, 2])
    with col:
        code = _select_airport(f, ranked)
    ui.page_header("Airport Performance", data.airport_label(code, long=True),
                   f"Departure-side operating profile for {p.label}"
                   + (f", {data.carrier_label(f.carrier)} flights only." if f.carrier else ", all marketing carriers."),
                   f)
    if f.dest:
        ui.note("The destination filter is not applied on this page; the profile covers all departures.")

    scope = f.route(origin=code, dest=None)
    cur = data.period_totals(p, scope)
    prev = data.period_totals(p.previous(), scope)
    yoy = data.period_totals(p.prior_year(), scope)
    cards = ui.compare_cards(KPIS, p, cur, prev, yoy, labels={"flights": "Departures"})

    # Peer ranking within the airport's hub tier (share of scheduled departures).
    df = all_airports.copy()
    df["share"] = df["flights"] / df["flights"].sum()
    df["tier"] = df["share"].map(hub_tier)
    tier = df.loc[df["origin"] == code, "tier"].iloc[0]
    peers = df[(df["tier"] == tier) & (df["flights"] >= MIN_AIRPORT_DEPARTURES_PER_MONTH * p.months)].copy()
    if code in set(peers["origin"]) and len(peers) >= 3:
        peers["pct"] = percentile(peers, "on_time_rate")
        pct = peers.loc[peers["origin"] == code, "pct"].iloc[0]
        rank = int((peers["on_time_rate"] > peers.loc[peers["origin"] == code, "on_time_rate"].iloc[0]).sum()) + 1
        cards.append(ui.text_card("On-time percentile", fmt_ordinal(round(pct)),
                                  f"#{rank} of {len(peers)} {tier.lower()}s",
                                  "Share of same-tier airports this airport beats on on-time arrival. Tiers use FAA "
                                  "hub cut points (1%, 0.25%, 0.05%) applied to share of scheduled departures."))
    mix = cause_mix(cur)
    if cur.get("delay_minutes"):
        top_cause = max(mix, key=lambda c: mix[c])
        cards.append(ui.text_card("Largest delay cause", CAUSE_LABELS[top_cause],
                                  f"{fmt_pct(mix[top_cause])} of reported delay minutes"))
    ui.kpi_grid(cards)

    months = data.loaded_months()
    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        ui.section("Performance trend", f"{code} departures vs the network (gray), monthly.")
        trend = data.agg("fact_route_monthly", months[0], months[-1], scope, ("month",))
        ref = data.agg("fact_route_monthly", months[0], months[-1], f.route(origin=None, dest=None), ("month",))
        st.plotly_chart(ch.monthly_small_multiples(trend, ["on_time_rate", "avg_taxi_out"], (p.start, p.end),
                                                   reference=ref, label=code, height=340),
                        width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("Reliability through the operating day",
                   "Departure 15+ delay rate by scheduled departure hour, with scheduled volume below.")
        hourly = data.agg("fact_origin_hourly", p.start, p.end, f.origin_side(origin=code), ("dep_hour",))
        hourly = hourly[(hourly["dep_hour"] >= 0) & (hourly["flights"] >= 20 * p.months)]
        ref_h = data.agg("fact_origin_hourly", p.start, p.end, f.origin_side(origin=None), ("dep_hour",))
        ref_h = ref_h[ref_h["dep_hour"].isin(hourly["dep_hour"])]
        labels = {h: f"{h:02d}" for h in range(24)}
        st.plotly_chart(ch.profile(hourly, "dep_hour", "dep_delay15_rate", "Scheduled departure hour",
                                   reference=ref_h, height=340, tick_labels=labels),
                        width="stretch", config=ch.PLOTLY_CONFIG)

    with st.container(border=True):
        ui.section("Delay build-up by month and hour",
                   f"Departure 15+ delay rate at {code}, last 12 loaded months × scheduled departure hour. "
                   "Cells under 20 departures are blank.")
        start12 = max(months[0], add_months(months[-1], -11))
        grid = data.agg("fact_origin_hourly", start12, months[-1], f.origin_side(origin=code), ("month", "dep_hour"))
        grid = grid[grid["dep_hour"] >= 5]
        grid.loc[grid["flights"] < 20, "dep_delay15_rate"] = np.nan
        grid["label"] = pd.to_datetime(grid["month"]).dt.strftime("%b %Y")
        pivot = grid.pivot(index="label", columns="dep_hour", values="dep_delay15_rate")
        order = pd.to_datetime(pd.Series(pivot.index), format="%b %Y").sort_values().dt.strftime("%b %Y")
        pivot = pivot.reindex(list(order))
        counts = grid.pivot(index="label", columns="dep_hour", values="flights").reindex(pivot.index)
        pivot.columns = counts.columns = [f"{h:02d}" for h in pivot.columns]
        if pivot.notna().any().any():
            st.plotly_chart(ch.heatmap(pivot, "dep_delay15_rate", zmin=0,
                                       zmax=float(np.nanpercentile(pivot.to_numpy(), 97)), counts=counts,
                                       x_title="Scheduled departure hour (local)", height=360),
                            width="stretch", config=ch.PLOTLY_CONFIG)

    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        ui.section("Day-of-week pattern", "On-time arrival for departures by day of week.")
        dow = data.agg("fact_origin_daily", p.start, p.end, f.origin_side(origin=code), ("dow",))
        ref_d = data.agg("fact_origin_daily", p.start, p.end, f.origin_side(origin=None), ("dow",))
        st.plotly_chart(ch.profile(dow, "dow", "on_time_rate", "", reference=ref_d, height=300, tick_labels=DOW),
                        width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("Delay cause mix", "Reported delay-cause minutes for delayed arrivals of flights departing here.")
        st.plotly_chart(ch.cause_bars({m: cur.get(m, 0) for m in CAUSE_MEASURES.values()}, height=300),
                        width="stretch", config=ch.PLOTLY_CONFIG)

    left, right = st.columns([1, 1], gap="medium")
    with left, st.container(border=True):
        _carriers_here(f, code)
    with right, st.container(border=True):
        _problem_routes(f, code, cur)

    with st.container(border=True):
        _destinations(f, code)


def _carriers_here(f, code: str) -> None:
    p = f.period
    ui.section("Top carriers at this airport",
               f"Marketing carriers by departures; ≥{MIN_CARRIER_AIRPORT_FLIGHTS_PER_MONTH} departures/month.")
    df = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=code, dest=None, carrier=None), ("carrier",))
    df = df[df["flights"] >= MIN_CARRIER_AIRPORT_FLIGHTS_PER_MONTH * p.months].sort_values("flights", ascending=False)
    if df.empty:
        ui.empty_state("No carriers above the floor", "")
        return
    table = pd.DataFrame({
        "Carrier": [f"{c} · {data.carrier_label(c)}" for c in df["carrier"]],
        "Departures": df["flights"],
        "Share": df["flights"] / df["flights"].sum() * 100,
        "On-time %": df["on_time_rate"] * 100,
        "Cancelled %": df["cancellation_rate"] * 100,
        "Avg dep. delay": df["avg_dep_delay"],
    })
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Departures": st.column_config.NumberColumn(format="localized"),
        "Share": st.column_config.NumberColumn(format="%.1f%%"),
        "On-time %": st.column_config.NumberColumn(format="%.1f%%"),
        "Cancelled %": st.column_config.NumberColumn(format="%.1f%%"),
        "Avg dep. delay": st.column_config.NumberColumn(format="%.1f min"),
    })


def _problem_routes(f, code: str, cur: dict) -> None:
    p = f.period
    ui.section("Most problematic routes",
               "Routes from this airport with the most delayed arrivals beyond the airport's own 15+ delay rate; "
               f"≥{MIN_ROUTE_FLIGHTS_PER_MONTH} flights/month.")
    df = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=code, dest=None), ("route",))
    ranked = hotspots(df, cur, p.months, MIN_ROUTE_FLIGHTS_PER_MONTH, top=8)
    if ranked.empty:
        ui.empty_state("No outlier routes", "Every qualifying route performs at or better than the airport rate.")
        return
    fig = ch.ranked_bars(ranked, "route", "excess_delayed", color=t.CATEGORICAL[1], text_format="{:,.0f}",
                         hover=[("flights", "Flights", ":,.0f"), ("on_time_rate", "On-time", ":.1%")])
    fig.update_xaxes(title="Excess delayed arrivals", tickformat="~s")
    st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)


def _destinations(f, code: str) -> None:
    p = f.period
    ui.section("Best and worst destinations",
               f"On-time arrival by destination from {code}; ≥{MIN_ROUTE_FLIGHTS_PER_MONTH} flights/month.")
    df = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=code, dest=None), ("dest",))
    df = df[df["flights"] >= MIN_ROUTE_FLIGHTS_PER_MONTH * p.months].sort_values("on_time_rate", ascending=False)
    if len(df) < 4:
        ui.empty_state("Too few qualifying destinations", "Widen the period to compare destinations.")
        return
    n = min(6, len(df) // 2)
    left, right = st.columns(2)
    hover = [("flights", "Flights", ":,.0f"), ("avg_arr_delay", "Avg arr. delay (min)", ":.1f")]
    with left:
        st.caption("Most reliable")
        st.plotly_chart(ch.ranked_bars(df.head(n), "dest", "on_time_rate", color=t.CATEGORICAL[0],
                                       text_format="{:.1%}", hover=hover), width="stretch", config=ch.PLOTLY_CONFIG)
    with right:
        st.caption("Least reliable")
        st.plotly_chart(ch.ranked_bars(df.tail(n).iloc[::-1], "dest", "on_time_rate", color=t.CATEGORICAL[1],
                                       text_format="{:.1%}", hover=hover), width="stretch", config=ch.PLOTLY_CONFIG)
