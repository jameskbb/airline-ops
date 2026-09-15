"""Carrier Benchmarking: how marketing carriers compare, and where each one is strong."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from flightops.analytics.benchmarks import STRENGTH_METRICS, relative_strengths
from flightops.charts import builders as ch
from flightops.charts import theme as t
from flightops.config import MIN_CARRIER_FLIGHTS_PER_MONTH
from flightops.metrics import METRICS, Period
from flightops.metrics.definitions import Unit
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current

TREND_METRICS = {"on_time_rate": "On-time %", "cancellation_rate": "Cancellation %",
                 "avg_arr_delay": "Avg arr. delay", "reliability_score": "Reliability Score"}


def render() -> None:
    f = current()
    p = f.period
    ui.page_header("Carrier Benchmarking",
                   "Branded networks (mainline plus regional partners flying under the brand) compared on the same "
                   f"metric definitions for {p.label}.", f)
    carriers_q = f.query(by=("carrier",), carrier=None)
    network_q = f.query(carrier=None)
    floor = MIN_CARRIER_FLIGHTS_PER_MONTH * p.months * (1 if not (f.origin or f.dest) else 0.03)
    df = data.run(carriers_q, "Carrier ranking")
    df = df[df["flights"] >= floor].sort_values("flights", ascending=False).reset_index(drop=True)
    if df.empty:
        ui.empty_state("No carriers above the volume floor", "Widen the period or clear airport filters.")
        return
    network = data.totals(network_q, "Carrier network reference")
    if f.carrier:
        ui.note(f"{data.carrier_label(f.carrier)} is highlighted; comparisons always include every carrier in scope.")

    with st.container(border=True):
        ui.section("Carrier ranking", f"Carriers with ≥{int(floor):,} flights in scope. Click a column header to sort; "
                                      "hover a header's ? for its definition.", {"Carriers": carriers_q})
        df["label"] = [f"{c} · {data.carrier_label(c)}" for c in df["carrier"]]
        ui.metric_table(df, [("label", "Carrier"), ("flights", "Flights"), ("reliability_score", "Reliability Score"),
                             ("on_time_rate", "On-time"), ("cancellation_rate", "Cancelled"),
                             ("severe_delay_rate", "Severe 60+"), ("avg_arr_delay", "Avg arr. delay"),
                             ("delay_min_per_100", "Delay min / 100 flights"), ("avg_taxi_out", "Avg taxi-out"),
                             ("propagation_index", "Propagation")])

    left, right = st.columns([1.1, 1], gap="medium")
    with left, st.container(border=True):
        ui.section("Reliability positioning",
                   "On-time arrival vs cancellation rate; bubble area = flights. Best position is bottom-right. Lines "
                   "mark the network values.", {"Carriers": carriers_q, "Network": network_q},
                   ("on_time_rate", "cancellation_rate"))
        plot = df.assign(color=[t.carrier_color(c) if (not f.carrier or c == f.carrier) else "#3a4552"
                                for c in df["carrier"]])
        st.plotly_chart(ch.bubble(plot, "on_time_rate", "cancellation_rate", "flights", "carrier", color="color",
                                  reference=network, hover=[("flights", "Flights", ":,.0f"),
                                                            ("severe_delay_rate", "Severe 60+", ":.1%")]),
                        width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("Relative strengths",
                   "Each carrier's gap to the peer median on each dimension, divided by the peer spread (median "
                   "absolute deviation) and oriented so blue = better and red = worse. Cells show actual values.",
                   {"Carriers": carriers_q}, STRENGTH_METRICS)
        _strengths(df)

    with st.container(border=True):
        months = data.loaded_months()
        c1, c2 = st.columns([2, 1])
        chosen = c1.multiselect("Carriers", list(df["carrier"]), default=[f.carrier] if f.carrier else list(df["carrier"][:4]),
                                max_selections=6, format_func=lambda c: f"{c} · {data.carrier_label(c)}",
                                key="carrier_trend")
        metric_key = c2.selectbox("Metric", list(TREND_METRICS), format_func=TREND_METRICS.get, key="carrier_metric")
        trend_q = f.query(period=Period(months[0], months[-1]), by=("carrier", "month"), carrier=chosen or None)
        ui.section("Carrier performance trend", "Monthly across the loaded history; the shaded band is the selected "
                                                "period.", {"Trend": trend_q}, (metric_key,))
        if chosen:
            fig = ch.lines(data.run(trend_q, "Carrier trend"), "month", metric_key, "carrier",
                           {c: t.carrier_color(c) for c in chosen}, height=330,
                           label_map={c: data.carrier_label(c) for c in chosen})
            fig.add_vrect(x0=pd.Timestamp(p.start) - pd.Timedelta(days=14),
                          x1=pd.Timestamp(p.end) + pd.Timedelta(days=14),
                          fillcolor="rgba(76,147,234,0.10)", line_width=0, layer="below")
            st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)


def _strengths(df: pd.DataFrame) -> None:
    strengths = relative_strengths(df, "carrier")
    if strengths.empty:
        return
    keys = list(dict.fromkeys(strengths["metric"]))
    z = strengths.pivot(index="carrier", columns="metric", values="standardized").reindex(index=df["carrier"], columns=keys)
    values = strengths.pivot(index="carrier", columns="metric", values="value").reindex(index=df["carrier"], columns=keys)

    def cell(v: float, key: str) -> str:
        unit = METRICS[key].unit
        return f"{v * 100:.1f}%" if unit is Unit.RATE else f"{v:.1f}" if unit is Unit.MINUTES else f"{v:,.0f}"

    text = [[cell(values.loc[c, k], k) for k in keys] for c in z.index]
    fig = go.Figure(go.Heatmap(
        z=(-z.clip(-2.5, 2.5)).to_numpy(), x=[METRICS[k].short for k in keys], y=list(z.index), text=text,
        texttemplate="%{text}", textfont=dict(size=11, color="#e6e9ee"), colorscale=t.DIVERGING, zmid=0,
        zmin=-2.5, zmax=2.5, xgap=2, ygap=2, showscale=False, hovertemplate="<b>%{y}</b> · %{x}: %{text}<extra></extra>",
    ))
    fig.update_xaxes(side="top", tickfont=dict(size=11), showgrid=False)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_layout(height=max(260, 34 * len(z) + 60), margin=dict(l=8, r=8, t=36, b=8))
    st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)
    lines = []
    for carrier, grp in strengths.groupby("carrier", sort=False):
        best, worst = grp.loc[grp["standardized"].idxmax()], grp.loc[grp["standardized"].idxmin()]
        if best["standardized"] > 0.5 or worst["standardized"] < -0.5:
            lines.append(f"**{carrier}** · strongest: {best['label'].lower()}; weakest: {worst['label'].lower()}")
    if lines:
        st.caption("  \n".join(lines[:8]))
