"""Streamlit AppTest smoke tests: every page renders against the committed data without errors."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from flightops.config import PROCESSED_DIR

pytestmark = pytest.mark.skipif(not any(PROCESSED_DIR.glob("fact_route_monthly/*.parquet")),
                                reason="processed data not present")

PAGES = ["overview", "network_map", "signals", "airports", "routes", "carriers", "delay_drivers", "methodology",
         "explorer"]


def _page_app(page: str, preset: dict) -> None:
    import importlib

    import streamlit as st

    from flightops.charts import theme  # noqa: F401
    from flightops.ui import filters

    for key, value in preset.items():
        st.session_state.setdefault(key, value)
    st.session_state["_page"] = page
    st.session_state["_filters"] = filters.render_sidebar()
    importlib.import_module(f"flightops.pages.{page}").render()


def _run(page: str, **preset) -> AppTest:
    at = AppTest.from_function(_page_app, args=(page, preset), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    _run(page)


@pytest.mark.parametrize("page,preset", [
    ("overview", {"f_carrier": "DL"}),
    ("overview", {"f_origin": "DFW", "f_dest": "DEN"}),
    ("overview", {"f_preset": "Last 12 months"}),
    ("airports", {"f_carrier": "WN", "f_origin": "MDW"}),
    ("routes", {"f_origin": "EWR", "f_dest": "SFO"}),
    ("signals", {"f_carrier": "AA"}),
    ("delay_drivers", {"f_preset": "Last 3 months", "f_carrier": "UA"}),
])
def test_filtered_pages_render(page, preset):
    _run(page, **preset)


def test_pages_record_their_queries_in_the_query_store():
    at = _run("overview")
    log = at.session_state["query_log"]
    labels = {label for entry in log.values() for label in entry["labels"]}
    assert "Headline KPIs (current)" in labels and "Network health trend" in labels
    assert all(entry["pages"] == ["overview"] for entry in log.values())
