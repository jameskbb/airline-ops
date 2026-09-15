"""Chart builders. Every chart answers one question, on one y-axis, with units in hover."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from flightops.charts import theme as t
from flightops.data.measures import CAUSE_LABELS, CAUSE_MEASURES
from flightops.metrics.definitions import METRICS, Unit

PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}


def axis_format(metric_key: str) -> dict:
    unit = METRICS[metric_key].unit
    if unit is Unit.RATE:
        return {"tickformat": ".0%"}
    if unit is Unit.MINUTES:
        return {"ticksuffix": " min"}
    return {"tickformat": "~s"}


def _fine_rate_format(metric_key: str, values: pd.Series) -> dict:
    """Small-range rate axes (e.g. 0.5%–4%) need a decimal so ticks don't repeat."""
    fmt = axis_format(metric_key)
    if METRICS[metric_key].unit is Unit.RATE and len(values) and values.max() - values.min() < 0.06:
        fmt = {"tickformat": ".1%"}
    return fmt


def hover_format(metric_key: str) -> str:
    unit = METRICS[metric_key].unit
    return {Unit.RATE: ":.1%", Unit.MINUTES: ":.1f", Unit.SCORE: ":.1f"}.get(unit, ":,.0f")


def _unit_suffix(metric_key: str) -> str:
    return " min" if METRICS[metric_key].unit is Unit.MINUTES else ""


def _shade(fig: go.Figure, start: dt.date | None, end: dt.date | None, row: int | None = None) -> None:
    if start is None or end is None:
        return
    x0 = pd.Timestamp(start) - pd.Timedelta(days=14)
    x1 = pd.Timestamp(end) + pd.Timedelta(days=14)
    kwargs = {"row": row, "col": 1} if row else {}
    fig.add_vrect(x0=x0, x1=x1, fillcolor="rgba(76,147,234,0.10)", line_width=0, layer="below", **kwargs)


def monthly_small_multiples(
    df: pd.DataFrame, metrics: list[str], highlight: tuple[dt.date, dt.date] | None = None,
    reference: pd.DataFrame | None = None, reference_label: str = "Network", label: str = "Selection",
    height: int = 330,
) -> go.Figure:
    """Stacked panels sharing a month x-axis: one metric per panel (never dual-axis)."""
    fig = make_subplots(rows=len(metrics), cols=1, shared_xaxes=True, vertical_spacing=0.09,
                        subplot_titles=[METRICS[m].label for m in metrics])
    for i, key in enumerate(metrics, start=1):
        if reference is not None:
            fig.add_trace(go.Scatter(
                x=reference["month"], y=reference[key], name=reference_label, mode="lines",
                line=dict(color=t.MUTED, width=1.5), legendgroup="ref", showlegend=i == 1,
                hovertemplate=f"{reference_label}: %{{y{hover_format(key)}}}{_unit_suffix(key)}<extra></extra>",
            ), row=i, col=1)
        fig.add_trace(go.Scatter(
            x=df["month"], y=df[key], name=label, mode="lines+markers",
            line=dict(color=t.CATEGORICAL[0] if i == 1 else t.CATEGORICAL[1], width=2),
            marker=dict(size=5), legendgroup="sel", showlegend=reference is not None and i == 1,
            hovertemplate=f"%{{x|%b %Y}}<br>{METRICS[key].label}: %{{y{hover_format(key)}}}{_unit_suffix(key)}<extra></extra>",
        ), row=i, col=1)
        fig.update_yaxes(**axis_format(key), row=i, col=1, nticks=4)
        _shade(fig, *(highlight or (None, None)), row=i)
    fig.update_xaxes(dtick="M3", tickformat="%b\n%Y")
    fig.update_annotations(font=dict(size=12, color=t.INK_2), x=0, xanchor="left")
    fig.update_layout(height=height, hovermode="x unified", margin=dict(t=24, l=8, r=8, b=8),
                      legend=dict(y=1.12))
    return fig


def lines(df: pd.DataFrame, x: str, y: str, color: str, color_map: dict[str, str] | None = None,
          height: int = 300, label_map: dict[str, str] | None = None) -> go.Figure:
    """One metric over time for several entities (fixed entity colors)."""
    fig = go.Figure()
    for key, grp in df.groupby(color, sort=False):
        name = (label_map or {}).get(key, key)
        fig.add_trace(go.Scatter(
            x=grp[x], y=grp[y], name=name, mode="lines+markers", marker=dict(size=5),
            line=dict(width=2, color=(color_map or {}).get(key)),
            hovertemplate=f"{name} · %{{x|%b %Y}}: %{{y{hover_format(y)}}}{_unit_suffix(y)}<extra></extra>",
        ))
        last = grp.iloc[-1]
        fig.add_annotation(x=last[x], y=last[y], text=f" {key}", showarrow=False, xanchor="left",
                           font=dict(size=11, color=t.INK_2))
    fig.update_yaxes(**axis_format(y))
    fig.update_xaxes(tickformat="%b\n%Y")
    fig.update_layout(height=height, hovermode="x unified", margin=dict(l=8, r=36, t=30, b=8))
    return fig


def ranked_bars(
    df: pd.DataFrame, label: str, value: str, height: int | None = None, color: str = t.CATEGORICAL[0],
    reference: float | None = None, reference_label: str = "Network", hover: list[tuple[str, str, str]] = (),
    text_format: str | None = None, highlight: str | None = None,
) -> go.Figure:
    """Horizontal bars, largest at top. ``hover`` = [(column, label, d3-format suffix)]."""
    data = df.iloc[::-1]
    custom = data[[c for c, _, _ in hover]].to_numpy() if hover else None
    lines_ = [f"<b>%{{y}}</b><br>{METRICS[value].label if value in METRICS else value}: "
              f"%{{x{hover_format(value) if value in METRICS else ':,.0f'}}}{_unit_suffix(value) if value in METRICS else ''}"]
    for i, (_, lab, fmt) in enumerate(hover):
        lines_.append(f"{lab}: %{{customdata[{i}]{fmt}}}")
    colors = [t.ACCENT if highlight and v == highlight else color for v in data[label]] if highlight else color
    fig = go.Figure(go.Bar(
        x=data[value], y=data[label], orientation="h", marker=dict(color=colors, line=dict(width=0)),
        customdata=custom, hovertemplate="<br>".join(lines_) + "<extra></extra>",
        text=[text_format.format(v) for v in data[value]] if text_format else None,
        textposition="outside", textfont=dict(size=11, color=t.INK_2), cliponaxis=False,
    ))
    if reference is not None:
        fig.add_vline(x=reference, line=dict(color=t.INK_2, width=1))
        fig.add_annotation(x=reference, y=1.02, yref="paper", text=reference_label, showarrow=False,
                           font=dict(size=10, color=t.MUTED))
    if value in METRICS:
        fig.update_xaxes(**axis_format(value))
    fig.update_xaxes(showgrid=True)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11.5, color=t.INK_2))
    fig.update_layout(height=height or max(220, 26 * len(df) + 50), margin=dict(l=8, r=40, t=18, b=8),
                      bargap=0.35)
    return fig


def cause_bars(values: dict[str, float], height: int = 250) -> go.Figure:
    """Delay-cause share of reported minutes, one bar per cause, sorted."""
    total = sum(values.values()) or 1
    rows = sorted(((c, values[m]) for c, m in CAUSE_MEASURES.items()), key=lambda kv: kv[1])
    fig = go.Figure(go.Bar(
        x=[v / total for _, v in rows], y=[CAUSE_LABELS[c] for c, _ in rows], orientation="h",
        marker=dict(color=[t.CAUSE_COLORS[c] for c, _ in rows]),
        customdata=[[v] for _, v in rows],
        text=[f"{v / total:.1%}" for _, v in rows], textposition="outside", cliponaxis=False,
        textfont=dict(size=11, color=t.INK_2),
        hovertemplate="<b>%{y}</b><br>Share of reported delay minutes: %{x:.1%}<br>"
                      "Minutes: %{customdata[0]:,.0f}<extra></extra>",
    ))
    fig.update_xaxes(tickformat=".0%", showgrid=True)
    fig.update_yaxes(tickfont=dict(size=12, color=t.INK_2))
    fig.update_layout(height=height, margin=dict(l=8, r=48, t=8, b=8), bargap=0.4)
    return fig


def cause_share_stacked(df: pd.DataFrame, category: str, height: int | None = None, horizontal: bool = True,
                        per: str = "share", label_map: dict[str, str] | None = None) -> go.Figure:
    """100% stacked (or per-flight) cause composition across a category or time."""
    fig = go.Figure()
    total = df[list(CAUSE_MEASURES.values())].sum(axis=1).replace(0, float("nan"))
    labels = df[category].map(label_map) if label_map else df[category]
    for cause in t.CAUSE_ORDER:
        measure = CAUSE_MEASURES[cause]
        if per == "share":
            values, fmt, axis = df[measure] / total, ":.1%", {"tickformat": ".0%"}
        else:  # minutes per 100 scheduled flights
            values, fmt, axis = df[measure] / df["flights"] * 100, ":,.0f", {"tickformat": "~s"}
        common = dict(name=CAUSE_LABELS[cause], marker=dict(color=t.CAUSE_COLORS[cause], line=dict(width=0)),
                      hovertemplate=f"<b>%{{{'y' if horizontal else 'x'}}}</b><br>{CAUSE_LABELS[cause]}: "
                                    f"%{{{'x' if horizontal else 'y'}{fmt}}}<extra></extra>")
        if horizontal:
            fig.add_trace(go.Bar(x=values, y=labels, orientation="h", **common))
        else:
            fig.add_trace(go.Bar(x=labels, y=values, **common))
    fig.update_layout(barmode="stack", height=height or max(260, 24 * len(df) + 70),
                      margin=dict(l=8, r=8, t=30, b=8), bargap=0.3,
                      legend=dict(traceorder="normal", y=1.02))
    if horizontal:
        fig.update_xaxes(**axis, showgrid=True)
        fig.update_yaxes(autorange="reversed", tickfont=dict(size=11.5, color=t.INK_2))
    else:
        fig.update_yaxes(**axis)
    return fig


def heatmap(pivot: pd.DataFrame, metric_key: str, height: int | None = None, x_title: str = "",
            zmin: float | None = None, zmax: float | None = None, counts: pd.DataFrame | None = None,
            y_labels: list[str] | None = None) -> go.Figure:
    """Sequential 'friction' heatmap: brighter = worse for lower-is-better metrics."""
    fmt = hover_format(metric_key)
    custom = counts.reindex(index=pivot.index, columns=pivot.columns).to_numpy() if counts is not None else None
    hover = f"<b>%{{y}}</b> · %{{x}}<br>{METRICS[metric_key].label}: %{{z{fmt}}}{_unit_suffix(metric_key)}"
    if custom is not None:
        hover += "<br>Flights: %{customdata:,.0f}"
    fig = go.Figure(go.Heatmap(
        z=pivot.to_numpy(), x=list(pivot.columns), y=y_labels or list(pivot.index),
        colorscale=t.SEQUENTIAL, zmin=zmin, zmax=zmax, xgap=2, ygap=2, customdata=custom,
        hovertemplate=hover + "<extra></extra>",
        colorbar=dict(tickformat=".0%" if METRICS[metric_key].unit is Unit.RATE else None, thickness=10,
                      outlinewidth=0, len=0.9, tickfont=dict(color=t.MUTED, size=10)),
    ))
    fig.update_xaxes(title=x_title, side="top", tickfont=dict(size=11), showgrid=False, type="category")
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(size=11.5, color=t.INK_2), type="category")
    fig.update_layout(height=height or max(260, 22 * len(pivot) + 80), margin=dict(l=8, r=8, t=30, b=8))
    return fig


def bubble(df: pd.DataFrame, x: str, y: str, size: str, label: str, color: str | None = None,
           height: int = 420, reference: dict | None = None, hover: list[tuple[str, str, str]] = ()) -> go.Figure:
    """Positioning scatter with direct labels (identity never by color alone)."""
    sizeref = 2.0 * df[size].max() / (46**2) if len(df) else 1
    custom = df[[c for c, _, _ in hover]].to_numpy() if hover else None
    hover_lines = ["<b>%{text}</b>", f"{METRICS[x].label}: %{{x{hover_format(x)}}}{_unit_suffix(x)}",
                   f"{METRICS[y].label}: %{{y{hover_format(y)}}}{_unit_suffix(y)}"]
    hover_lines += [f"{lab}: %{{customdata[{i}]{fmt}}}" for i, (_, lab, fmt) in enumerate(hover)]
    fig = go.Figure(go.Scatter(
        x=df[x], y=df[y], mode="markers+text", text=df[label], textposition="top center",
        textfont=dict(size=11.5, color=t.INK), customdata=custom,
        marker=dict(size=df[size], sizemode="area", sizeref=sizeref, sizemin=6,
                    color=df[color] if color else t.CATEGORICAL[0], opacity=0.85,
                    line=dict(width=2, color=t.SURFACE)),
        hovertemplate="<br>".join(hover_lines) + "<extra></extra>",
    ))
    if reference:
        fig.add_vline(x=reference[x], line=dict(color=t.AXIS, width=1))
        fig.add_hline(y=reference[y], line=dict(color=t.AXIS, width=1))
        fig.add_annotation(x=reference[x], y=1.0, yref="paper", text="Network", showarrow=False,
                           font=dict(size=10, color=t.MUTED), xanchor="left")
    fig.update_xaxes(**_fine_rate_format(x, df[x]), title=METRICS[x].label, showgrid=True)
    fig.update_yaxes(**_fine_rate_format(y, df[y]), title=METRICS[y].label)
    fig.update_layout(height=height, margin=dict(l=8, r=16, t=20, b=8))
    return fig


def profile(df: pd.DataFrame, x: str, metric_key: str, x_title: str, reference: pd.DataFrame | None = None,
            reference_label: str = "Network", height: int = 300, tick_labels: dict | None = None) -> go.Figure:
    """Metric by an ordered bucket (hour/day) with scheduled volume as a second small panel."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.06)
    xs = df[x].map(tick_labels) if tick_labels else df[x]
    if reference is not None:
        rx = reference[x].map(tick_labels) if tick_labels else reference[x]
        fig.add_trace(go.Scatter(x=rx, y=reference[metric_key], name=reference_label, mode="lines",
                                 line=dict(color=t.MUTED, width=1.5),
                                 hovertemplate=f"{reference_label}: %{{y{hover_format(metric_key)}}}{_unit_suffix(metric_key)}<extra></extra>"),
                      row=1, col=1)
    fig.add_trace(go.Scatter(x=xs, y=df[metric_key], name=METRICS[metric_key].label, mode="lines+markers",
                             line=dict(color=t.CATEGORICAL[0], width=2), marker=dict(size=6),
                             hovertemplate=f"{METRICS[metric_key].label}: %{{y{hover_format(metric_key)}}}{_unit_suffix(metric_key)}<extra></extra>"),
                  row=1, col=1)
    fig.add_trace(go.Bar(x=xs, y=df["flights"], name="Scheduled flights", marker=dict(color="#2c3a4b"),
                         hovertemplate="Scheduled flights: %{y:,.0f}<extra></extra>"), row=2, col=1)
    fig.update_yaxes(**axis_format(metric_key), row=1, col=1, nticks=5)
    fig.update_yaxes(tickformat="~s", row=2, col=1, nticks=3, title=dict(text="Flights", font=dict(size=10)))
    fig.update_xaxes(title=x_title, row=2, col=1, type="category", tickangle=0)
    fig.update_xaxes(type="category", row=1, col=1, tickangle=0)
    fig.update_layout(height=height, hovermode="x unified", margin=dict(l=8, r=8, t=24, b=8),
                      legend=dict(y=1.08), bargap=0.25)
    return fig


def distribution_strip(values: pd.Series, marker_value: float, metric_key: str, marker_label: str,
                       height: int = 150) -> go.Figure:
    """Peer distribution (histogram) with the selected entity marked."""
    fig = go.Figure(go.Histogram(x=values, nbinsx=30, marker=dict(color="#2c3a4b", line=dict(width=0)),
                                 hovertemplate=f"{METRICS[metric_key].label}: %{{x}}<br>Peers: %{{y}}<extra></extra>"))
    fig.add_vline(x=marker_value, line=dict(color=t.CATEGORICAL[1], width=2))
    fig.add_annotation(x=marker_value, y=1.0, yref="paper", text=marker_label, showarrow=False,
                       font=dict(size=11, color=t.INK), xanchor="left", bgcolor=t.SURFACE)
    fig.update_xaxes(**axis_format(metric_key))
    fig.update_yaxes(title="Peer routes", nticks=3)
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=16, b=8), bargap=0.05)
    return fig
