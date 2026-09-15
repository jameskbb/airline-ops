"""FlightOps Intelligence: U.S. airline operations and network performance.

Run with ``streamlit run app.py``. This entrypoint is the router and frame: it sets
page config, renders the shared sidebar filters and runs the selected page.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import streamlit as st  # noqa: E402

st.set_page_config(
    page_title="FlightOps Intelligence",
    page_icon=str(ROOT / "assets" / "icon.svg"),
    layout="wide",
    initial_sidebar_state="expanded",
)

from flightops.charts import theme  # noqa: E402,F401  (registers the Plotly template)
from flightops.data.store import DataNotAvailableError  # noqa: E402
from flightops.pages import (  # noqa: E402
    airports,
    carriers,
    delay_drivers,
    explorer,
    methodology,
    network_map,
    overview,
    routes,
    signals,
)
from flightops.ui import components, data, filters, nav  # noqa: E402

components.load_css()
st.logo(str(ROOT / "assets" / "logo.svg"), size="large", icon_image=str(ROOT / "assets" / "icon.svg"))

try:
    data.store()
except DataNotAvailableError as exc:
    components.empty_state("No processed data found", str(exc))
    st.stop()

explorer_page = st.Page(explorer.render, title="Data Explorer", icon=":material/table_view:", url_path="explorer")
nav.register(explorer=explorer_page)

navigation = st.navigation({
    "Overview": [
        st.Page(overview.render, title="Executive Overview", icon=":material/space_dashboard:", default=True),
        st.Page(network_map.render, title="Network Map", icon=":material/public:", url_path="network-map"),
        st.Page(signals.render, title="Signals", icon=":material/notifications_active:", url_path="signals"),
    ],
    "Analysis": [
        st.Page(airports.render, title="Airport Performance", icon=":material/local_airport:", url_path="airports"),
        st.Page(routes.render, title="Route Intelligence", icon=":material/route:", url_path="routes"),
        st.Page(carriers.render, title="Carrier Benchmarking", icon=":material/leaderboard:", url_path="carriers"),
        st.Page(delay_drivers.render, title="Delay Drivers", icon=":material/timer:", url_path="delay-drivers"),
    ],
    "Data": [
        explorer_page,
        st.Page(methodology.render, title="Methodology", icon=":material/fact_check:", url_path="methodology"),
    ],
})
st.session_state["_page"] = navigation.title
st.session_state["_filters"] = filters.render_sidebar()
navigation.run()
components.footer()
