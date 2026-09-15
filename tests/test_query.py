import datetime as dt

import pytest

from flightops.data.query import TABLES, Query

JUNE = dt.date(2026, 6, 1)


def test_build_drops_none_records_unsupported_and_normalizes_lists():
    q = Query.build("fact_origin_hourly", JUNE, JUNE, by=("dep_hour",), carrier=["DL", "AA"], origin=None, dest="DEN")
    assert q.filters == (("carrier", ("DL", "AA")),)
    assert q.not_applied == ("dest",)  # the hourly table has no destination column


def test_invalid_table_or_grouping_is_rejected():
    with pytest.raises(ValueError):
        Query.build("nope", JUNE, JUNE)
    with pytest.raises(ValueError):
        Query.build("fact_origin_daily", JUNE, JUNE, by=("dest",))


def test_ids_are_stable_and_distinguish_queries():
    a = Query.build("fact_route_monthly", JUNE, JUNE, carrier="DL")
    assert a.id == Query.build("fact_route_monthly", JUNE, JUNE, carrier="DL").id
    assert a.id != Query.build("fact_route_monthly", JUNE, JUNE, carrier="AA").id
    assert a.id.startswith("Q-") and len(a.id) == 10


def test_aggregate_sql_is_parameterized_and_display_sql_inlines_values():
    q = Query.build("fact_route_monthly", JUNE, JUNE, by=("route",), carrier="O'Hare", origin=["DFW", "DEN"])
    sql, params = q.sql(["flights", "arr_del15"])
    assert "?" in sql and "O'Hare" not in sql
    assert params == [JUNE, JUNE, "O'Hare", "DFW", "DEN"]
    assert "origin || '→' || dest AS route" in sql and "GROUP BY 1" in sql
    shown = q.display_sql(["flights", "arr_del15"])
    assert "DATE '2026-06-01'" in shown and "'O''Hare'" in shown and "?" not in shown


def test_rows_query_selects_source_rows():
    sql, _ = Query.build("fact_origin_daily", JUNE, JUNE, rows=True, carrier="DL").sql(["flights"])
    assert sql.startswith("SELECT *") and "GROUP BY" not in sql


def test_describe_is_human_readable():
    text = Query.build("fact_route_monthly", JUNE, dt.date(2026, 8, 1), by=("carrier",), origin="DFW").describe()
    assert text == "fact_route_monthly · Jun 2026 – Aug 2026 · origin = DFW · by carrier"


def test_table_contract_matches_every_fact_table():
    assert set(TABLES) == {"fact_origin_daily", "fact_route_monthly", "fact_origin_hourly", "fact_route_profile"}
