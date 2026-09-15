import datetime as dt
import json

import duckdb
import pytest

from flightops.data import pipeline
from flightops.data.months import add_months, month_range, parse_month
from flightops.data.schema import SchemaError, validate_header
from flightops.data.source import parse_index
from flightops.data.store import Store
from flightops.data.transform import FACT_TABLES, process_month
from tests.conftest import RAW_HEADER, flight, write_bts_zip

JUNE = dt.date(2026, 6, 1)


@pytest.fixture
def processed(tmp_path, bts_zip):
    out = tmp_path / "processed"
    report = process_month(bts_zip, JUNE, out)
    meta = tmp_path / "metadata"
    (meta / "months").mkdir(parents=True)
    (meta / "months" / "2026-06.json").write_text(json.dumps(report.to_dict()))
    return out, meta, report


def test_header_validation_tolerates_trailing_space_and_fails_on_missing():
    assert validate_header(RAW_HEADER, "x.zip") == []
    broken = [c for c in RAW_HEADER if c.strip() != "ArrDel15"]
    with pytest.raises(SchemaError, match="ArrDel15"):
        validate_header(broken, "x.zip")


def test_new_source_columns_are_reported():
    assert validate_header([*RAW_HEADER, "BrandNewColumn"], "x.zip") == ["BrandNewColumn"]


def test_quality_report_counts(processed):
    _, _, report = processed
    assert report.raw_rows == 10
    assert report.duplicate_rows == 1
    assert report.out_of_month_rows == 1
    assert report.loaded_rows == 8
    assert report.cancelled == 1 and report.diverted == 1
    assert report.unexpected_carriers == {"ZZ": 1}
    assert report.cause_rows == 3 and report.cause_reconciled_rows == 3
    assert set(report.table_rows) == set(FACT_TABLES)


def test_facts_handle_cancelled_diverted_and_null_causes(processed):
    out, meta, _ = processed
    store = Store(out, meta)
    totals = store.totals("fact_route_monthly", JUNE, JUNE)
    assert totals["flights"] == 8
    assert totals["arr_eligible"] == 6  # cancelled + diverted excluded
    assert totals["arr_del15"] == 3
    assert totals["arr_del60"] == 1
    assert totals["delay_minutes"] == 20 + 75 + 16  # null causes on on-time flights are not zero-filled rows
    assert totals["cancelled_weather"] == 1
    assert totals["on_time_rate"] == pytest.approx(3 / 6)


def test_fact_tables_reconcile(processed):
    out, meta, report = processed
    assert pipeline.reconcile(out, meta) == {"2026-06": report.loaded_rows}


def test_route_identifiers_directional_and_market(processed):
    out, meta, _ = processed
    store = Store(out, meta)
    routes = store.aggregate("fact_route_monthly", JUNE, JUNE, by=["route", "market"], metrics=False)
    assert set(routes["route"]) == {"DFW→DEN", "DFW→ORD"}
    assert set(routes["market"]) == {"DEN–DFW", "DFW–ORD"}


def test_filters_and_unsupported_filters(processed):
    out, meta, _ = processed
    store = Store(out, meta)
    dl = store.totals("fact_route_monthly", JUNE, JUNE, {"carrier": "DL"})
    assert dl["flights"] == 1
    multi = store.totals("fact_route_monthly", JUNE, JUNE, {"carrier": ["AA", "DL"]})
    assert multi["flights"] == 7
    with pytest.raises(ValueError):
        store.aggregate("fact_origin_daily", JUNE, JUNE, {"dest": "DEN"})


def test_date_filtering_excludes_months_outside_window(processed):
    out, meta, _ = processed
    store = Store(out, meta)
    assert store.totals("fact_route_monthly", dt.date(2026, 5, 1), dt.date(2026, 5, 1)) == {}
    hours = store.aggregate("fact_origin_hourly", JUNE, JUNE, by=["dep_hour"])
    assert set(hours["dep_hour"]) == {8, 17, 19}
    dow = store.aggregate("fact_origin_daily", JUNE, JUNE, by=["dow"])
    assert list(dow["dow"]) == [1]


def test_reprocessing_is_idempotent(tmp_path, bts_zip):
    out = tmp_path / "processed"
    process_month(bts_zip, JUNE, out)
    process_month(bts_zip, JUNE, out)
    con = duckdb.connect()
    n = con.execute(f"SELECT sum(flights) FROM '{(out / 'fact_route_monthly').as_posix()}/*.parquet'").fetchone()[0]
    assert n == 8


def test_empty_month_fails_clearly(tmp_path):
    path = write_bts_zip(tmp_path / "bad.zip", [flight(FlightDate="2026-07-02")])
    with pytest.raises(SchemaError, match="no valid rows"):
        process_month(path, JUNE, tmp_path / "p")


def test_plan_sync_skips_loaded_and_prunes_outside_window(processed):
    out, meta, _ = processed
    requested = month_range(parse_month("2026-05"), parse_month("2026-07"))
    available = month_range(parse_month("2026-01"), parse_month("2026-06"))
    plan = pipeline.plan_sync(requested, available, force=False, prune=True, processed_dir=out, metadata_dir=meta)
    assert plan.already_loaded == [JUNE]
    assert plan.to_process == [dt.date(2026, 5, 1)]
    assert plan.unpublished == [dt.date(2026, 7, 1)]
    plan = pipeline.plan_sync([dt.date(2026, 5, 1)], available, False, True, out, meta)
    assert plan.to_prune == ["2026-06"]


def test_resolve_window_and_index_parsing():
    html = ('<a href="/PREZIP/On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_2026_5.zip">'
            '<a href="/PREZIP/On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_2026_6.zip">')
    available = parse_index(html)
    assert available == [dt.date(2026, 5, 1), JUNE]
    assert pipeline.resolve_window(available, months=1) == [JUNE]
    assert pipeline.resolve_window(available, start="2026-04", end="2026-05") == [dt.date(2026, 4, 1), dt.date(2026, 5, 1)]
    assert add_months(JUNE, -13) == dt.date(2025, 5, 1)
