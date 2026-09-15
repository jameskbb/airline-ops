"""Executive Overview: network health, change and friction on one screen."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from flightops.analytics.benchmarks import hotspots
from flightops.charts import builders as ch
from flightops.charts import theme as t
from flightops.config import (
    MIN_AIRPORT_DEPARTURES_PER_MONTH,
    MIN_CARRIER_FLIGHTS_PER_MONTH,
    MIN_ROUTE_FLIGHTS_PER_MONTH,
)
from flightops.data.measures import CAUSE_MEASURES
from flightops.metrics import Period, compare, get_metric
from flightops.narrative import get_provider
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current

KPIS = ["flights", "on_time_rate", "avg_arr_delay", "cancellation_rate", "severe_delay_rate", "delay_minutes"]


def _secrets() -> dict:
    try:
        return dict(st.secrets)
    except Exception:  # no secrets file configured
        return {}


def render() -> None:
    f = current()
    p = f.period
    ui.page_header("Executive Overview",
                   f"How reliably {f.scope} operated in {p.label}, what changed, and where operational friction "
                   "concentrated.", f)
    queries, values = ui.comparisons(lambda period: f.query(period=period), p, "Headline KPIs")
    cur = values["current"]
    if not cur:
        ui.empty_state("No flights match these filters", "Widen the period or clear a carrier or airport filter.")
        return
    ui.metric_row(KPIS, queries, values)

    months = data.loaded_months()
    left, right = st.columns([1.6, 1], gap="medium")
    with left, st.container(border=True):
        trend_q = f.query(period=Period(months[0], months[-1]), by=("month",))
        ui.section("Network health trend",
                   "Monthly on-time arrival and cancellation rate across the loaded history. The shaded band is the "
                   "selected period; separate panels avoid a misleading dual axis.",
                   {"Monthly trend": trend_q}, ("on_time_rate", "cancellation_rate"))
        trend = data.run(trend_q, "Network health trend")
        st.plotly_chart(ch.monthly_small_multiples(trend, ["on_time_rate", "cancellation_rate"], (p.start, p.end),
                                                   height=340), width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("Latest Operations Brief", f"{p.label} · statements chosen by materiality from calculated facts",
                   data.brief_queries(p, f.carrier, f.origin, f.dest))
        facts = data.brief_facts(p, f.carrier, f.origin, f.dest, f.scope)
        provider = get_provider(_secrets())
        ui.brief(provider.generate(facts), provider.name)

    left, right = st.columns([1.25, 1], gap="medium")
    with left, st.container(border=True):
        _hotspots(f)
    with right, st.container(border=True):
        ui.section("Delay cause mix",
                   "Share of reported delay minutes on flights arriving 15+ minutes late, by the cause the carrier "
                   "reported to BTS.", {"Selected period": queries["current"]}, ("delay_minutes", "propagation_index"))
        st.plotly_chart(ch.cause_bars({m: cur.get(m, 0) for m in CAUSE_MEASURES.values()}, height=250),
                        width="stretch", config=ch.PLOTLY_CONFIG)
        ui.note(f"A delayed arrival averaged {get_metric('avg_delay_per_delayed').format(cur['avg_delay_per_delayed'])} "
                "of reported delay. Weather here means extreme weather only; routine weather is reported under NAS.")

    with st.container(border=True):
        _carrier_snapshot(f)


def _hotspots(f) -> None:
    p = f.period
    if f.dest:
        ui.section("Operational hotspots")
        ui.empty_state("Single-route scope", "Hotspots rank airports or routes; open Route Intelligence instead.")
        return
    network_q = f.query(origin=None, dest=None)
    if f.origin:
        entity, min_vol, query = "route", MIN_ROUTE_FLIGHTS_PER_MONTH, f.query(by=("route",))
        title = f"Operational hotspots · routes from {f.origin}"
    else:
        entity, min_vol, query = "origin", MIN_AIRPORT_DEPARTURES_PER_MONTH, f.query(by=("origin",))
        title = "Operational hotspots · airports"
    ui.section(title,
               "Excess delayed arrivals = delayed arrivals − completed arrivals × the network 15+ delay rate: where "
               f"volume and poor reliability intersect. Minimum {min_vol:,} flights per month.",
               {"Ranked entities": query, "Network reference": network_q}, ("delay15_rate", "on_time_rate"))
    ranked = hotspots(data.run(query, title), data.totals(network_q, "Hotspot network reference"),
                      p.months, min_vol, top=10)
    if ranked.empty:
        ui.empty_state("No hotspots", "Nothing cleared the volume floor with worse-than-network reliability.")
        return
    fig = ch.ranked_bars(ranked, entity, "excess_delayed", color=t.CATEGORICAL[1], text_format="{:,.0f}",
                         hover=[("flights", "Scheduled flights", ":,.0f"), ("on_time_rate", "On-time", ":.1%"),
                                ("delay_share", "Share of delay minutes", ":.1%"),
                                ("flight_share", "Share of flights", ":.1%")])
    fig.update_xaxes(title="Excess delayed arrivals vs network rate", tickformat="~s")
    st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)


def _carrier_snapshot(f) -> None:
    p = f.period
    cur_q = f.query(by=("carrier",), carrier=None)
    queries = {"Selected period": cur_q}
    if data.in_range(p.previous()):
        queries["Prior period"] = f.query(by=("carrier",), carrier=None, period=p.previous())
    floor = MIN_CARRIER_FLIGHTS_PER_MONTH * p.months * (1 if not (f.origin or f.dest) else 0.05)
    ui.section("Carrier snapshot", f"Marketing carriers in scope with ≥{int(floor):,} flights, ranked by volume.",
               queries, ("reliability_score", "on_time_rate"))
    df = data.run(cur_q, "Carrier snapshot")
    df = df[df["flights"] >= floor].sort_values("flights", ascending=False)
    if df.empty:
        ui.empty_state("No carriers in scope", "No marketing carrier cleared the minimum-volume floor.")
        return
    prev = data.run(queries["Prior period"], "Carrier snapshot (prior)") if "Prior period" in queries else None
    otp = get_metric("on_time_rate")
    prior_otp = prev.set_index("carrier")["on_time_rate"] if prev is not None and not prev.empty else pd.Series(dtype=float)
    df["label"] = [f"{c} · {data.carrier_label(c)}" for c in df["carrier"]]
    df["share"] = (df["flights"] / df["flights"].sum() * 100).round(1)
    df["otp_change"] = [round(d.change, 1) if (d := compare(otp, r.on_time_rate, prior_otp.get(r.carrier))) else None
                        for r in df.itertuples()]
    ui.metric_table(df, [("label", "Carrier"), ("flights", "Flights"), ("share", "Share of flights (%)"),
                         ("on_time_rate", "On-time"), ("otp_change", "Δ on-time vs prior (pts)"),
                         ("cancellation_rate", "Cancelled"), ("severe_delay_rate", "Severe 60+"),
                         ("avg_arr_delay", "Avg arr. delay"), ("reliability_score", "Reliability Score")])
