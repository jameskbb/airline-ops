import datetime as dt

import pytest

from flightops.analytics import signals as sig
from flightops.analytics.benchmarks import apply_min_volume, distance_band, hotspots, hub_tier, percentile
from flightops.metrics import Period, add_metrics
from flightops.narrative import deterministic_brief
from tests.conftest import monthly_frame

PERIOD = Period(dt.date(2026, 6, 1), dt.date(2026, 6, 1))
HISTORY = [f"2025-{m:02d}" for m in range(6, 13)] + [f"2026-{m:02d}" for m in range(1, 7)]


def _series(entity, flights, rate_by_month, cancel=0.01):
    rows = []
    for month in HISTORY:
        rate = rate_by_month(month)
        eligible = int(flights * (1 - cancel))
        rows.append((entity, month, flights, eligible, round(eligible * rate), int(flights * cancel)))
    return rows


def _network(frame):
    return frame.drop(columns=["origin"]).groupby("month", as_index=False).sum()


def test_airport_deterioration_fires_relative_to_network():
    stable = lambda m: 0.20  # noqa: E731
    spike = lambda m: 0.36 if m == "2026-06" else (0.20 + (0.004 if m.endswith(("1", "3", "5", "7", "9")) else -0.004))  # noqa: E731
    frame = monthly_frame("origin", _series("ORD", 20_000, spike) + _series("ATL", 30_000, stable)
                          + _series("DEN", 25_000, stable))
    found = sig.detect_shifts(frame, _network(frame), "origin", "airport", PERIOD, "on_time")
    assert [s.entity for s in found] == ["ORD"]
    signal = found[0]
    assert signal.direction == "deterioration"
    assert signal.delta < -4
    assert signal.impact > 0 and "trailing 3-month baseline" in signal.detail


def test_networkwide_swing_does_not_flag_every_airport():
    storm = lambda m: 0.35 if m == "2026-06" else 0.20  # noqa: E731
    frame = monthly_frame("origin", _series("ORD", 20_000, storm) + _series("ATL", 30_000, storm))
    assert sig.detect_shifts(frame, _network(frame), "origin", "airport", PERIOD, "on_time") == []


def test_low_volume_entities_never_signal():
    spike = lambda m: 0.60 if m == "2026-06" else 0.20  # noqa: E731
    frame = monthly_frame("origin", _series("TNY", 300, spike) + _series("ATL", 30_000, lambda m: 0.2))
    assert sig.detect_shifts(frame, _network(frame), "origin", "airport", PERIOD, "on_time") == []


def test_small_changes_below_materiality_do_not_fire():
    drift = lambda m: 0.23 if m == "2026-06" else 0.20  # noqa: E731  3 pts < 4-pt floor
    frame = monthly_frame("origin", _series("ORD", 20_000, drift) + _series("ATL", 30_000, lambda m: 0.2))
    assert sig.detect_shifts(frame, _network(frame), "origin", "airport", PERIOD, "on_time") == []


def test_cancellation_spike_requires_ratio_and_delta():
    frame = monthly_frame("origin", [
        *[("ORD", m, 20_000, 19_000, 3_000, 5_000 if m == "2026-06" else 200) for m in HISTORY],
        *[("ATL", m, 30_000, 29_000, 4_000, 300) for m in HISTORY],
    ])
    found = sig.detect_shifts(frame, _network(frame), "origin", "airport", PERIOD, "cancellation")
    assert [s.entity for s in found] == ["ORD"] and found[0].kind == "cancellation_deterioration"


def test_ranking_orders_by_severity_then_impact():
    a = sig.Signal("k", "c", "airport", "A", "A", "deterioration", "", "", "", "", "m", 0, 0, 0, 2, 100, 1, "Watch")
    b = sig.Signal("k", "c", "airport", "B", "B", "deterioration", "", "", "", "", "m", 0, 0, 0, 3, 50, 1, "High impact")
    c = sig.Signal("k", "c", "airport", "C", "C", "deterioration", "", "", "", "", "m", 0, 0, 0, 2, 900, 1, "Watch")
    assert [s.entity for s in sig.rank([a, b, c])] == ["B", "C", "A"]


def test_percentile_is_direction_aware():
    df = add_metrics(monthly_frame("origin", [("A", "2026-06", 100, 100, 10, 0), ("B", "2026-06", 100, 100, 30, 0),
                                              ("C", "2026-06", 100, 100, 20, 0)]))
    df["on_time_rate_pct"] = percentile(df, "on_time_rate")
    df["delay_pct"] = percentile(df, "delay15_rate")
    assert list(df["on_time_rate_pct"]) == [100, 0, 50]
    assert list(df["delay_pct"]) == [100, 0, 50]  # lower delay rate ranks higher


def test_min_volume_and_hotspots():
    df = add_metrics(monthly_frame("origin", [("BIG", "2026-06", 10_000, 9_800, 3_000, 0),
                                              ("OK", "2026-06", 10_000, 9_800, 1_500, 0),
                                              ("TNY", "2026-06", 50, 50, 40, 0)]))
    df["delay_minutes"] = df["arr_del15"] * 50
    assert set(apply_min_volume(df, 1_000, 1)["origin"]) == {"BIG", "OK"}
    network = {"flights": 20_050, "delay_minutes": df["delay_minutes"].sum(), "delay15_rate": 4_540 / 19_650}
    hs = hotspots(df, network, 1, 1_000)
    assert list(hs["origin"]) == ["BIG"]


def test_tiers_and_bands():
    assert hub_tier(0.02) == "Large hub" and hub_tier(0.001) == "Small hub" and hub_tier(0.0001) == "Non-hub"
    assert distance_band(641) == "500–999 mi" and distance_band(2600) == "2,500+ mi"


def test_deterministic_brief_uses_only_supplied_facts():
    facts = {
        "scope": "the network",
        "network": {"on_time_rate": 0.784, "cancellation_rate": 0.021},
        "comparisons": {
            "on_time_rate": {"vs_prior": {"change": -4.2, "text": "-4.2 pts", "favorable": False},
                             "vs_prior_year": {"change": -1.7, "text": "-1.7 pts", "favorable": False}},
            "cancellation_rate": {"vs_prior": {"change": 0.6, "text": "+0.6 pts", "favorable": False}},
        },
        "largest_cause": {"cause": "late_aircraft", "label": "Late Aircraft", "share": 0.391},
        "hotspot": {"airport": "DFW", "delay_share": 0.128, "flight_share": 0.079},
    }
    brief = deterministic_brief(facts)
    assert 3 <= len(brief) <= 5
    assert brief[0].topic == "on_time" and "78.4%" in brief[0].text and "-4.2 pts" in brief[0].text
    assert any("DFW accounted for 12.8%" in s.text for s in brief)
    assert any(s.tone == "negative" for s in brief)


@pytest.mark.parametrize("rule", list(sig.RULES))
def test_rules_have_positive_thresholds(rule):
    r = sig.RULES[rule]
    assert r.min_delta > 0 and r.min_sigma > 0 and r.z_threshold >= 2
