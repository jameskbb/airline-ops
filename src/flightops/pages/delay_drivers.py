"""Delay Drivers: what reported causes are behind delay, and where delay propagates."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from flightops.charts import builders as ch
from flightops.charts import theme as t
from flightops.config import MIN_AIRPORT_DEPARTURES_PER_MONTH, MIN_CARRIER_FLIGHTS_PER_MONTH
from flightops.data.measures import CAUSE_LABELS, CAUSE_MEASURES
from flightops.metrics import METRICS
from flightops.metrics.definitions import cause_mix
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current
from flightops.utils.format import fmt_pct

KPIS = ["delay_minutes", "delay_min_per_100", "avg_delay_per_delayed", "propagation_index"]


def render() -> None:
    f = current()
    p = f.period
    ui.page_header("Delay Drivers", "What is behind delay",
                   "Reported delay causes for flights arriving 15+ minutes late. Causes are assigned by carriers under "
                   "DOT reporting rules; they describe attribution, not a causal model.", f)
    cur = data.period_totals(p, f.route())
    if not cur or not cur.get("delay_minutes"):
        ui.empty_state("No reported delay minutes in scope", "Widen the period or clear filters.")
        return
    prev = data.period_totals(p.previous(), f.route())
    yoy = data.period_totals(p.prior_year(), f.route())
    cards = ui.compare_cards(KPIS, p, cur, prev, yoy)
    mix = cause_mix(cur)
    top = max(mix, key=mix.get)
    cards.append(ui.text_card("Largest reported cause", CAUSE_LABELS[top], f"{fmt_pct(mix[top])} of delay minutes"))
    ui.kpi_grid(cards)

    months = data.loaded_months()
    with st.container(border=True):
        ui.section("Cause mix over time",
                   "Reported delay minutes per 100 scheduled flights, by cause and month. Normalizing by volume "
                   "separates operational change from schedule size.")
        trend = data.agg("fact_route_monthly", months[0], months[-1], f.route(), ("month",))
        trend["label"] = pd.to_datetime(trend["month"]).dt.strftime("%b %y")
        fig = ch.cause_share_stacked(trend, "label", horizontal=False, per="flights", height=330)
        fig.update_yaxes(title="Delay min / 100 flights")
        st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)

    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        ui.section("Cause by carrier", f"Share of each carrier's reported delay minutes; ≥{MIN_CARRIER_FLIGHTS_PER_MONTH:,} flights/month.")
        by_c = data.agg("fact_route_monthly", p.start, p.end, f.route(carrier=None), ("carrier",))
        by_c = by_c[by_c["flights"] >= MIN_CARRIER_FLIGHTS_PER_MONTH * p.months * (0.03 if (f.origin or f.dest) else 1)]
        by_c = by_c.sort_values("propagation_index", ascending=False)
        if not by_c.empty:
            st.plotly_chart(ch.cause_share_stacked(by_c, "carrier", label_map={c: f"{c} · {data.carrier_label(c)}" for c in by_c["carrier"]}),
                            width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("Cause by time of day",
                   "Reported delay minutes per 100 scheduled departures by scheduled departure hour. Late-aircraft delay "
                   "accumulates as the day's rotations stack up.")
        hourly = data.agg("fact_origin_hourly", p.start, p.end, f.origin_side(), ("dep_hour",))
        hourly = hourly[(hourly["dep_hour"] >= 5) & (hourly["flights"] >= 50 * p.months)]
        hourly["label"] = hourly["dep_hour"].map(lambda h: f"{h:02d}")
        fig = ch.cause_share_stacked(hourly, "label", horizontal=False, per="flights", height=max(300, 24 * 8 + 70))
        fig.update_yaxes(title="Delay min / 100 flights")
        fig.update_xaxes(type="category", title="Scheduled departure hour")
        st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)

    with st.container(border=True):
        _propagation(f, cur)

    with st.container(border=True):
        ui.section("Cause by airport", "The 20 airports with the most reported delay minutes on departing flights, "
                                       "ordered by delay volume.")
        by_a = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=None), ("origin",))
        by_a = by_a.sort_values("delay_minutes", ascending=False).head(20)
        st.plotly_chart(ch.cause_share_stacked(by_a, "origin", label_map={a: data.airport_label(a) for a in by_a["origin"]}),
                        width="stretch", config=ch.PLOTLY_CONFIG)
        ui.note("Weather here means extreme weather as reported by carriers; non-extreme weather that slows the "
                "system is reported under NAS, so weather's real footprint is larger than the Weather share alone.")


def _propagation(f, cur: dict) -> None:
    p = f.period
    pi = METRICS["propagation_index"]
    ui.section("Delay Propagation Index by airport",
               "Late-aircraft delay minutes ÷ all reported delay minutes for flights departing each airport. A high index "
               "means delay at this station is mostly associated with inbound aircraft arriving late (delay imported from "
               "upstream), rather than originating locally. Aircraft rotations are not reconstructed. "
               f"Airports with ≥{MIN_AIRPORT_DEPARTURES_PER_MONTH:,} departures/month.")
    df = data.agg("fact_route_monthly", p.start, p.end, f.route(origin=None, dest=None), ("origin",))
    df = df[(df["flights"] >= MIN_AIRPORT_DEPARTURES_PER_MONTH * p.months) & (df["delay_minutes"] > 0)]
    if df.empty:
        ui.empty_state("No airports above the floor", "")
        return
    left, right = st.columns(2)
    hover = [("flights", "Departures", ":,.0f"), ("delay_min_per_100", "Delay min / 100 flights", ":,.0f"),
             ("avg_arr_delay", "Avg arr. delay (min)", ":.1f")]
    ref = cur["propagation_index"]
    with left:
        st.caption(f"Highest propagation (network {pi.format(ref)})")
        high = df.sort_values("propagation_index", ascending=False).head(12)
        st.plotly_chart(ch.ranked_bars(high, "origin", "propagation_index", color=t.CATEGORICAL[1], reference=ref,
                                       text_format="{:.0%}", hover=hover), width="stretch", config=ch.PLOTLY_CONFIG)
    with right:
        st.caption("Lowest propagation: delay mostly originates locally")
        low = df.sort_values("propagation_index").head(12)
        st.plotly_chart(ch.ranked_bars(low, "origin", "propagation_index", color=t.CATEGORICAL[0], reference=ref,
                                       text_format="{:.0%}", hover=hover), width="stretch", config=ch.PLOTLY_CONFIG)
    share = {c: cur[m] for c, m in CAUSE_MEASURES.items()}
    local = (share["carrier"] + share["nas"] + share["weather"] + share["security"]) / cur["delay_minutes"]
    ui.note(f"In scope, {fmt_pct(ref)} of reported delay minutes were associated with late-arriving aircraft and "
            f"{fmt_pct(local)} were attributed to carrier, NAS, weather or security causes at the flight itself.")
