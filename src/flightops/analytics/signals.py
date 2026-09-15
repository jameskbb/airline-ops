"""Deterministic operational signal detection.

Signals compare the selected period with a trailing baseline and only fire when a
change is both *material* (a minimum absolute change) and *unusual* (a z-score
against the entity's own month-to-month variability), for entities that clear a
minimum-volume floor.

Most signals are **network-relative**: an entity's gap to the network is compared
with its baseline gap. A summer thunderstorm month that lowers every airport's
on-time rate therefore does not flag every airport; only airports that moved more
than the network do. Absolute values are still reported for context.

Impact is expressed in *affected flights* so different signal types rank
together (for example, additional delayed arrivals or additional cancellations).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from flightops.data.measures import CAUSE_LABELS, CAUSE_MEASURES, MEASURE_NAMES
from flightops.metrics.definitions import METRICS, Direction, Unit, add_metrics, safe_div
from flightops.metrics.periods import Period
from flightops.utils.format import fmt_int, fmt_pct

SEVERITY_ORDER = {"High impact": 0, "Elevated": 1, "Watch": 2}


@dataclass(frozen=True)
class ShiftRule:
    metric: str
    min_delta: float  # display units: points for rates, minutes for averages
    min_sigma: float  # variability floor (display units) to stop tiny-variance blowups
    z_threshold: float = 2.0
    relative: bool = True
    min_ratio: float | None = None  # current/baseline ratio also required (e.g. cancellations)
    improvements: bool = True  # also report favorable shifts


RULES: dict[str, ShiftRule] = {
    "on_time": ShiftRule("on_time_rate", min_delta=4.0, min_sigma=1.5),
    "cancellation": ShiftRule("cancellation_rate", min_delta=1.0, min_sigma=0.5, min_ratio=1.75, improvements=False),
    "severe": ShiftRule("severe_delay_rate", min_delta=1.5, min_sigma=0.6, improvements=False),
    "taxi_out": ShiftRule("avg_taxi_out", min_delta=2.0, min_sigma=0.8, improvements=False),
}

# Absolute movement must be at least this share of ``min_delta`` too, so an entity
# that merely held steady while the network fell is not labeled an "improvement".
MIN_ABSOLUTE_SHARE = 0.5

MIN_VOLUME = {"airport": 1_000, "carrier": 3_000, "route": 300}

# Maximum signals kept per (entity type, signal kind) so one noisy category cannot crowd the feed.
CAPS = {"airport": 8, "carrier": 6, "route": 6, "network": 3}
BASELINE_MONTHS = 3
HISTORY_MONTHS = 12
CAUSE_SHIFT_MIN_PTS = 6.0
CONCENTRATION_MIN_RATIO = 1.5
CONCENTRATION_MIN_SHARE = 0.01


@dataclass
class Signal:
    kind: str
    category: str
    entity_type: str
    entity: str
    entity_label: str
    direction: str  # "deterioration" | "improvement" | "shift"
    headline: str
    detail: str
    comparison: str
    why: str
    metric: str
    current: float
    baseline: float
    delta: float
    z: float
    impact: float
    volume: float
    severity: str = "Watch"
    extras: dict = field(default_factory=dict)

    @property
    def sort_key(self) -> tuple:
        return (SEVERITY_ORDER[self.severity], -self.impact)


def _scale(metric_key: str) -> float:
    return 100.0 if METRICS[metric_key].unit is Unit.RATE else 1.0


def _unit_text(metric_key: str) -> str:
    return "pts" if METRICS[metric_key].unit is Unit.RATE else "min"


def _severity(impact: float, z: float, months: int) -> str:
    """Severity needs both scale (affected flights per month) and unusualness (|z|)."""
    per_month = impact / max(months, 1)
    if per_month >= 1_000 and abs(z) >= 2.5:
        return "High impact"
    if per_month >= 300 and abs(z) >= 2.0:
        return "Elevated"
    return "Watch"


def _sum_by(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    measures = [c for c in df.columns if c in MEASURE_NAMES and c not in keys]
    return add_metrics(df.groupby(keys, as_index=False)[measures].sum())


def _network_totals(network_monthly: pd.DataFrame, period: Period) -> pd.Series:
    window = _window(network_monthly, period)
    measures = [c for c in window.columns if c in MEASURE_NAMES]
    return add_metrics(window[measures].sum().to_frame().T).iloc[0]


def _window(monthly: pd.DataFrame, period: Period) -> pd.DataFrame:
    months = pd.to_datetime(monthly["month"]).dt.date
    return monthly[(months >= period.start) & (months <= period.end)]


def detect_shifts(
    monthly: pd.DataFrame,
    network_monthly: pd.DataFrame,
    entity_col: str,
    entity_type: str,
    period: Period,
    rule_name: str,
    label: Callable[[str], str] = str,
) -> list[Signal]:
    """Detect material, unusual shifts in one metric for every entity.

    ``monthly`` holds summed measures by ``entity_col`` and ``month``;
    ``network_monthly`` holds network totals by ``month``.
    """
    rule = RULES[rule_name]
    metric = METRICS[rule.metric]
    scale = _scale(rule.metric)
    min_volume = MIN_VOLUME[entity_type] * period.months
    baseline_period = period.trailing(BASELINE_MONTHS)
    history_period = period.trailing(HISTORY_MONTHS)

    cur = _sum_by(_window(monthly, period), [entity_col]).set_index(entity_col)
    base = _sum_by(_window(monthly, baseline_period), [entity_col]).set_index(entity_col)
    if cur.empty or base.empty:
        return []
    net_cur = _network_totals(network_monthly, period)
    net_base = _network_totals(network_monthly, baseline_period)

    hist = add_metrics(_window(monthly, history_period).copy())
    net_hist = add_metrics(_window(network_monthly, history_period).copy()).set_index("month")[rule.metric]
    if rule.relative:
        hist["gap"] = hist[rule.metric] - hist["month"].map(net_hist)
    else:
        hist["gap"] = hist[rule.metric]
    # Only months with enough volume count toward variability.
    hist = hist[hist["flights"] >= MIN_VOLUME[entity_type]]
    sigma = hist.groupby(entity_col)["gap"].agg(["std", "count"])

    signals: list[Signal] = []
    for entity, row in cur.iterrows():
        if entity not in base.index or row["flights"] < min_volume:
            continue
        b = base.loc[entity]
        if b["flights"] < MIN_VOLUME[entity_type] * BASELINE_MONTHS:
            continue
        c_val, b_val = row[rule.metric], b[rule.metric]
        if pd.isna(c_val) or pd.isna(b_val):
            continue
        abs_delta = (c_val - b_val) * scale
        if rule.relative:
            delta = ((c_val - net_cur[rule.metric]) - (b_val - net_base[rule.metric])) * scale
        else:
            delta = abs_delta
        if abs(delta) < rule.min_delta or np.sign(delta) != np.sign(abs_delta):
            continue
        if abs(abs_delta) < rule.min_delta * MIN_ABSOLUTE_SHARE:
            continue
        if rule.min_ratio and delta > 0 and safe_div(c_val, b_val) < rule.min_ratio:
            continue
        std = sigma.loc[entity, "std"] * scale if entity in sigma.index and sigma.loc[entity, "count"] >= 6 else np.nan
        std = max(std if not np.isnan(std) else 0.0, rule.min_sigma)
        z = delta / std
        if abs(z) < rule.z_threshold:
            continue

        worse = (delta < 0) if metric.direction is Direction.HIGHER_IS_BETTER else (delta > 0)
        if not worse and not rule.improvements:
            continue
        impact = _impact(rule_name, row, abs_delta)
        signals.append(
            _shift_signal(rule_name, entity_type, str(entity), label(str(entity)), worse, c_val, b_val,
                          abs_delta, delta, z, impact, row, net_cur, net_base, period, rule)
        )
    return signals


def _impact(rule_name: str, row: pd.Series, abs_delta: float) -> float:
    if rule_name == "on_time":
        return abs(abs_delta) / 100 * row["arr_eligible"]  # additional (or avoided) delayed arrivals
    if rule_name in ("cancellation",):
        return abs(abs_delta) / 100 * row["flights"]
    if rule_name == "severe":
        return abs(abs_delta) / 100 * row["arr_eligible"]
    if rule_name == "taxi_out":
        # Equivalent 15-minute delays: extra taxi minutes across all departures ÷ 15.
        return abs(abs_delta) * row.get("taxi_out_n", row["flights"]) / 15
    return 0.0


_TITLES = {
    "on_time": ("On-time deterioration", "On-time improvement"),
    "cancellation": ("Cancellation spike", "Cancellation relief"),
    "severe": ("Severe-delay anomaly", "Severe delays easing"),
    "taxi_out": ("Taxi-out increase", "Taxi-out improvement"),
}
_WHY = {
    "on_time": "On-time arrival is the headline reliability measure customers and DOT rankings use.",
    "cancellation": "Cancellations strand passengers and crews and usually signal operational stress beyond routine delay.",
    "severe": "60+ minute delays drive missed connections, crew legality issues and compensation costs.",
    "taxi_out": "Rising taxi-out points to surface congestion, deicing or departure-queue constraints at the airport.",
}


def _shift_signal(rule_name, entity_type, entity, entity_label, worse, c_val, b_val, abs_delta, delta, z,
                  impact, row, net_cur, net_base, period, rule) -> Signal:
    metric = METRICS[rule.metric]
    unit = _unit_text(rule.metric)
    title = _TITLES[rule_name][0 if worse else 1]
    verb = {True: "fell" if metric.direction is Direction.HIGHER_IS_BETTER else "rose",
            False: "rose" if metric.direction is Direction.HIGHER_IS_BETTER else "fell"}[worse]
    detail = (
        f"{entity_label} {metric.label.lower()} {verb} to {metric.format(c_val)}, "
        f"{abs(abs_delta):.1f} {unit} {'below' if abs_delta < 0 else 'above'} its trailing "
        f"{BASELINE_MONTHS}-month baseline of {metric.format(b_val)}."
    )
    if rule.relative:
        net_move = (net_cur[rule.metric] - net_base[rule.metric]) * _scale(rule.metric)
        detail += (
            f" The network moved {net_move:+.1f} {unit} over the same comparison, so the "
            f"entity-specific shift is {delta:+.1f} {unit}."
        )
    impact_text = {
        "on_time": "delayed arrivals", "cancellation": "cancellations", "severe": "severe delays",
        "taxi_out": "equivalent 15-min delays",
    }[rule_name]
    impact_prefix = "≈" + fmt_int(impact) + (" additional " if worse else " fewer ")
    return Signal(
        kind=f"{rule_name}_{'deterioration' if worse else 'improvement'}",
        category=title,
        entity_type=entity_type,
        entity=entity,
        entity_label=entity_label,
        direction="deterioration" if worse else "improvement",
        headline=f"{entity_label}: {title.lower()}",
        detail=detail,
        comparison=f"{period.label} vs trailing {BASELINE_MONTHS} months ({period.trailing(BASELINE_MONTHS).label})",
        why=_WHY[rule_name],
        metric=rule.metric,
        current=float(c_val),
        baseline=float(b_val),
        delta=float(delta),
        z=float(z),
        impact=float(impact),
        volume=float(row["flights"]),
        severity=_severity(impact, z, period.months),
        extras={"impact_text": impact_prefix + impact_text},
    )


def detect_concentration(
    current: pd.DataFrame, network: dict, entity_col: str, period: Period, label: Callable[[str], str] = str
) -> list[Signal]:
    """Airports contributing disproportionately to network delay minutes."""
    if current.empty or not network or not network.get("delay_minutes"):
        return []
    df = current[current["flights"] >= MIN_VOLUME["airport"] * period.months].copy()
    df["flight_share"] = df["flights"] / network["flights"]
    df["delay_share"] = df["delay_minutes"] / network["delay_minutes"]
    df["ratio"] = safe_div(df["delay_share"], df["flight_share"])
    hits = df[(df["ratio"] >= CONCENTRATION_MIN_RATIO) & (df["delay_share"] >= CONCENTRATION_MIN_SHARE)]
    per_delayed = safe_div(network["delay_minutes"], network.get("cause_flights", np.nan))
    out = []
    for _, row in hits.iterrows():
        excess_minutes = (row["delay_share"] - row["flight_share"]) * network["delay_minutes"]
        impact = safe_div(excess_minutes, per_delayed)
        name = label(str(row[entity_col]))
        out.append(Signal(
            kind="delay_concentration",
            category="Disproportionate delay contribution",
            entity_type="airport",
            entity=str(row[entity_col]),
            entity_label=name,
            direction="deterioration",
            headline=f"{name}: outsized share of network delay",
            detail=(
                f"{name} represented {fmt_pct(row['flight_share'])} of analyzed departures but "
                f"{fmt_pct(row['delay_share'])} of reported delay minutes ({row['ratio']:.1f}× its volume share)."
            ),
            comparison=f"Share of network totals, {period.label}",
            why="Delay concentrated at one station spreads across the network through connecting aircraft and crews.",
            metric="delay_minutes",
            current=float(row["delay_share"]),
            baseline=float(row["flight_share"]),
            delta=float((row["delay_share"] - row["flight_share"]) * 100),
            z=float(row["ratio"]),
            impact=float(impact if not np.isnan(impact) else 0.0),
            volume=float(row["flights"]),
            severity="High impact" if row["delay_share"] >= 0.03 and row["ratio"] >= 1.75 else "Elevated",
            extras={"impact_text": f"≈{fmt_int(impact)} delayed-flight equivalents above volume share"},
        ))
    return out


def detect_cause_shift(
    monthly: pd.DataFrame, period: Period, entity_col: str | None, entity_type: str,
    label: Callable[[str], str] = str,
) -> list[Signal]:
    """Material shifts in the delay-cause mix vs the trailing baseline."""
    keys = [entity_col] if entity_col else []
    cur_df = _window(monthly, period)
    base_df = _window(monthly, period.trailing(BASELINE_MONTHS))
    if cur_df.empty or base_df.empty:
        return []
    if not keys:
        cur_df, base_df = cur_df.assign(_all="Network"), base_df.assign(_all="Network")
        keys = ["_all"]
    cur = _sum_by(cur_df, keys).set_index(keys[0])
    base = _sum_by(base_df, keys).set_index(keys[0])
    out = []
    for entity, row in cur.iterrows():
        if entity not in base.index or row["flights"] < MIN_VOLUME.get(entity_type, 3_000) * period.months:
            continue
        b = base.loc[entity]
        if row["delay_minutes"] <= 0 or b["delay_minutes"] <= 0:
            continue
        shifts = {
            cause: (row[m] / row["delay_minutes"] - b[m] / b["delay_minutes"]) * 100
            for cause, m in CAUSE_MEASURES.items()
        }
        cause, pts = max(shifts.items(), key=lambda kv: abs(kv[1]))
        if abs(pts) < CAUSE_SHIFT_MIN_PTS:
            continue
        name = "The network" if keys[0] == "_all" else label(str(entity))
        share_now = row[CAUSE_MEASURES[cause]] / row["delay_minutes"]
        impact = abs(pts) / 100 * row["delay_minutes"] / max(row["avg_delay_per_delayed"], 1)
        out.append(Signal(
            kind="cause_shift",
            category="Delay-cause shift",
            entity_type="network" if keys[0] == "_all" else entity_type,
            entity=str(entity),
            entity_label="Network" if keys[0] == "_all" else label(str(entity)),
            direction="shift",
            headline=f"{name}: {CAUSE_LABELS[cause]} share of delay {'up' if pts > 0 else 'down'} {abs(pts):.1f} pts",
            detail=(
                f"{CAUSE_LABELS[cause]} accounted for {fmt_pct(share_now)} of reported delay minutes, "
                f"{pts:+.1f} pts versus the trailing {BASELINE_MONTHS}-month mix."
            ),
            comparison=f"{period.label} vs trailing {BASELINE_MONTHS} months",
            why="A change in reported cause mix changes which lever matters (schedule buffers, ATC programs, turn times).",
            metric="delay_minutes",
            current=float(share_now),
            baseline=float(b[CAUSE_MEASURES[cause]] / b["delay_minutes"]),
            delta=float(pts),
            z=float(pts / CAUSE_SHIFT_MIN_PTS),
            impact=float(impact),
            volume=float(row["flights"]),
            severity="Elevated" if abs(pts) >= 10 else "Watch",
            extras={"impact_text": f"{abs(pts):.1f} pts of delay mix reassigned", "cause": cause},
        ))
    return out


def detect_improvement_streaks(
    monthly: pd.DataFrame, period: Period, entity_col: str, label: Callable[[str], str] = str, min_streak: int = 3
) -> list[Signal]:
    """Carriers whose on-time rate beat the same month a year earlier for consecutive months."""
    df = add_metrics(monthly.copy())
    df["month"] = pd.to_datetime(df["month"]).dt.date
    out = []
    for entity, grp in df.groupby(entity_col):
        series = grp.set_index("month")["on_time_rate"]
        volume = grp.set_index("month")["flights"]
        streak, gains, month = 0, [], period.end
        while month in series.index:
            prior = month.replace(year=month.year - 1)
            if prior not in series.index or volume.get(month, 0) < MIN_VOLUME["carrier"]:
                break
            gain = (series[month] - series[prior]) * 100
            if gain <= 0.5:
                break
            streak += 1
            gains.append(gain)
            month = (pd.Timestamp(month) - pd.DateOffset(months=1)).date()
        if streak >= min_streak:
            avg_gain = float(np.mean(gains))
            last = grp[pd.to_datetime(grp["month"]).dt.date == period.end].iloc[0]
            name = label(str(entity))
            out.append(Signal(
                kind="improvement_streak",
                category="Improvement streak",
                entity_type="carrier",
                entity=str(entity),
                entity_label=name,
                direction="improvement",
                headline=f"{name}: {streak} straight months of year-over-year on-time gains",
                detail=(
                    f"{name} beat its same-month prior-year on-time rate for {streak} consecutive months, "
                    f"by an average of {avg_gain:.1f} pts. The latest month was {fmt_pct(series[period.end])}."
                ),
                comparison="Each month vs the same month one year earlier",
                why="Sustained year-over-year gains separate structural improvement from favorable weather months.",
                metric="on_time_rate",
                current=float(series[period.end]),
                baseline=float(series[period.end.replace(year=period.end.year - 1)]),
                delta=avg_gain,
                z=float(streak),
                impact=float(avg_gain / 100 * last["arr_eligible"]),
                volume=float(last["flights"]),
                severity="Elevated" if streak >= 6 else "Watch",
                extras={"impact_text": f"≈{fmt_int(avg_gain / 100 * last['arr_eligible'])} fewer delayed arrivals per month"},
            ))
    return out


def rank(signals: list[Signal], limit: int | None = None) -> list[Signal]:
    ordered = sorted(signals, key=lambda s: s.sort_key)
    return ordered[:limit] if limit else ordered


def cap(signals: list[Signal]) -> list[Signal]:
    """Keep the most significant signals per (entity type, kind)."""
    kept: list[Signal] = []
    counts: dict[tuple[str, str], int] = {}
    for signal in rank(signals):
        key = (signal.entity_type, signal.kind)
        if counts.get(key, 0) < CAPS.get(signal.entity_type, 5):
            kept.append(signal)
            counts[key] = counts.get(key, 0) + 1
    return kept


@dataclass
class SignalInputs:
    """Monthly summed measures for every entity level the engine evaluates."""

    network: pd.DataFrame  # by month
    airports: pd.DataFrame  # by origin, month (fact_route_monthly)
    airport_hourly: pd.DataFrame  # by origin, month (fact_origin_hourly: taxi-out)
    carriers: pd.DataFrame  # by carrier, month
    routes: pd.DataFrame  # by route, month
    airport_current: pd.DataFrame  # by origin for the period, with metrics
    network_current: dict


def detect_all(
    inputs: SignalInputs, period: Period,
    airport_label: Callable[[str], str] = str, carrier_label: Callable[[str], str] = str,
) -> list[Signal]:
    found: list[Signal] = []
    for rule in ("on_time", "cancellation", "severe"):
        found += detect_shifts(inputs.airports, inputs.network, "origin", "airport", period, rule, airport_label)
        found += detect_shifts(inputs.carriers, inputs.network, "carrier", "carrier", period, rule, carrier_label)
    found += detect_shifts(inputs.airport_hourly, inputs.network, "origin", "airport", period, "taxi_out", airport_label)
    found += detect_shifts(inputs.routes, inputs.network, "route", "route", period, "on_time")
    found += detect_concentration(inputs.airport_current, inputs.network_current, "origin", period, airport_label)
    found += detect_cause_shift(inputs.network, period, None, "network")
    found += detect_cause_shift(inputs.carriers, period, "carrier", "carrier", carrier_label)
    found += detect_improvement_streaks(inputs.carriers, period, "carrier", carrier_label)
    return rank(cap(found))
