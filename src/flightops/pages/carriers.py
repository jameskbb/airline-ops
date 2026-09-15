"""Carrier Benchmarking: how marketing carriers compare, and where each one is strong."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from flightops.analytics.benchmarks import relative_strengths
from flightops.charts import builders as ch
from flightops.charts import theme as t
from flightops.config import MIN_CARRIER_FLIGHTS_PER_MONTH
from flightops.metrics import METRICS
from flightops.metrics.definitions import Unit
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current

TREND_METRICS = {"on_time_rate": "On-time %", "cancellation_rate": "Cancellation %",
                 "avg_arr_delay": "Avg arr. delay", "reliability_score": "Reliability Score"}


def render() -> None:
    f = current()
    p = f.period
    ui.page_header("Carrier Benchmarking", "Marketing carrier performance",
                   "Branded networks (mainline plus regional partners flying under the brand) compared on the same "
                   f"metric definitions for {p.label}.", f)
    scope = f.route(carrier=None)
    floor = MIN_CARRIER_FLIGHTS_PER_MONTH * p.months * (1 if not (f.origin or f.dest) else 0.03)
    df = data.agg("fact_route_monthly", p.start, p.end, scope, ("carrier",))
    df = df[df["flights"] >= floor].sort_values("flights", ascending=False).reset_index(drop=True)
    if df.empty:
        ui.empty_state("No carriers above the volume floor", "Widen the period or clear airport filters.")
        return
    network = data.period_totals(p, scope)
    if f.carrier:
        ui.note(f"{data.carrier_label(f.carrier)} is highlighted; comparisons always include every carrier in scope.")

    with st.container(border=True):
        ui.section("Carrier ranking", f"Minimum {int(floor):,} flights in scope. Click a column header to sort.")
        table = pd.DataFrame({
            "Carrier": [f"{c} · {data.carrier_label(c)}" for c in df["carrier"]],
            "Flights": df["flights"],
            "Reliability Score": df["reliability_score"],
            "On-time %": df["on_time_rate"] * 100,
            "Cancelled %": df["cancellation_rate"] * 100,
            "Severe 60+ %": df["severe_delay_rate"] * 100,
            "Avg arr. delay": df["avg_arr_delay"],
            "Delay min / 100 flights": df["delay_min_per_100"],
            "Avg taxi-out": df["avg_taxi_out"],
            "Propagation": df["propagation_index"] * 100,
        })
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            "Flights": st.column_config.NumberColumn(format="localized"),
            "Reliability Score": st.column_config.ProgressColumn(format="%.1f", min_value=50, max_value=100,
                                                                 help=METRICS["reliability_score"].definition),
            "On-time %": st.column_config.NumberColumn(format="%.1f%%", help=METRICS["on_time_rate"].definition),
            "Cancelled %": st.column_config.NumberColumn(format="%.2f%%"),
            "Severe 60+ %": st.column_config.NumberColumn(format="%.1f%%"),
            "Avg arr. delay": st.column_config.NumberColumn(format="%.1f min", help=METRICS["avg_arr_delay"].definition),
            "Delay min / 100 flights": st.column_config.NumberColumn(format="%.0f"),
            "Avg taxi-out": st.column_config.NumberColumn(format="%.1f min"),
            "Propagation": st.column_config.NumberColumn(format="%.1f%%", help=METRICS["propagation_index"].definition),
        })

    left, right = st.columns([1.1, 1], gap="medium")
    with left, st.container(border=True):
        ui.section("Reliability positioning",
                   "On-time arrival vs cancellation rate; bubble area = flights. Best position is bottom-right. "
                   "Lines mark the network values.")
        plot = df.assign(color=[t.carrier_color(c) if (not f.carrier or c == f.carrier) else "#3a4552"
                                for c in df["carrier"]])
        fig = ch.bubble(plot, "on_time_rate", "cancellation_rate", "flights", "carrier", color="color",
                        reference=network, hover=[("flights", "Flights", ":,.0f"),
                                                  ("severe_delay_rate", "Severe 60+", ":.1%")])
        st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)
    with right, st.container(border=True):
        ui.section("Relative strengths",
                   "Gap to the peer median on each dimension, oriented so blue = better and red = worse. "
                   "Cell text shows the carrier's actual value.")
        _strengths(df)

    with st.container(border=True):
        ui.section("Carrier performance trend", "Monthly, across the loaded history; the shaded band is the selected period.")
        c1, c2 = st.columns([2, 1])
        default = [f.carrier] if f.carrier else list(df["carrier"].head(4))
        with c1:
            chosen = st.multiselect("Carriers", list(df["carrier"]), default=default, max_selections=6,
                                    format_func=lambda c: f"{c} · {data.carrier_label(c)}", key="carrier_trend")
        with c2:
            metric_key = st.selectbox("Metric", list(TREND_METRICS), format_func=TREND_METRICS.get, key="carrier_metric")
        if chosen:
            months = data.loaded_months()
            trend = data.agg("fact_route_monthly", months[0], months[-1], f.route(carrier=list(chosen)),
                             ("carrier", "month"))
            fig = ch.lines(trend, "month", metric_key, "carrier", {c: t.carrier_color(c) for c in chosen}, height=330,
                           label_map={c: data.carrier_label(c) for c in chosen})
            fig.add_vrect(x0=pd.Timestamp(p.start) - pd.Timedelta(days=14), x1=pd.Timestamp(p.end) + pd.Timedelta(days=14),
                          fillcolor="rgba(76,147,234,0.10)", line_width=0, layer="below")
            st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)


def _strengths(df: pd.DataFrame) -> None:
    strengths = relative_strengths(df, "carrier")
    if strengths.empty:
        return
    labels = list(dict.fromkeys(strengths["label"]))
    z = strengths.pivot(index="carrier", columns="label", values="standardized").reindex(index=df["carrier"], columns=labels)
    values = strengths.pivot(index="carrier", columns="metric", values="value").reindex(index=df["carrier"])
    text = []
    for carrier in z.index:
        row = []
        for key in dict.fromkeys(strengths["metric"]):
            v = values.loc[carrier, key]
            metric = METRICS[key]
            row.append(f"{v * 100:.1f}%" if metric.unit is Unit.RATE else f"{v:.1f}" if metric.unit is Unit.MINUTES else f"{v:,.0f}")
        text.append(row)
    fig = go.Figure(go.Heatmap(
        z=(-z.clip(-2.5, 2.5)).to_numpy(), x=labels, y=list(z.index), text=text, texttemplate="%{text}",
        textfont=dict(size=11, color="#e6e9ee"), colorscale=t.DIVERGING, zmid=0, zmin=-2.5, zmax=2.5, xgap=2, ygap=2,
        showscale=False, hovertemplate="<b>%{y}</b> · %{x}: %{text}<extra></extra>",
    ))
    fig.update_xaxes(side="top", tickfont=dict(size=11), showgrid=False)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_layout(height=max(260, 34 * len(z) + 60), margin=dict(l=8, r=8, t=36, b=8))
    st.plotly_chart(fig, width="stretch", config=ch.PLOTLY_CONFIG)

    lines = []
    for carrier, grp in strengths.groupby("carrier", sort=False):
        best = grp.loc[grp["standardized"].idxmax()]
        worst = grp.loc[grp["standardized"].idxmin()]
        if best["standardized"] > 0.5 or worst["standardized"] < -0.5:
            lines.append(f"**{carrier}** · strongest: {best['label'].lower()}; weakest: {worst['label'].lower()}")
    if lines:
        st.caption("  \n".join(lines[:8]))
