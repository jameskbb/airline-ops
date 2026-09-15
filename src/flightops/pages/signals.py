"""Signals: material, unusual operational changes surfaced automatically."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from flightops.analytics import signals as sig
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current


def _in_scope(signal: sig.Signal, f) -> bool:
    """Narrow network-wide signals to entities matching the active filters."""
    if f.carrier and not (signal.entity_type == "network" or signal.entity == f.carrier):
        return False
    if f.origin and signal.entity_type == "airport" and signal.entity != f.origin:
        return False
    if f.origin and signal.entity_type == "route" and not signal.entity.startswith(f"{f.origin}→"):
        return False
    return not (f.dest and signal.entity_type == "route" and not signal.entity.endswith(f"→{f.dest}"))


def render() -> None:
    f = current()
    p = f.period
    ui.page_header("Signals",
                   f"Material, unusual changes in {p.label} against each entity's trailing {sig.BASELINE_MONTHS}-month "
                   "baseline, with minimum-volume floors. Ranked by severity, then estimated impact.", f)
    if not data.in_range(p.trailing(sig.BASELINE_MONTHS)):
        ui.empty_state("Not enough history", f"Signals need {sig.BASELINE_MONTHS} loaded months before the period.")
        return
    signals = [s for s in data.signals_for(p) if _in_scope(s, f)]
    ui.section("Signal feed", "Signals are computed network-wide, then narrowed to entities matching the active "
                              "carrier and airport filters.",
               {name.capitalize(): q for name, q in data.signal_queries(p).items()})

    counts = pd.Series([s.severity for s in signals], dtype=object).value_counts()
    cols = st.columns(4)
    for col, (label, value, help_text) in zip(cols, [
        ("High impact", counts.get("High impact", 0), "≥1,000 affected flights per month and |z| ≥ 2.5."),
        ("Elevated", counts.get("Elevated", 0), "≥300 affected flights per month and |z| ≥ 2."),
        ("Watch", counts.get("Watch", 0), "Material and unusual, at smaller scale."),
        ("Improvements", sum(s.direction == "improvement" for s in signals), "Favorable shifts, included above."),
    ], strict=True):
        with col:
            ui.stat_card(label, str(value), help=help_text)

    c1, c2, c3 = st.columns([1.2, 1.6, 1])
    severities = c1.multiselect("Severity", list(sig.SEVERITY_ORDER), default=list(sig.SEVERITY_ORDER), key="sig_sev")
    categories = sorted({s.category for s in signals})
    chosen = c2.multiselect("Signal type", categories, default=categories, key="sig_cat")
    entity = c3.segmented_control("Entity", ["All", "airport", "carrier", "route", "network"], default="All",
                                  key="sig_entity", format_func=str.title)
    shown = [s for s in signals if s.severity in severities and s.category in chosen
             and entity in (None, "All", s.entity_type)]
    if not shown:
        ui.empty_state("No signals match", "Nothing in this scope cleared the materiality, variability and volume "
                                           "tests, which means operations stayed within normal ranges.")
    else:
        left, right = st.columns(2, gap="medium")
        for i, signal in enumerate(shown):
            with left if i % 2 == 0 else right:
                ui.signal_card(signal)

    with st.expander("How signals are detected"):
        st.dataframe(pd.DataFrame([
            {"Rule": name, "Metric": r.metric, "Min change": r.min_delta, "Variability floor": r.min_sigma,
             "z threshold": r.z_threshold, "Network-relative": r.relative, "Reports improvements": r.improvements}
            for name, r in sig.RULES.items()]), hide_index=True, width="stretch")
        st.markdown(f"""
- **Comparison.** The selected period is compared with the trailing {sig.BASELINE_MONTHS} months. Network-relative
  rules compare the entity's *gap to the network* with its baseline gap, so system-wide weather months do not flag
  every airport.
- **Materiality.** The shift must exceed the minimum change (points for rates, minutes for averages), and the absolute
  move must be at least {sig.MIN_ABSOLUTE_SHARE:.0%} of it.
- **Unusualness.** z = shift ÷ the entity's month-to-month standard deviation of that gap over the prior
  {sig.HISTORY_MONTHS} months, floored to avoid tiny-variance blowups.
- **Volume floors.** Airports ≥{sig.MIN_VOLUME['airport']:,}, carriers ≥{sig.MIN_VOLUME['carrier']:,} and routes
  ≥{sig.MIN_VOLUME['route']:,} flights per month, in both the current and baseline windows.
- **Impact** is in affected flights: additional delayed arrivals, cancellations or severe delays. Taxi-out impact is
  converted to equivalent 15-minute delays.
- **Other detectors.** Delay concentration (delay-minute share ≥{sig.CONCENTRATION_MIN_RATIO}× flight share and ≥1% of
  network delay), delay-cause mix shifts (≥{sig.CAUSE_SHIFT_MIN_PTS:.0f} share points) and year-over-year improvement
  streaks (≥3 consecutive months).
- Each (entity type, signal type) is capped so one noisy category cannot crowd the feed. The input queries are listed
  under the "?" of the signal feed and in the Data Explorer.
""")
