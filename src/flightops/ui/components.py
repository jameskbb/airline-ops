"""Reusable UI components built from native Streamlit elements.

Every aggregated number can be traced back to the query that produced it:
metric cards and chart sections carry a "?" popover showing the metric definition,
the input measures, the query's SQL, and a link into the Data Explorer.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import streamlit as st

from flightops.data.measures import MEASURES
from flightops.data.months import format_month, parse_month
from flightops.data.query import Query
from flightops.metrics import METRICS, Delta, Direction, Period, compare
from flightops.metrics.definitions import Unit
from flightops.ui import data, nav
from flightops.utils.format import fmt_int

STYLE = Path(__file__).resolve().parents[3] / "assets" / "style.css"
MEASURE_TEXT = {m.name: m.description for m in MEASURES}
DIRECTION_TEXT = {
    Direction.HIGHER_IS_BETTER: "Higher is better.",
    Direction.LOWER_IS_BETTER: "Lower is better.",
    Direction.NEUTRAL: "Neither direction is better or worse.",
}
HELP_ICON = ":material/help:"


def load_css() -> None:
    st.html(f"<style>{STYLE.read_text()}</style>")


def freshness() -> str:
    end = data.metadata().get("coverage_end")
    return f"Data through {format_month(parse_month(end))}" if end else "No data loaded"


def page_header(title: str, subtitle: str, filters=None) -> None:
    st.title(title)
    st.caption(subtitle)
    badges = [f":blue-badge[:material/schedule: {freshness()} · monthly BTS reporting]"]
    if filters is not None:
        badges += [f":gray-badge[{name}: {value}]" for name, value in filters.chips]
    st.markdown(" ".join(badges))


# -- query explanations -------------------------------------------------------------
def query_details(query: Query, key: str) -> None:
    """Query ID, description, SQL, and a link to open it in the Data Explorer."""
    st.markdown(f"**Query `{query.id}`**  \n{query.describe()}")
    if query.not_applied:
        st.caption(f"Not applied (this table has no such column): {', '.join(query.not_applied)}")
    st.code(data.sql(query), language="sql")
    if st.button("Open in Data Explorer", key=f"open-{key}-{query.id}", icon=":material/table_view:"):
        nav.open_in_explorer(query.id)


def explain_metric(key: str, queries: dict[str, Query], values: dict[str, dict], widget_key: str) -> None:
    """Popover body: definition, calculation, inputs read from the actual results, and the queries."""
    metric = METRICS[key]
    st.markdown(f"**{metric.label}**")
    st.write(metric.definition)
    st.markdown(f"**Calculation:** `{metric.calculation}`")
    st.caption(DIRECTION_TEXT[metric.direction])
    rows = []
    for measure in metric.requires:
        row = {"Input": measure, "Meaning": MEASURE_TEXT.get(measure, "")}
        row.update({name.title(): fmt_int(v.get(measure)) for name, v in values.items() if v})
        rows.append(row)
    result = {"Input": f"= {metric.short}", "Meaning": "Result"}
    result.update({name.title(): metric.format(v.get(key)) for name, v in values.items() if v})
    st.dataframe(pd.DataFrame([*rows, result]), hide_index=True, width="stretch")
    for i, (name, query) in enumerate(queries.items()):
        if i == 0:
            query_details(query, f"{widget_key}-{name}")
        else:
            with st.expander(f"{name.title()} query · {query.id}"):
                query_details(query, f"{widget_key}-{name}")


def comparisons(make_query: Callable[[Period], Query], period: Period, label: str) -> tuple[dict, dict]:
    """Queries and single-row results for the period, the prior period and the same period last year.

    Comparison periods outside the loaded months are omitted rather than shown as zero.
    """
    periods = {"current": period, "previous": period.previous(), "prior year": period.prior_year()}
    queries, values = {}, {}
    for name, p in periods.items():
        if name != "current" and not data.in_range(p):
            continue
        queries[name] = make_query(p)
        values[name] = data.totals(queries[name], f"{label} ({name})")
    return queries, values


def _delta_color(delta: Delta | None) -> str:
    if delta is None or delta.favorable is None:
        return "off"
    return "normal" if (delta.change > 0) == delta.favorable else "inverse"


def _delta_markdown(delta: Delta | None, suffix: str) -> str:
    if delta is None:
        return f"— {suffix}"
    color = "gray" if delta.favorable is None else ("green" if delta.favorable else "red")
    arrow = ":material/arrow_upward:" if delta.change > 0 else ":material/arrow_downward:"
    return f":{color}[{arrow} {delta.text}] {suffix}"


def metric_row(keys: list[str], queries: dict[str, Query], values: dict[str, dict],
               labels: dict[str, str] | None = None, extra: list | None = None) -> None:
    """KPI cards for ``keys``. ``queries``/``values`` map "current", "previous", "prior year"
    to the query and its single-row result. ``extra`` adds callables that render more cards."""
    cards = [lambda k=k: _metric_card(k, queries, values, (labels or {}).get(k)) for k in keys]
    cards += extra or []
    for start in range(0, len(cards), 6):
        for col, card in zip(st.columns(6), cards[start:start + 6], strict=False):
            with col:
                card()


def _metric_card(key: str, queries: dict[str, Query], values: dict[str, dict], label: str | None) -> None:
    metric = METRICS[key]
    cur, prev, yoy = (values.get(n, {}) for n in ("current", "previous", "prior year"))
    months = queries["current"].start, queries["current"].end
    span = 1 if months[0] == months[1] else None
    d_prev = compare(metric, cur.get(key), prev.get(key)) if prev else None
    d_yoy = compare(metric, cur.get(key), yoy.get(key)) if yoy else None
    with st.container(border=True):
        head, icon = st.columns([5, 1], vertical_alignment="center", gap="small")
        head.caption(label or metric.label)
        with icon, st.popover("", icon=HELP_ICON, type="tertiary", help=f"How {metric.label} is calculated"):
            explain_metric(key, queries, values, f"kpi-{key}")
        st.metric(label or metric.label, metric.format(cur.get(key), compact=key == "delay_minutes"),
                  delta=d_prev.text if d_prev else None, delta_color=_delta_color(d_prev),
                  delta_description="MoM" if span else "vs prior",
                  label_visibility="collapsed")
        st.caption(_delta_markdown(d_yoy, "YoY"))


def stat_card(label: str, value: str, note: str = "", help: str | None = None) -> None:
    """A card for a derived statement (percentile, largest cause) with an explanation tooltip."""
    with st.container(border=True):
        st.caption(label, help=help)
        st.metric(label, value, label_visibility="collapsed")
        st.caption(note or " ")


def section(title: str, caption: str | None = None, queries: dict[str, Query] | None = None,
            metrics: tuple[str, ...] = ()) -> None:
    """Section heading with a "?" listing the metrics and queries behind the view."""
    head, icon = st.columns([14, 1], vertical_alignment="center")
    head.markdown(f"### {title}")
    if queries or metrics:
        with icon, st.popover("", icon=HELP_ICON, type="tertiary", help="How this view is built"):
            if caption:
                st.write(caption)
            for key in metrics:
                m = METRICS[key]
                st.markdown(f"**{m.label}** · {m.definition}  \n`{m.calculation}`")
            for name, query in (queries or {}).items():
                st.divider()
                st.caption(name)
                query_details(query, f"sec-{title}-{name}")
    if caption:
        st.caption(caption)


def metric_table(df: pd.DataFrame, columns: list[tuple[str, str]], height: int | None = None) -> None:
    """Dataframe whose metric columns take format and "?" help from the metric registry.

    ``columns`` is a list of (source column, header). Source columns that are metric
    keys are formatted by unit (rates shown as percentages) and get the definition as help.
    """
    out, config = {}, {}
    for col, header in columns:
        metric = METRICS.get(col)
        if metric is None:
            out[header] = df[col].to_numpy()
            if pd.api.types.is_integer_dtype(df[col]):
                config[header] = st.column_config.NumberColumn(header, format="localized")
            elif pd.api.types.is_float_dtype(df[col]):
                config[header] = st.column_config.NumberColumn(header, format="%.1f")
            continue
        rate = metric.unit is Unit.RATE
        out[header] = (df[col] * 100 if rate else df[col]).to_numpy()
        fmt = {Unit.RATE: "%.1f%%", Unit.MINUTES: "%.1f min", Unit.SCORE: "%.1f"}.get(metric.unit, "localized")
        config[header] = st.column_config.NumberColumn(
            header, format=fmt, help=f"{metric.definition} Calculation: {metric.calculation}.")
    st.dataframe(pd.DataFrame(out), hide_index=True, width="stretch", height=height or "auto", column_config=config)


# -- other elements --------------------------------------------------------------------
def brief(statements, provider_name: str) -> None:
    color = {"negative": "red", "positive": "green", "neutral": "blue"}
    st.markdown("\n\n".join(f":{color.get(s.tone, 'gray')}[●]&nbsp; {s.text}" for s in statements))
    st.caption(f"Generated by {provider_name}. Every figure comes from the queries listed under the \"?\".")


SEVERITY_COLOR = {"High impact": "red", "Elevated": "orange", "Watch": "gray"}


def signal_card(signal) -> None:
    metric = METRICS[signal.metric]
    if signal.kind == "delay_concentration":
        cur, base = f"{signal.current:.1%} of delay minutes", f"{signal.baseline:.1%} of departures"
    elif signal.kind == "cause_shift":
        cur, base = f"{signal.current:.1%} share", f"{signal.baseline:.1%} share"
    else:
        cur, base = metric.format(signal.current), metric.format(signal.baseline)
    tone = "green" if signal.direction == "improvement" else SEVERITY_COLOR[signal.severity]
    with st.container(border=True):
        st.markdown(f":{tone}-badge[{signal.severity} · {signal.direction}] :gray-badge[{signal.category}]")
        st.markdown(f"**{signal.headline}**")
        st.write(signal.detail)
        a, b, c = st.columns(3)
        a.caption(f"Current  \n**{cur}**")
        b.caption(f"Baseline  \n**{base}**")
        c.caption(f"Estimated impact  \n**{signal.extras.get('impact_text', '—')}**")
        st.caption(f"{signal.comparison}. Why it matters: {signal.why}")


def empty_state(title: str, message: str) -> None:
    st.info(f"**{title}.** {message}", icon=":material/info:")


def note(text: str) -> None:
    st.caption(text)


def footer() -> None:
    st.divider()
    st.caption(f"Source: U.S. DOT Bureau of Transportation Statistics, Marketing Carrier On-Time Performance · "
               f"updated through {data.metadata().get('coverage_end', '—')} · historical monthly reporting, "
               "not real-time flight status.")
