"""Structured facts: the only input the narrative layer (deterministic or LLM) sees.

Numbers are computed here by the metric layer. A narrator may choose, order and
phrase facts; it never calculates them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from flightops.data.measures import CAUSE_LABELS
from flightops.metrics.compare import compare
from flightops.metrics.definitions import METRICS, cause_mix
from flightops.metrics.periods import Period


def _clean(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


@dataclass
class BriefFacts:
    period: str
    period_label: str
    scope: str
    network: dict
    comparisons: dict = field(default_factory=dict)
    largest_cause: dict | None = None
    hotspot: dict | None = None
    carrier_mover: dict | None = None
    top_signal: dict | None = None

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "period_label": self.period_label,
            "scope": self.scope,
            "network": {k: _clean(v) for k, v in self.network.items()},
            "comparisons": self.comparisons,
            "largest_cause": self.largest_cause,
            "hotspot": self.hotspot,
            "carrier_mover": self.carrier_mover,
            "top_signal": self.top_signal,
        }


KEY_METRICS = ("on_time_rate", "cancellation_rate", "severe_delay_rate", "avg_arr_delay", "flights", "delay_minutes")


def build_brief_facts(
    period: Period,
    scope: str,
    current: dict,
    previous: dict,
    prior_year: dict,
    airports: pd.DataFrame,
    carriers_current: pd.DataFrame,
    carriers_trailing: pd.DataFrame,
    min_airport_flights: int,
    min_carrier_flights: int,
    top_signal=None,
    airport_names: dict | None = None,
    carrier_names: dict | None = None,
) -> BriefFacts:
    airport_names = airport_names or {}
    carrier_names = carrier_names or {}
    network = {k: current.get(k) for k in (*KEY_METRICS, "arr_eligible", "arr_del15", "reliability_score")}
    facts = BriefFacts(period=period.key, period_label=period.label, scope=scope, network=network)

    for key in KEY_METRICS:
        metric = METRICS[key]
        entry = {}
        for name, ref in (("vs_prior", previous), ("vs_prior_year", prior_year)):
            delta = compare(metric, current.get(key), ref.get(key)) if ref else None
            if delta:
                entry[name] = {"change": round(delta.change, 4), "kind": delta.kind, "text": delta.text,
                               "favorable": delta.favorable, "reference": _clean(delta.reference)}
        facts.comparisons[key] = entry

    mix = cause_mix(current) if current.get("delay_minutes") else {}
    if mix:
        cause, share = max(mix.items(), key=lambda kv: (kv[1] if not math.isnan(kv[1]) else -1))
        facts.largest_cause = {"cause": cause, "label": CAUSE_LABELS[cause], "share": round(share, 4)}

    if not airports.empty and current.get("delay_minutes"):
        df = airports[airports["flights"] >= min_airport_flights].copy()
        df["flight_share"] = df["flights"] / current["flights"]
        df["delay_share"] = df["delay_minutes"] / current["delay_minutes"]
        df["excess"] = df["delay_share"] - df["flight_share"]
        if not df.empty:
            top = df.sort_values("excess", ascending=False).iloc[0]
            if top["excess"] > 0:
                facts.hotspot = {
                    "airport": top["origin"],
                    "name": airport_names.get(top["origin"], top["origin"]),
                    "delay_share": round(float(top["delay_share"]), 4),
                    "flight_share": round(float(top["flight_share"]), 4),
                    "on_time_rate": round(float(top["on_time_rate"]), 4),
                }

    if not carriers_current.empty and not carriers_trailing.empty:
        merged = carriers_current.merge(carriers_trailing, on="carrier", suffixes=("", "_trail"))
        merged = merged[(merged["flights"] >= min_carrier_flights)
                        & (merged["flights_trail"] >= min_carrier_flights * 3)]
        if not merged.empty:
            merged["change_pts"] = (merged["on_time_rate"] - merged["on_time_rate_trail"]) * 100
            mover = merged.loc[merged["change_pts"].abs().idxmax()]
            facts.carrier_mover = {
                "carrier": mover["carrier"],
                "name": carrier_names.get(mover["carrier"], mover["carrier"]),
                "on_time_rate": round(float(mover["on_time_rate"]), 4),
                "change_pts_vs_trailing_3m": round(float(mover["change_pts"]), 2),
            }

    if top_signal is not None:
        facts.top_signal = {"headline": top_signal.headline, "detail": top_signal.detail,
                            "severity": top_signal.severity}
    return facts
