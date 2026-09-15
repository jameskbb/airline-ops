"""Signals: material, unusual operational changes surfaced automatically."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from flightops.analytics import signals as sig
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current


def _in_scope(signal: sig.Signal, f) -> bool:
    if f.carrier and signal.entity_type == "carrier" and signal.entity != f.carrier:
        return False
    if f.carrier and signal.entity_type not in ("carrier", "network"):
        return False
    if f.origin:
        if signal.entity_type == "airport" and signal.entity != f.origin:
            return False
        if signal.entity_type == "route" and not signal.entity.startswith(f"{f.origin}→"):
            return False
    return not (f.dest and signal.entity_type == "route" and not signal.entity.endswith(f"→{f.dest}"))


def render() -> None:
    f = current()
    p = f.period
    ui.page_header("Signals", "What changed that matters",
                   f"Deterministic detection over {p.label} vs each entity's trailing {sig.BASELINE_MONTHS}-month "
                   "baseline, network-relative where noted, with minimum-volume floors. Ranked by severity, then impact.", f)
    if not data.in_range(p.trailing(sig.BASELINE_MONTHS)):
        ui.empty_state("Not enough history", f"Signals need {sig.BASELINE_MONTHS} months of history before the period.")
        return
    signals = data.signals_for(p.start, p.end)
    scoped = [s for s in signals if _in_scope(s, f)]
    if f.any_dimension:
        ui.note("Signals are computed network-wide, then narrowed to entities matching the active carrier/airport filters.")

    counts = pd.Series([s.severity for s in scoped]).value_counts()
    improvements = sum(1 for s in scoped if s.direction == "improvement")
    ui.kpi_grid([
        ui.text_card("High impact", str(counts.get("High impact", 0)), "≥1,000 affected flights/month and |z| ≥ 2.5"),
        ui.text_card("Elevated", str(counts.get("Elevated", 0)), "≥300 affected flights/month and |z| ≥ 2"),
        ui.text_card("Watch", str(counts.get("Watch", 0)), "Material and unusual, smaller scale"),
        ui.text_card("Improvements", str(improvements), "Favorable shifts included in the counts"),
    ])

    c1, c2, c3 = st.columns([1.2, 1.6, 1])
    with c1:
        severities = st.multiselect("Severity", list(sig.SEVERITY_ORDER), default=list(sig.SEVERITY_ORDER), key="sig_sev")
    with c2:
        categories = sorted({s.category for s in scoped})
        chosen = st.multiselect("Signal type", categories, default=categories, key="sig_cat")
    with c3:
        entity = st.segmented_control("Entity", ["All", "airport", "carrier", "route", "network"], default="All",
                                      key="sig_entity", format_func=lambda e: e.title())
    shown = [s for s in scoped if s.severity in severities and s.category in chosen
             and (entity in (None, "All") or s.entity_type == entity)]
    if not shown:
        ui.empty_state("No signals match", "Nothing in this scope cleared the materiality, variability and volume tests, "
                                           "which is itself a finding: operations were within normal ranges.")
    else:
        left, right = st.columns(2, gap="medium")
        for i, signal in enumerate(shown):
            with left if i % 2 == 0 else right:
                st.markdown(ui.signal_card(signal), unsafe_allow_html=True)

    with st.expander("How signals are detected"):
        rules = pd.DataFrame([
            {"Rule": name, "Metric": r.metric, "Min change": r.min_delta, "Variability floor": r.min_sigma,
             "z threshold": r.z_threshold, "Network-relative": r.relative, "Reports improvements": r.improvements}
            for name, r in sig.RULES.items()
        ])
        st.dataframe(rules, hide_index=True, width="stretch")
        st.markdown(
            f"""
- **Comparison.** The selected period is compared with the trailing {sig.BASELINE_MONTHS} months. Network-relative rules
  compare the entity's *gap to the network* with its baseline gap, so system-wide weather months do not flag every airport.
- **Materiality.** The shift must exceed the minimum change (points for rates, minutes for averages), and the absolute
  move must be at least {sig.MIN_ABSOLUTE_SHARE:.0%} of it.
- **Unusualness.** z = shift ÷ the entity's month-to-month standard deviation of that gap over the prior
  {sig.HISTORY_MONTHS} months (floored to avoid tiny-variance blowups).
- **Volume floors.** Airports ≥{sig.MIN_VOLUME['airport']:,}, carriers ≥{sig.MIN_VOLUME['carrier']:,}, routes
  ≥{sig.MIN_VOLUME['route']:,} flights per month, in both current and baseline windows.
- **Impact** is expressed in affected flights: additional delayed arrivals, cancellations or severe delays; taxi-out
  impact is converted to equivalent 15-minute delays.
- **Other detectors.** Delay concentration (delay-minute share ≥{sig.CONCENTRATION_MIN_RATIO}× flight share, ≥1% of
  network delay), delay-cause mix shifts (≥{sig.CAUSE_SHIFT_MIN_PTS:.0f} share points) and year-over-year improvement streaks
  (≥3 consecutive months).
- Each (entity type, signal type) is capped so one noisy category cannot crowd the feed.
"""
        )
