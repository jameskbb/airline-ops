"""Regression: concurrent sessions must not read each other's DuckDB results.

Streamlit runs each session on its own thread and ``ui.data.store()`` shares one
``Store`` per process, so ``Store`` must give every query its own cursor. A single
shared connection lets threads interleave execute/fetch and swap results.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from flightops.config import PROCESSED_DIR
from flightops.data.query import Query
from flightops.data.store import Store

pytestmark = pytest.mark.skipif(not any(PROCESSED_DIR.glob("fact_route_monthly/*.parquet")),
                                reason="processed data not present")

THREADS = 8
ROUNDS = 6


def _queries(store: Store) -> list[Query]:
    months = store.loaded_months()
    start, end = months[-3], months[-1]
    return [
        Query.build("fact_route_monthly", start, end),
        Query.build("fact_route_monthly", start, end, by=("carrier",)),
        Query.build("fact_route_monthly", start, end, by=("origin",)),
        Query.build("fact_route_monthly", months[0], end, by=("month",)),
        Query.build("fact_origin_hourly", start, end, by=("origin",)),
        Query.build("fact_route_monthly", start, end, by=("route",)),
    ]


def test_concurrent_queries_match_serial_results():
    store = Store()
    queries = _queries(store)
    serial = [store.run(q) for q in queries]
    jobs = [i for _ in range(ROUNDS) for i in range(len(queries))]

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        results = list(pool.map(lambda i: (i, store.run(queries[i])), jobs))

    for i, df in results:
        # DuckDB's parallel sums can drift ~1e-16 relative between runs, so compare with a tolerance.
        pd.testing.assert_frame_equal(df, serial[i], check_exact=False, rtol=1e-9, atol=1e-12)


def test_dimension_reads_are_thread_safe():
    store = Store()
    expected = store.table("dim_carrier")
    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        frames = list(pool.map(lambda _: store.table("dim_carrier"), range(THREADS * ROUNDS)))
    for df in frames:
        pd.testing.assert_frame_equal(df, expected)

