"""Delay Drivers: which reported causes are behind delay, and where delay propagates."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from flightops.charts import builders as ch
from flightops.charts import theme as t
from flightops.config import MIN_AIRPORT_DEPARTURES_PER_MONTH, MIN_CARRIER_FLIGHTS_PER_MONTH
from flightops.data.measures import CAUSE_LABELS, CAUSE_MEASURES
from flightops.metrics import METRICS, Period
from flightops.metrics.definitions import cause_mix
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current
from flightops.utils.format import fmt_pct

KPIS = ["delay_minutes", "delay_min_per_100", "avg_delay_per_delayed", "propagation_index"]


def render() -> None:
    f = current()
    p = f.period
    ui.page_header("Delay Drivers",
                   "Reported delay causes for flights arriving 15+ minutes late. Carriers assign causes under DOT "
                   "reporting rules; they describe attribution, not a causal model.", f)
    queries, values = ui.comparisons(lambda period: f.query(period=period), p, "Delay KPIs")
    cur = values["current"]
    if not cur or not cur.get("delay_minutes"):
        ui.empty_state("No reported delay minutes in scope", "Widen the period or clear filters.")
        return
    mix = cause_mix(cur)
    top = max(mix, key=mix.get)
    ui.metric_row(KPIS, queries, values, extra=[lambda: ui.stat_card(
        "Largest reported cause", CAUSE_LABELS[top], f"{fmt_pct(mix[top])} of delay minutes",
        help="Cause with the most reported delay minutes: cause minutes ÷ total reported cause minutes.")])

    months = data.loaded_months()
    with st.container(border=True):
        trend_q = f.query(period=Period(months[0], months[-1]), by=("month",))
        ui.section("Cause mix over time",
                   "Reported delay minutes per 100 scheduled flights, by cause and month. Normalizing by volume "
                   "separates operational change from schedule size.", {"Monthly": trend_q}, ("delay_min_per_100",))
        trend = data.run(trend_q, "Cause mix over time")
        trend["label"] = pd.to_datetime(trend["month"]).dt.strftime("%b %y")
        fig = ch.cause_share_stacked(trend, "label", horizontal=False, per="flights", height=330)
        fig.update_yaxes(title="Delay min / 100 flights")
        st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)

    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        floor = MIN_CARRIER_FLIGHTS_PER_MONTH * p.months * (0.03 if (f.origin or f.dest) else 1)
        by_carrier_q = f.query(by=("carrier",), carrier=None)
        ui.section("Cause by carrier", f"Share of each carrier's reported delay minutes; ≥{int(floor):,} flights.",
                   {"Carriers": by_carrier_q}, ("delay_minutes",))
        by_c = data.run(by_carrier_q, "Cause by carrier")
        by_c = by_c[by_c["flights"] >= floor].sort_values("propagation_index", ascending=False)
        if not by_c.empty:
            st.plotly_chart(ch.cause_share_stacked(by_c, "carrier",
                                                   label_map={c: f"{c} · {data.carrier_label(c)}" for c in by_c["carrier"]}),
                            width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        hourly_q = f.query("fact_origin_hourly", by=("dep_hour",))
        ui.section("Cause by time of day",
                   "Reported delay minutes per 100 scheduled departures by scheduled departure hour. Late-aircraft "
                   "delay accumulates as the day's rotations stack up.", {"By hour": hourly_q}, ("delay_min_per_100",))
        hourly = data.run(hourly_q, "Cause by time of day")
        hourly = hourly[(hourly["dep_hour"] >= 5) & (hourly["flights"] >= 50 * p.months)]
        hourly["label"] = hourly["dep_hour"].map(lambda h: f"{h:02d}")
        fig = ch.cause_share_stacked(hourly, "label", horizontal=False, per="flights", height=300)
        fig.update_yaxes(title="Delay min / 100 flights")
        fig.update_xaxes(type="category", title="Scheduled departure hour")
        st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)

    with st.container(border=True):
        _propagation(f, cur)

    with st.container(border=True):
        by_airport_q = f.query(by=("origin",), origin=None)
        ui.section("Cause by airport", "The 20 airports with the most reported delay minutes on departing flights, "
                                       "ordered by delay volume.", {"Airports": by_airport_q}, ("delay_minutes",))
        by_a = data.run(by_airport_q, "Cause by airport").nlargest(20, "delay_minutes")
        st.plotly_chart(ch.cause_share_stacked(by_a, "origin",
                                               label_map={a: data.airport_label(a) for a in by_a["origin"]}),
                        width="stretch", config=ch.PLOTLY_CONFIG)
        ui.note("Weather here means extreme weather as reported by carriers; non-extreme weather that slows the system "
                "is reported under NAS, so weather's real footprint is larger than the Weather share alone.")


def _propagation(f, cur: dict) -> None:
    p = f.period
    query = f.query(by=("origin",), origin=None, dest=None)
    ui.section("Delay Propagation Index by airport",
               "Late-aircraft delay minutes ÷ all reported delay minutes for flights departing each airport. A high "
               "index means delay here is mostly associated with inbound aircraft arriving late (imported from "
               "upstream) rather than originating locally. Aircraft rotations are not reconstructed. Airports with "
               f"≥{MIN_AIRPORT_DEPARTURES_PER_MONTH:,} departures/month.", {"Airports": query}, ("propagation_index",))
    df = data.run(query, "Propagation by airport")
    df = df[(df["flights"] >= MIN_AIRPORT_DEPARTURES_PER_MONTH * p.months) & (df["delay_minutes"] > 0)]
    if df.empty:
        ui.empty_state("No airports above the floor", "Widen the period.")
        return
    ref = cur["propagation_index"]
    hover = [("flights", "Departures", ":,.0f"), ("delay_min_per_100", "Delay min / 100 flights", ":,.0f"),
             ("avg_arr_delay", "Avg arr. delay (min)", ":.1f")]
    left, right = st.columns(2)
    for column, frame, caption, color in (
        (left, df.nlargest(12, "propagation_index"), f"Highest propagation (in scope: {METRICS['propagation_index'].format(ref)})", t.CATEGORICAL[1]),
        (right, df.nsmallest(12, "propagation_index"), "Lowest propagation: delay mostly originates locally", t.CATEGORICAL[0]),
    ):
        with column:
            st.caption(caption)
            st.plotly_chart(ch.ranked_bars(frame, "origin", "propagation_index", color=color, reference=ref,
                                           reference_label="In scope", text_format="{:.0%}", hover=hover),
                            width="stretch", config=ch.PLOTLY_CONFIG)
    local = sum(cur[m] for c, m in CAUSE_MEASURES.items() if c != "late_aircraft") / cur["delay_minutes"]
    ui.note(f"In scope, {fmt_pct(ref)} of reported delay minutes were associated with late-arriving aircraft and "
            f"{fmt_pct(local)} were attributed to carrier, NAS, weather or security causes on the flight itself.")
