"""Methodology: definitions, lineage, coverage and data-quality checks."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from flightops.config import (
    MIN_AIRPORT_DEPARTURES_PER_MONTH,
    MIN_CARRIER_FLIGHTS_PER_MONTH,
    MIN_ROUTE_FLIGHTS_PER_MONTH,
    ON_TIME_THRESHOLD_MIN,
    SEVERE_DELAY_THRESHOLD_MIN,
)
from flightops.data.measures import MEASURES
from flightops.data.months import format_month, month_key, month_range, parse_month
from flightops.data.query import TABLE_DESCRIPTIONS
from flightops.metrics import METRICS
from flightops.ui import components as ui
from flightops.ui import data
from flightops.utils.format import fmt_int

LINEAGE = """
digraph {
  rankdir=LR; bgcolor="transparent";
  node [shape=box, style="rounded,filled", fillcolor="#121821", color="#2b3645", fontcolor="#e6e9ee",
        fontname="Helvetica", fontsize=11];
  edge [color="#56606c"];
  bts [label="U.S. DOT / BTS TranStats\\nmonthly ZIP files"];
  validate [label="Validation + transformation\\n(DuckDB, schema contract, DQ report)"];
  parquet [label="Parquet analytical layer\\n4 fact tables, 1 partition per month"];
  query [label="Query store\\n(every read is a logged Query)"];
  metrics [label="Metric layer\\n(definitions, direction, comparisons)"];
  app [label="Streamlit pages\\n+ Data Explorer"];
  bts -> validate -> parquet -> query -> metrics -> app;
}
"""


def render() -> None:
    meta = data.metadata()
    ui.page_header("Methodology", "Source, coverage, metric definitions, aggregation design, limitations and "
                                  "data-quality results.")
    loaded = meta.get("months_loaded", [])
    end = parse_month(meta["coverage_end"]) if meta.get("coverage_end") else None
    lag = (dt.date.today().year - end.year) * 12 + dt.date.today().month - end.month if end else None
    for col, (label, value, note) in zip(st.columns(5), [
        ("Coverage", f"{len(loaded)} months",
         f"{format_month(parse_month(meta['coverage_start']), 'short')} – {format_month(end, 'short')}" if end else ""),
        ("Flights analyzed", fmt_int(meta.get("flights_loaded")), f"{fmt_int(meta.get('raw_rows_processed'))} raw rows"),
        ("Reporting lag", f"{lag} months" if lag is not None else "—",
         f"Latest BTS month published: {meta.get('latest_published_month', '—')}"),
        ("Last refresh", (meta.get("generated_at") or "—")[:10], "UTC, by scripts/sync_bts.py"),
        ("Analytical layer", f"{sum(meta.get('table_bytes', {}).values()) / 1e6:.1f} MB",
         f"{fmt_int(sum(meta.get('table_rows', {}).values()))} aggregate rows"),
    ], strict=True):
        with col:
            ui.stat_card(label, value, note)

    tabs = st.tabs(["Source & scope", "Metric definitions", "Aggregation & lineage", "Benchmarks & signals",
                    "Data quality", "Limitations"])
    with tabs[0]:
        _source(meta)
    with tabs[1]:
        _definitions()
    with tabs[2]:
        _lineage(meta)
    with tabs[3]:
        _benchmarks()
    with tabs[4]:
        _quality(meta, data.month_reports())
    with tabs[5]:
        _limitations()


def _source(meta: dict) -> None:
    st.markdown(f"""
**Source.** {meta.get('source', 'U.S. DOT Bureau of Transportation Statistics')}: *{meta.get('dataset', '')}*,
downloaded directly from the TranStats PREZIP directory, one ZIP per reporting month. Airport names and coordinates
come from the BTS Aviation Support Tables *Master Coordinate* table, using the latest record per airport.

**Marketing carriers.** Records are reported by the *marketing* (branded) network, so regional flying sold as American
Eagle, Delta Connection or United Express rolls up to AA, DL or UA. Records BTS marks as duplicate code-share reports
(`Duplicate = Y`) are excluded so a flight is never counted twice.

**Reporting scope.** BTS on-time reporting covers scheduled domestic passenger flights of U.S. carriers above the DOT
reporting threshold (0.5% of domestic scheduled-passenger revenue), plus carriers reporting voluntarily. "The network"
here means this reporting population, not all U.S. aviation.

**Freshness.** BTS publishes each month roughly two to three months after it ends. This is historical operational
reporting, not live flight status. The newest loaded month is **{meta.get('coverage_end', '—')}**.
""")
    seen = set().union(*data.month_reports()["carriers"])
    carriers = data.dimension("dim_carrier")
    st.dataframe(carriers[carriers["carrier"].isin(seen)].rename(columns={
        "carrier": "Code", "carrier_name": "Marketing carrier", "carrier_short": "Short name"}),
        hide_index=True, width="content")


def _definitions() -> None:
    st.markdown(f"""
**Populations.** *Scheduled flights* are all non-duplicate records. *Completed arrivals* are flights that were neither
cancelled nor diverted (BTS populates `ArrDel15` only for these). Cancelled flights are never counted as arrival
delays; diverted flights appear only in the diversion rate and in scheduled volume.

**On-time (DOT convention).** A flight is on time if it arrives less than {ON_TIME_THRESHOLD_MIN} minutes after its
scheduled arrival. **Severe delay** means {SEVERE_DELAY_THRESHOLD_MIN}+ minutes late.

**Two kinds of average delay.** *Schedule variance* averages signed `ArrDelay` (early arrivals offset late ones).
*Positive delay* averages `ArrDelayMinutes`, where early arrivals count as 0.

**Direction.** Each metric declares whether higher is better, lower is better or neutral; delta colors and arrows
follow that declaration. Rates change in percentage points (pts), minute averages in minutes, counts in relative %.

This table is generated from the metric registry that computes every number in the app.
""")
    st.dataframe(pd.DataFrame([{"Metric": m.label, "Definition": m.definition, "Calculation": m.calculation,
                                "Inputs": ", ".join(m.requires), "Unit": m.unit.value, "Better when": m.direction.value}
                               for m in METRICS.values()]), hide_index=True, width="stretch", height=560)
    st.markdown("""
**Delay causes.** Carriers attribute arrival delay minutes (15+ minute delays only) to five causes: *Carrier*
(maintenance, crew, cleaning, fueling), *Extreme Weather*, *NAS* (non-extreme weather, airport operations, heavy traffic,
ATC), *Security* and *Late-arriving aircraft* (the previous flight with the same aircraft arrived late). The causes sum
to the arrival delay minutes.
""")


def _lineage(meta: dict) -> None:
    st.graphviz_chart(LINEAGE, width="stretch")
    st.markdown("""
**Additive facts only.** Fact tables hold counts and minute sums, never ratios. Every KPI is derived after aggregation,
so any slice re-derives rates correctly instead of averaging averages. The Data Explorer shows each query's SQL, its
result and the source rows it sums.
""")
    tables = pd.DataFrame([{"Table": name, "Grain": grain} for name, grain in TABLE_DESCRIPTIONS.items()])
    tables["Rows"] = tables["Table"].map(lambda n: fmt_int(meta.get("table_rows", {}).get(n)))
    tables["Size"] = tables["Table"].map(lambda n: f"{meta.get('table_bytes', {}).get(n, 0) / 1e6:.1f} MB")
    st.dataframe(tables, hide_index=True, width="stretch")
    st.markdown("""
**Idempotent refresh.** Each month is its own Parquet partition per table. Re-running a month replaces exactly that
month, months outside the window are pruned, and every sync reconciles flight counts across all fact tables against
the month's data-quality report. A mismatch fails the run.

**Periods.** Analytical periods are whole reporting months. Comparisons use the preceding period of equal length and
the same months one year earlier, when they are loaded.
""")
    with st.expander("Stored measures"):
        st.dataframe(pd.DataFrame([{"Measure": m.name, "Definition": m.description} for m in MEASURES]),
                     hide_index=True, width="stretch")


def _benchmarks() -> None:
    st.markdown(f"""
**Minimum-volume rules.** Rankings include airports with ≥{MIN_AIRPORT_DEPARTURES_PER_MONTH:,} departures per month,
carriers with ≥{MIN_CARRIER_FLIGHTS_PER_MONTH:,} flights per month and routes with ≥{MIN_ROUTE_FLIGHTS_PER_MONTH}
flights per month, scaled by the number of months in the period.

**Airport peer groups.** FAA hub cut points (≥1%, ≥0.25%, ≥0.05%) are applied to each airport's share of scheduled
departures in scope; percentiles compare an airport only with its own tier.

**Route peer groups.** Directional routes in the same distance band (<500, 500–999, 1,000–1,499, 1,500–2,499, 2,500+
miles) that clear the route volume floor. Percentile = share of peers with a lower on-time rate.

**Hotspots.** *Excess delayed arrivals* = delayed arrivals − completed arrivals × the reference 15+ delay rate.

**Delay Propagation Index.** Late-aircraft minutes ÷ total reported delay minutes: delay associated with inbound
aircraft arriving late. It does not trace individual aircraft.

**Narrative.** The Latest Operations Brief is produced by rules from a structured facts object built from logged
queries. An optional LLM narrator (`FLIGHTOPS_BRIEF_PROVIDER=anthropic`) receives only those facts, and its output is
discarded if it contains a figure that is not in them.
""")


def _quality(meta: dict, reports: pd.DataFrame) -> None:
    if reports.empty:
        ui.empty_state("No data-quality reports", "Run the sync script.")
        return
    months = month_range(parse_month(meta["coverage_start"]), parse_month(meta["coverage_end"]))
    missing = [month_key(m) for m in months if month_key(m) not in set(reports["month"])]
    unexpected = {k: v for r in reports["unexpected_carriers"] for k, v in r.items()}
    new_cols = sorted({c for r in reports["extra_columns"] for c in r})
    cause_rate = reports["cause_reconciled_rows"].sum() / max(reports["cause_rows"].sum(), 1)
    total = lambda col: int(reports[col].sum())  # noqa: E731
    status = lambda ok: "Pass" if ok else "Warn"  # noqa: E731
    st.dataframe(pd.DataFrame([
        ("Months loaded", f"{len(reports)} of {len(months)} in window", status(not missing)),
        ("Missing months", ", ".join(missing) or "None", status(not missing)),
        ("Latest available period", meta.get("latest_published_month", "—"), "Info"),
        ("Raw rows processed", fmt_int(total("raw_rows")), "Info"),
        ("Duplicate code-share rows excluded", fmt_int(total("duplicate_rows")), "Info"),
        ("Invalid or out-of-month dates", fmt_int(total("invalid_date_rows") + total("out_of_month_rows")),
         status(total("invalid_date_rows") == 0)),
        ("Rows missing airport/carrier keys", fmt_int(total("invalid_airport_rows")), status(total("invalid_airport_rows") == 0)),
        ("Unexpected carrier codes", ", ".join(f"{k} ({v:,})" for k, v in unexpected.items()) or "None", status(not unexpected)),
        ("New source columns", ", ".join(new_cols) or "None", "Pass" if not new_cols else "Info"),
        ("Completed flights missing ArrDel15", fmt_int(total("null_arr_flag_completed")),
         status(total("null_arr_flag_completed") == 0)),
        ("Departed flights missing TaxiOut", fmt_int(total("null_taxi_out_departed")), "Info"),
        ("Cancellations without a reason code", fmt_int(total("cancelled_without_code")),
         status(total("cancelled_without_code") == 0)),
        ("Delay causes reconcile to arrival delay (±1 min)", f"{cause_rate:.3%} of delayed flights", status(cause_rate > 0.99)),
        ("Fact tables reconcile to loaded rows", "Verified at every sync (the sync fails otherwise)", "Pass"),
        ("Airports without coordinates", str(meta.get("dimensions", {}).get("dim_airport_missing_coordinates", "—")),
         status(not meta.get("dimensions", {}).get("dim_airport_missing_coordinates"))),
    ], columns=["Check", "Result", "Status"]), hide_index=True, width="stretch")
    per_month = pd.DataFrame({
        "Month": reports["month"], "Raw rows": reports["raw_rows"], "Flights loaded": reports["loaded_rows"],
        "Cancelled": reports["cancelled"], "Diverted": reports["diverted"], "Carriers": reports["carriers"].map(len),
        "Null TaxiOut (departed)": reports["null_taxi_out_departed"],
        "Cause reconciliation (%)": (reports["cause_reconciled_rows"] / reports["cause_rows"].clip(lower=1) * 100).round(2),
        "Processed": reports["processed_at"].str[:10],
    }).sort_values("Month", ascending=False)
    st.dataframe(per_month, hide_index=True, width="stretch")


def _limitations() -> None:
    st.markdown("""
- **Not real time.** Monthly BTS reporting lags by roughly two to three months.
- **Reported, not causal, delay causes.** Carriers assign cause minutes under DOT guidance, and attribution practices
  can differ between carriers.
- **Weather is understated by the Weather cause.** Non-extreme weather effects are reported under NAS.
- **Propagation is associated, not traced.** The Delay Propagation Index uses the reported late-aircraft cause.
- **Departure-hour attribution.** Hourly views use the *scheduled* local departure hour at the origin airport.
- **Reporting population.** Only reporting carriers are included; the marketing view assigns regional partner flying
  to the brand, which differs from DOT's operating-carrier rankings.
- **Aggregated layer.** The app ships aggregated tables (the lowest grain is day × carrier × origin), not individual
  flight records. The sync script rebuilds everything from the BTS source files.
- **Signals are statistical screens.** They flag material, unusual changes against trailing baselines; they do not
  explain root cause.
""")
