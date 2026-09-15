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
from flightops.metrics import compare, get_metric
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
    ui.page_header(
        "Executive Overview", "U.S. Airline Operations and Network Performance",
        f"How reliably {f.scope} operated in {p.label}, what changed, and where operational friction concentrated.",
        f,
    )
    cur = data.period_totals(p, f.route())
    if not cur:
        ui.empty_state("No flights match these filters", "Widen the period or clear a carrier or airport filter.")
        return
    prev = data.period_totals(p.previous(), f.route())
    yoy = data.period_totals(p.prior_year(), f.route())
    ui.kpi_grid(ui.compare_cards(KPIS, p, cur, prev, yoy))

    months = data.loaded_months()
    left, right = st.columns([1.6, 1], gap="medium")
    with left, st.container(border=True):
        ui.section("Network health trend",
                   "Monthly on-time arrival and cancellation rate across the loaded history. The shaded band is the "
                   "selected period; separate panels avoid a misleading dual axis.")
        trend = data.agg("fact_route_monthly", months[0], months[-1], f.route(), ("month",))
        st.plotly_chart(ch.monthly_small_multiples(trend, ["on_time_rate", "cancellation_rate"], (p.start, p.end),
                                                   height=340),
                        width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("Latest Operations Brief", f"{p.label} · statements selected from calculated facts")
        facts = data.brief_facts(p.start, p.end, f.route(), f.scope)
        provider = get_provider(_secrets())
        ui.brief(provider.generate(facts), provider.name)

    left, right = st.columns([1.25, 1], gap="medium")
    with left, st.container(border=True):
        _hotspots(f, cur)
    with right, st.container(border=True):
        ui.section("Delay cause mix", "Share of reported delay minutes on flights arriving 15+ minutes late, "
                                      "by the cause the carrier reported to BTS.")
        causes = {m: cur.get(m, 0) for m in ("delay_carrier_min", "delay_weather_min", "delay_nas_min",
                                              "delay_security_min", "delay_late_aircraft_min")}
        st.plotly_chart(ch.cause_bars(causes, height=250), width="stretch", config=ch.PLOTLY_CONFIG)
        per_delayed = get_metric("avg_delay_per_delayed")
        ui.note(f"Late-aircraft minutes are delay associated with an earlier flight arriving late (the Delay Propagation "
                f"Index). A delayed arrival averaged {per_delayed.format(cur.get('avg_delay_per_delayed'))} of reported delay. "
                "Weather here is extreme weather only; routine weather is reported under NAS.")

    with st.container(border=True):
        _carrier_snapshot(f, prev)


def _hotspots(f, cur: dict) -> None:
    p = f.period
    if f.origin and not f.dest:
        ui.section("Operational hotspots · routes",
                   f"Routes from {f.origin} with the most delayed arrivals beyond the network 15+ delay rate. "
                   f"Minimum {MIN_ROUTE_FLIGHTS_PER_MONTH} flights per month.")
        df = data.agg("fact_route_monthly", p.start, p.end, f.route(dest=None), ("route",))
        label, min_vol = "route", MIN_ROUTE_FLIGHTS_PER_MONTH
    elif f.dest:
        ui.section("Operational hotspots", "Hotspot ranking is shown when no destination filter is active.")
        ui.empty_state("Single-route scope", "Open Route Intelligence for route-level diagnostics.")
        return
    else:
        ui.section("Operational hotspots · airports",
                   "Airports where volume and poor reliability intersect: delayed arrivals beyond what the network 15+ "
                   f"delay rate implies for the same departures. Minimum {MIN_AIRPORT_DEPARTURES_PER_MONTH:,} departures per month.")
        df = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=None), ("origin",))
        label, min_vol = "origin", MIN_AIRPORT_DEPARTURES_PER_MONTH
    network = data.period_totals(p, f.route(origin=None, dest=None))
    ranked = hotspots(df, network, p.months, min_vol, top=10)
    if ranked.empty:
        ui.empty_state("No hotspots", "No entity cleared the volume floor with worse-than-network reliability.")
        return
    ranked = ranked.assign(name=ranked[label])
    fig = ch.ranked_bars(
        ranked, "name", "excess_delayed", color=t.CATEGORICAL[1],
        hover=[("flights", "Scheduled flights", ":,.0f"), ("on_time_rate", "On-time", ":.1%"),
               ("delay_share", "Share of delay minutes", ":.1%"), ("flight_share", "Share of flights", ":.1%")],
        text_format="{:,.0f}",
    )
    fig.update_xaxes(title="Excess delayed arrivals vs network rate", tickformat="~s")
    st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)


def _carrier_snapshot(f, prev: dict) -> None:
    p = f.period
    ui.section("Carrier snapshot",
               f"Marketing carriers in scope, ranked by volume. Minimum {MIN_CARRIER_FLIGHTS_PER_MONTH:,} flights per "
               "month (scaled to scope). Reliability Score = on-time arrivals per 100 scheduled flights.")
    df = data.agg("fact_route_monthly", p.start, p.end, f.route(carrier=None), ("carrier",))
    prev_df = (data.agg("fact_route_monthly", p.previous().start, p.previous().end, f.route(carrier=None), ("carrier",))
               if prev else pd.DataFrame())
    scale = 1 if not (f.origin or f.dest) else 0.05
    df = df[df["flights"] >= MIN_CARRIER_FLIGHTS_PER_MONTH * p.months * scale].sort_values("flights", ascending=False)
    if df.empty:
        ui.empty_state("No carriers in scope", "No marketing carrier cleared the minimum-volume floor.")
        return
    otp = get_metric("on_time_rate")
    if not prev_df.empty:
        prev_map = prev_df.set_index("carrier")["on_time_rate"]
        df["otp_change"] = [
            (d.change if (d := compare(otp, r.on_time_rate, prev_map.get(r.carrier))) else None) for r in df.itertuples()
        ]
    else:
        df["otp_change"] = None
    table = pd.DataFrame({
        "Carrier": [f"{c} · {data.carrier_label(c)}" for c in df["carrier"]],
        "Flights": df["flights"],
        "Share": df["flights"] / df["flights"].sum() * 100,
        "On-time %": df["on_time_rate"] * 100,
        "Δ on-time (pts)": df["otp_change"],
        "Cancelled %": df["cancellation_rate"] * 100,
        "Severe 60+ %": df["severe_delay_rate"] * 100,
        "Avg arr. delay (min)": df["avg_arr_delay"],
        "Reliability Score": df["reliability_score"],
    })
    st.dataframe(
        table, hide_index=True, width="stretch",
        column_config={
            "Flights": st.column_config.NumberColumn(format="localized"),
            "Share": st.column_config.ProgressColumn("Share of flights", format="%.1f%%", min_value=0,
                                                     max_value=float(table["Share"].max())),
            "On-time %": st.column_config.NumberColumn(format="%.1f%%"),
            "Δ on-time (pts)": st.column_config.NumberColumn(format="%+.1f", help="Change vs the prior equivalent period, percentage points"),
            "Cancelled %": st.column_config.NumberColumn(format="%.1f%%"),
            "Severe 60+ %": st.column_config.NumberColumn(format="%.1f%%"),
            "Avg arr. delay (min)": st.column_config.NumberColumn(format="%.1f"),
            "Reliability Score": st.column_config.ProgressColumn(format="%.1f", min_value=50, max_value=100),
        },
    )
