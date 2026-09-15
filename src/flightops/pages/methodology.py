"""Methodology & Data: definitions, lineage, coverage and data-quality checks."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from flightops.config import (
    MIN_AIRPORT_DEPARTURES_PER_MONTH,
    MIN_CARRIER_FLIGHTS_PER_MONTH,
    MIN_ROUTE_FLIGHTS_PER_MONTH,
    SEVERE_DELAY_THRESHOLD_MIN,
)
from flightops.data.measures import MEASURES
from flightops.data.months import format_month, month_key, month_range, parse_month
from flightops.metrics import METRICS
from flightops.ui import components as ui
from flightops.ui import data
from flightops.utils.format import fmt_int

LINEAGE = [
    ("U.S. DOT / BTS TranStats", "Marketing Carrier On-Time Performance, monthly PREZIP archives"),
    ("Monthly flight files", "~600–700K rows × 120 columns per month, cached locally (gitignored)"),
    ("Validation + transformation", "Schema contract, typed staging in DuckDB, duplicate/out-of-month exclusion, DQ report"),
    ("Parquet analytical layer", "4 fact tables of additive measures, one partition per month, + airport and carrier dimensions"),
    ("DuckDB metric layer", "Filtered aggregation → governed KPI definitions → comparisons, benchmarks, signals"),
    ("Streamlit", "Cached queries, deterministic narrative, interactive views"),
]


def render() -> None:
    meta = data.metadata()
    ui.page_header("Methodology & Data", "How every number is produced",
                   "Source, coverage, metric definitions, aggregation design, limitations and data-quality results.",
                   show_chips=False)
    reports = data.month_reports()
    loaded = meta.get("months_loaded", [])
    end = parse_month(meta["coverage_end"]) if meta.get("coverage_end") else None
    lag = None
    if end:
        today = dt.date.today()
        lag = (today.year - end.year) * 12 + today.month - end.month
    ui.kpi_grid([
        ui.text_card("Coverage", f"{len(loaded)} months",
                     f"{format_month(parse_month(meta['coverage_start']), 'short')} – {format_month(end, 'short')}"
                     if end else "—"),
        ui.text_card("Flights analyzed", fmt_int(meta.get("flights_loaded")), f"{fmt_int(meta.get('raw_rows_processed'))} raw rows processed"),
        ui.text_card("Reporting lag", f"{lag} months" if lag is not None else "—",
                     f"Latest BTS month published: {meta.get('latest_published_month', '—')}"),
        ui.text_card("Last refresh", (meta.get("generated_at") or "—")[:10], "UTC, by scripts/sync_bts.py"),
        ui.text_card("Analytical layer", f"{sum(meta.get('table_bytes', {}).values()) / 1e6:.1f} MB",
                     f"{fmt_int(sum(meta.get('table_rows', {}).values()))} aggregate rows"),
    ])

    tabs = st.tabs(["Source & scope", "Metric definitions", "Aggregation & lineage", "Signals & benchmarks",
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
        _quality(meta, reports)
    with tabs[5]:
        _limitations()


def _source(meta: dict) -> None:
    st.markdown(
        f"""
**Source.** {meta.get('source', 'U.S. DOT Bureau of Transportation Statistics')}: *{meta.get('dataset', '')}*, published
on TranStats and downloaded directly from the official PREZIP directory, one ZIP per reporting month. Airport names and
coordinates come from the BTS Aviation Support Tables *Master Coordinate* table, using the latest record per airport.

**Why the marketing-carrier dataset.** Records are reported by the *marketing* (branded) network, so regional flying
sold as American Eagle, Delta Connection or United Express rolls up to AA, DL or UA. That matches how customers and
executives think about an airline. Where BTS marks a record as a duplicate code-share report (`Duplicate = Y`), it is
excluded so a flight is never counted twice.

**Reporting scope.** BTS on-time reporting covers scheduled domestic passenger flights of U.S. carriers above the DOT
reporting threshold (0.5% of domestic scheduled-passenger revenue) and carriers reporting voluntarily. Smaller airlines,
charters and international segments are outside the population. "The network" in this app means this BTS reporting
population, not all U.S. aviation.

**Freshness.** BTS publishes each month roughly two to three months after it ends. This is historical operational
reporting, not live flight status. The newest loaded month is **{meta.get('coverage_end', '—')}**.
"""
    )
    carriers = data.carriers()
    seen = set()
    for r in data.month_reports()["carriers"]:
        seen |= set(r)
    st.dataframe(carriers[carriers["carrier"].isin(seen)].rename(columns={
        "carrier": "Code", "carrier_name": "Marketing carrier", "carrier_short": "Short name"}),
        hide_index=True, width="content")


def _definitions() -> None:
    st.markdown(
        f"""
**Populations.** *Scheduled flights* are all non-duplicate records. *Completed arrivals* are flights that were neither
cancelled nor diverted (BTS populates `ArrDel15` only for these). Cancelled flights are never counted as arrival delays.
Diverted flights appear only in the diversion rate and in scheduled volume.

**On-time (DOT convention).** A flight is on time if it arrives less than 15 minutes after its scheduled arrival time.
**Severe delay** uses a {SEVERE_DELAY_THRESHOLD_MIN}+ minute arrival threshold.

**Two kinds of average delay.** *Schedule variance* averages signed `ArrDelay` (early arrivals are negative and offset
late ones). *Positive delay* averages `ArrDelayMinutes`, where early arrivals count as 0. The app leads with positive
delay and labels both explicitly.

**Metric direction.** Each metric declares whether higher is better, lower is better or neutral. Green/red deltas, the
▲/▼ arrows and signal direction all derive from that declaration. Rate changes are shown in percentage points (pts),
minute averages in minutes, and counts as relative %.
"""
    )
    rows = [{"Metric": m.label, "Definition": m.definition, "Calculation": m.calculation,
             "Unit": m.unit.value, "Better when": m.direction.value} for m in METRICS.values()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=560)
    st.markdown(
        """
**Operational Reliability Score** = on-time arrivals ÷ scheduled flights × 100. One transparent number that
penalizes cancellations and diversions as well as delays. An airline cannot raise it by cancelling late-running flights,
which *can* raise the on-time rate.

**Delay causes.** BTS asks carriers to attribute arrival delay minutes (15+ minute delays only) to five causes:
*Carrier* (within the airline's control: maintenance, crew, cleaning, fueling), *Extreme Weather* (significant
meteorological conditions), *NAS* (National Aviation System: non-extreme weather, airport operations, heavy traffic,
ATC), *Security* and *Late-arriving aircraft* (a previous flight with the same aircraft arrived late). The five causes
sum to the arrival delay minutes. Cause share = cause minutes ÷ total reported cause minutes.
"""
    )


def _lineage(meta: dict) -> None:
    left, right = st.columns([1, 1.3], gap="large")
    with left:
        steps = []
        for i, (title, detail) in enumerate(LINEAGE):
            steps.append(f'<div class="step">{ui.esc(title)}<small>{ui.esc(detail)}</small></div>')
            if i < len(LINEAGE) - 1:
                steps.append('<div class="arrow"></div>')
        st.markdown('<div class="fo-lineage">' + "".join(steps) + "</div>", unsafe_allow_html=True)
    with right:
        st.markdown(
            """
**Additive facts only.** Fact tables hold counts and minute sums, never ratios. Every KPI is derived after
aggregation, so a quarter, a carrier at an airport or a single route re-derives rates correctly instead of averaging
averages.

**Grains.** Each table is the smallest grain its questions need, which keeps the committed layer small:
"""
        )
        grains = pd.DataFrame([
            {"Table": "fact_origin_daily", "Grain": "day × marketing carrier × origin", "Answers": "daily trends, day of week"},
            {"Table": "fact_route_monthly", "Grain": "month × carrier × origin × destination", "Answers": "KPIs, routes, destination filters, block time"},
            {"Table": "fact_origin_hourly", "Grain": "month × carrier × origin × scheduled departure hour", "Answers": "operating-day heatmaps, taxi-out"},
            {"Table": "fact_route_profile", "Grain": "month × origin × destination × (hour | day of week)", "Answers": "route operating profile"},
        ])
        grains["Rows"] = grains["Table"].map(lambda tname: fmt_int(meta.get("table_rows", {}).get(tname)))
        grains["Size"] = grains["Table"].map(lambda tname: f"{meta.get('table_bytes', {}).get(tname, 0) / 1e6:.1f} MB")
        st.dataframe(grains, hide_index=True, width="stretch")
        st.markdown(
            """
**Idempotent refresh.** Each month is written as its own Parquet partition per table. Re-running a month replaces
exactly that month; months outside the configured window are pruned. After every sync, flight counts are reconciled
across all four fact tables and against the month's data-quality report, and the sync fails if they disagree.

**Periods.** BTS publishes monthly, so analytical periods are whole months. Comparisons use the immediately preceding
period of equal length and the same months one year earlier, when loaded.
"""
        )
        with st.expander("Stored measures"):
            st.dataframe(pd.DataFrame([{"Measure": m.name, "Definition": m.description} for m in MEASURES]),
                         hide_index=True, width="stretch")


def _benchmarks() -> None:
    st.markdown(
        f"""
**Minimum-volume rules.** Rankings only include airports with ≥{MIN_AIRPORT_DEPARTURES_PER_MONTH:,} departures per month,
carriers with ≥{MIN_CARRIER_FLIGHTS_PER_MONTH:,} flights per month and routes with ≥{MIN_ROUTE_FLIGHTS_PER_MONTH} flights
per month (about three a day), all scaled by the number of months in the period.

**Airport peer groups.** Airports are tiered with FAA hub cut points (≥1%, ≥0.25%, ≥0.05%) applied to share of scheduled
departures in the selected scope. Percentiles compare an airport only with its own tier.

**Route peer groups.** Directional routes in the same distance band (<500, 500–999, 1,000–1,499, 1,500–2,499, 2,500+ miles)
that clear the route volume floor. Percentile = share of peers with a lower on-time rate.

**Hotspots.** *Excess delayed arrivals* = delayed arrivals − completed arrivals × the reference 15+ delay rate. It ranks where
volume and poor reliability intersect: a large airport slightly worse than average can matter more than a small, very
poor one.

**Delay Propagation Index.** Late-aircraft minutes ÷ total reported delay minutes. It measures how much of a station's
reported delay is associated with inbound aircraft arriving late. It does not reconstruct aircraft rotations from tail
numbers.

**Signals** are documented in full on the Signals page, under "How signals are detected".

**Narrative.** The Latest Operations Brief is produced by deterministic rules from a structured facts object. An optional
LLM narrator (`FLIGHTOPS_BRIEF_PROVIDER=anthropic`) receives only that facts object, and its output is discarded if it
contains any figure not present in the facts.
"""
    )


def _quality(meta: dict, reports: pd.DataFrame) -> None:
    if reports.empty:
        ui.empty_state("No data-quality reports", "Run the sync script.")
        return
    months = month_range(parse_month(meta["coverage_start"]), parse_month(meta["coverage_end"]))
    missing = [month_key(m) for m in months if month_key(m) not in set(reports["month"])]
    unexpected = {k: v for r in reports["unexpected_carriers"] for k, v in r.items()}
    new_cols = sorted({c for r in reports["extra_columns"] for c in r})
    cause_rate = reports["cause_reconciled_rows"].sum() / max(reports["cause_rows"].sum(), 1)
    checks = pd.DataFrame([
        {"Check": "Months loaded", "Result": f"{len(reports)} of {len(months)} in window", "Status": "Pass" if not missing else "Warn"},
        {"Check": "Missing months", "Result": ", ".join(missing) or "None", "Status": "Pass" if not missing else "Warn"},
        {"Check": "Latest available period", "Result": meta.get("latest_published_month", "—"), "Status": "Info"},
        {"Check": "Raw rows processed", "Result": fmt_int(reports["raw_rows"].sum()), "Status": "Info"},
        {"Check": "Duplicate code-share rows excluded", "Result": fmt_int(reports["duplicate_rows"].sum()), "Status": "Info"},
        {"Check": "Invalid or out-of-month dates", "Result": fmt_int(reports["invalid_date_rows"].sum() + reports["out_of_month_rows"].sum()),
         "Status": "Pass" if reports["invalid_date_rows"].sum() == 0 else "Warn"},
        {"Check": "Rows missing airport/carrier keys", "Result": fmt_int(reports["invalid_airport_rows"].sum()),
         "Status": "Pass" if reports["invalid_airport_rows"].sum() == 0 else "Warn"},
        {"Check": "Unexpected carrier codes", "Result": ", ".join(f"{k} ({v:,})" for k, v in unexpected.items()) or "None",
         "Status": "Pass" if not unexpected else "Warn"},
        {"Check": "New source columns", "Result": ", ".join(new_cols) or "None", "Status": "Pass" if not new_cols else "Info"},
        {"Check": "Completed flights missing ArrDel15", "Result": fmt_int(reports["null_arr_flag_completed"].sum()),
         "Status": "Pass" if reports["null_arr_flag_completed"].sum() == 0 else "Warn"},
        {"Check": "Departed flights missing TaxiOut", "Result": fmt_int(reports["null_taxi_out_departed"].sum()), "Status": "Info"},
        {"Check": "Cancellations without a reason code", "Result": fmt_int(reports["cancelled_without_code"].sum()),
         "Status": "Pass" if reports["cancelled_without_code"].sum() == 0 else "Warn"},
        {"Check": "Delay causes reconcile to arrival delay (±1 min)", "Result": f"{cause_rate:.3%} of delayed flights",
         "Status": "Pass" if cause_rate > 0.99 else "Warn"},
        {"Check": "Fact tables reconcile to loaded rows", "Result": "Verified at every sync (sync fails otherwise)", "Status": "Pass"},
        {"Check": "Airports without coordinates", "Result": str(meta.get("dimensions", {}).get("dim_airport_missing_coordinates", "—")),
         "Status": "Pass" if not meta.get("dimensions", {}).get("dim_airport_missing_coordinates") else "Warn"},
    ])
    st.dataframe(checks, hide_index=True, width="stretch")
    per_month = pd.DataFrame({
        "Month": reports["month"],
        "Raw rows": reports["raw_rows"],
        "Flights loaded": reports["loaded_rows"],
        "Cancelled": reports["cancelled"],
        "Diverted": reports["diverted"],
        "Carriers": reports["carriers"].map(len),
        "Null TaxiOut (departed)": reports["null_taxi_out_departed"],
        "Cause reconciliation": reports["cause_reconciled_rows"] / reports["cause_rows"].clip(lower=1) * 100,
        "Processed": reports["processed_at"].str[:10],
    }).sort_values("Month", ascending=False)
    st.dataframe(per_month, hide_index=True, width="stretch", column_config={
        "Raw rows": st.column_config.NumberColumn(format="localized"),
        "Flights loaded": st.column_config.NumberColumn(format="localized"),
        "Cancelled": st.column_config.NumberColumn(format="localized"),
        "Diverted": st.column_config.NumberColumn(format="localized"),
        "Cause reconciliation": st.column_config.NumberColumn(format="%.2f%%"),
    })


def _limitations() -> None:
    st.markdown(
        """
- **Not real time.** Monthly BTS reporting lags by roughly two to three months.
- **Reported, not causal, delay causes.** Carriers assign cause minutes under DOT guidance; attribution practices can
  differ between carriers. Treat cause mix as reported attribution.
- **Weather is understated by the Weather cause.** Non-extreme weather effects are reported under NAS.
- **Propagation is associated, not traced.** The Delay Propagation Index uses the reported late-aircraft cause. It
  does not follow individual aircraft through the day.
- **Departure-hour attribution.** Hourly views use the *scheduled* local departure hour at the origin airport.
- **Reporting population.** Only reporting carriers are included; the marketing view assigns regional partner flying to
  the brand, which differs from operating-carrier (DOT Air Travel Consumer Report) rankings.
- **Month-granular periods.** Date filtering is by whole reporting months; the daily fact table supports day-of-week views.
- **Signals are statistical screens.** They flag material, unusual changes against trailing baselines. They do not
  explain root cause.
"""
    )
