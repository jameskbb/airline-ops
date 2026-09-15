"""Page registry, so components can link to pages created by the entrypoint."""

from __future__ import annotations

import streamlit as st

_pages: dict = {}


def register(**pages) -> None:
    _pages.update(pages)


def open_in_explorer(query_id: str) -> None:
    """Jump to the Data Explorer with a query from the query store selected."""
    st.session_state["explorer_query"] = query_id
    st.switch_page(_pages["explorer"])
