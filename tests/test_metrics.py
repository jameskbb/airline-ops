import math

import pandas as pd
import pytest

from flightops.metrics import METRICS, Direction, add_metrics, compare, compute, get_metric
from flightops.metrics.definitions import cause_mix, safe_div
from tests.conftest import measures_row


def test_on_time_rate_excludes_cancelled_and_diverted():
    # 100 scheduled, 5 cancelled, 2 diverted → 93 eligible arrivals, 13 late
    v = measures_row(flights=100, cancelled=5, diverted=2, arr_eligible=93, arr_del15=13)
    assert compute("on_time_rate", v) == pytest.approx(80 / 93)
    assert compute("delay15_rate", v) == pytest.approx(13 / 93)


def test_cancellation_completion_and_diversion_rates_use_scheduled_denominator():
    v = measures_row(flights=200, cancelled=6, diverted=2)
    assert compute("cancellation_rate", v) == pytest.approx(0.03)
    assert compute("completion_rate", v) == pytest.approx(0.97)
    assert compute("diversion_rate", v) == pytest.approx(0.01)


def test_severe_delay_rate():
    v = measures_row(arr_eligible=50, arr_del60=4)
    assert compute("severe_delay_rate", v) == pytest.approx(0.08)


def test_positive_delay_vs_schedule_variance_are_distinct():
    # Early arrivals pull variance negative but contribute 0 to positive delay.
    v = measures_row(arr_eligible=4, arr_delay_sum=-10, arr_delay_min_sum=30)
    assert compute("avg_arr_delay", v) == pytest.approx(7.5)
    assert compute("avg_arr_variance", v) == pytest.approx(-2.5)


def test_reliability_score_penalizes_cancellations():
    v = measures_row(flights=100, cancelled=10, arr_eligible=90, arr_del15=10)
    assert compute("reliability_score", v) == pytest.approx(80.0)
    assert compute("on_time_rate", v) == pytest.approx(80 / 90)


def test_delay_cause_aggregation_and_share():
    v = measures_row(flights=10, delay_carrier_min=30, delay_weather_min=10, delay_nas_min=20,
                     delay_security_min=0, delay_late_aircraft_min=40, cause_flights=4)
    assert compute("delay_minutes", v) == 100
    assert compute("delay_min_per_100", v) == pytest.approx(1000)
    assert compute("propagation_index", v) == pytest.approx(0.4)
    assert compute("avg_delay_per_delayed", v) == pytest.approx(25)
    mix = cause_mix(v)
    assert sum(mix.values()) == pytest.approx(1.0)
    assert mix["late_aircraft"] == pytest.approx(0.4)


def test_zero_denominators_yield_nan_not_errors():
    v = measures_row()
    assert math.isnan(compute("on_time_rate", v))
    assert math.isnan(compute("propagation_index", v))
    assert math.isnan(safe_div(1, 0))


def test_safe_div_preserves_filtered_index_with_scalar_denominator():
    s = pd.Series([10.0, 20.0, 30.0], index=[3, 7, 11])
    out = safe_div(s, 10)
    assert list(out.index) == [3, 7, 11] and list(out) == [1.0, 2.0, 3.0]
    assert safe_div(s, 0).isna().all()
    ratio = safe_div(s, pd.Series([5.0, 0.0, 10.0], index=[3, 7, 11]))
    assert ratio[3] == 2.0 and math.isnan(ratio[7]) and ratio[11] == 3.0


def test_add_metrics_is_vectorized_and_skips_unavailable():
    df = pd.DataFrame([measures_row(flights=10, arr_eligible=10, arr_del15=2),
                       measures_row(flights=0, arr_eligible=0, arr_del15=0)])
    out = add_metrics(df.drop(columns=["taxi_out_sum"]))
    assert out.loc[0, "on_time_rate"] == pytest.approx(0.8)
    assert math.isnan(out.loc[1, "on_time_rate"])
    assert "avg_taxi_out" not in out.columns


def test_every_metric_has_direction_and_definition():
    for metric in METRICS.values():
        assert isinstance(metric.direction, Direction)
        assert metric.definition and metric.calculation


def test_rate_comparisons_use_percentage_points():
    d = compare(get_metric("on_time_rate"), 0.784, 0.816)
    assert d.kind == "pts"
    assert d.change == pytest.approx(-3.2)
    assert d.text == "-3.2 pts"
    assert d.favorable is False


def test_lower_is_better_direction():
    d = compare(get_metric("cancellation_rate"), 0.015, 0.021)
    assert d.favorable is True  # fewer cancellations is an improvement
    d = compare(get_metric("delay_minutes"), 90_000, 100_000)
    assert d.kind == "pct" and d.favorable is True and d.text == "-10.0%"


def test_minutes_compare_in_minutes_and_neutral_metric_has_no_favorability():
    d = compare(get_metric("avg_arr_delay"), 18.6, 17.4)
    assert d.kind == "min" and d.text == "+1.2 min" and d.favorable is False
    assert compare(get_metric("flights"), 110, 100).favorable is None


def test_flat_changes_are_neutral_and_missing_returns_none():
    assert compare(get_metric("on_time_rate"), 0.8001, 0.8).favorable is None
    assert compare(get_metric("on_time_rate"), None, 0.8) is None
    assert compare(get_metric("on_time_rate"), float("nan"), 0.8) is None
