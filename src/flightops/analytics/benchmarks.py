"""Peer groups, percentile ranks, hotspots and relative strengths.

Every ranking here applies a minimum-volume rule first, so a 40-flight route can
never outrank a trunk route on a lucky month.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from flightops.metrics.definitions import METRICS, Direction, safe_div

# FAA hub classes use each airport's share of U.S. enplanements (1%, 0.25%, 0.05%).
# We apply the same cut points to share of scheduled departures in the BTS population.
HUB_TIERS = ((0.01, "Large hub"), (0.0025, "Medium hub"), (0.0005, "Small hub"))

DISTANCE_BANDS = ((500, "Under 500 mi"), (1000, "500–999 mi"), (1500, "1,000–1,499 mi"),
                  (2500, "1,500–2,499 mi"), (float("inf"), "2,500+ mi"))


def hub_tier(departure_share: float) -> str:
    for cut, label in HUB_TIERS:
        if departure_share >= cut:
            return label
    return "Non-hub"


def distance_band(miles: float) -> str:
    if miles is None or (isinstance(miles, float) and np.isnan(miles)):
        return "Unknown"
    for cut, label in DISTANCE_BANDS:
        if miles < cut:
            return label
    return DISTANCE_BANDS[-1][1]


def apply_min_volume(df: pd.DataFrame, min_per_month: int, months: int, column: str = "flights") -> pd.DataFrame:
    """Keep rows with at least ``min_per_month`` × ``months`` volume."""
    return df[df[column] >= min_per_month * months].copy()


def percentile(df: pd.DataFrame, metric_key: str) -> pd.Series:
    """Direction-aware percentile (0–100): share of peers this row outperforms.

    100 means best in the peer group, 0 means worst. Ties share the average rank.
    Requires at least two peers; otherwise NaN.
    """
    values = df[metric_key]
    if values.notna().sum() < 2:
        return pd.Series(np.nan, index=df.index)
    higher = METRICS[metric_key].direction is not Direction.LOWER_IS_BETTER
    ranks = values.rank(method="average", ascending=higher)
    return (ranks - 1) / (values.notna().sum() - 1) * 100


def with_shares(df: pd.DataFrame, total_flights: float, total_delay_minutes: float) -> pd.DataFrame:
    out = df.copy()
    out["flight_share"] = safe_div(out["flights"], total_flights)
    out["delay_share"] = safe_div(out["delay_minutes"], total_delay_minutes)
    out["delay_share_ratio"] = safe_div(out["delay_share"], out["flight_share"])
    return out


def excess_delayed_arrivals(df: pd.DataFrame, reference_rate: float) -> pd.Series:
    """Delayed arrivals above what the reference 15+ delay rate would imply.

    Positive values mean more delayed flights than a network-average operation of
    the same size; this is the volume × reliability intersection used for hotspots.
    """
    return df["arr_del15"] - df["arr_eligible"] * reference_rate


def hotspots(
    df: pd.DataFrame, network: dict, months: int, min_per_month: int, top: int = 10
) -> pd.DataFrame:
    """Rank entities where volume and poor reliability intersect."""
    if df.empty or not network:
        return df.iloc[0:0]
    eligible = apply_min_volume(df, min_per_month, months)
    eligible = with_shares(eligible, network["flights"], network["delay_minutes"])
    eligible["excess_delayed"] = excess_delayed_arrivals(eligible, network["delay15_rate"])
    ranked = eligible[eligible["excess_delayed"] > 0].sort_values("excess_delayed", ascending=False)
    return ranked.head(top)


STRENGTH_METRICS = (
    "on_time_rate", "cancellation_rate", "severe_delay_rate", "avg_arr_delay",
    "delay_min_per_100", "avg_taxi_out", "diversion_rate",
)


def relative_strengths(df: pd.DataFrame, entity: str, metrics: tuple[str, ...] = STRENGTH_METRICS) -> pd.DataFrame:
    """Long table of each entity's gap to the peer median, oriented so positive = better.

    ``gap`` is in display units (points for rates, minutes for averages);
    ``standardized`` divides by the peer spread (MAD-based) to compare dimensions.
    """
    rows = []
    for key in metrics:
        if key not in df:
            continue
        metric = METRICS[key]
        values = df[key].astype(float)
        median = values.median()
        spread = (values - median).abs().median() * 1.4826 or values.std() or 1.0
        sign = -1 if metric.direction is Direction.LOWER_IS_BETTER else 1
        scale = 100 if metric.unit.value == "rate" else 1
        for _, row in df.iterrows():
            gap = (row[key] - median) * scale
            rows.append({
                entity: row[entity],
                "metric": key,
                "label": metric.short,
                "value": row[key],
                "peer_median": median,
                "gap": gap,
                "better_gap": sign * gap,
                "standardized": sign * (row[key] - median) / spread if spread else 0.0,
            })
    return pd.DataFrame(rows)
