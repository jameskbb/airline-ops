"""Data Explorer: the query store and the raw analytical tables, with CSV export."""

from __future__ import annotations

import dataclasses

import pandas as pd
import streamlit as st

from flightops.data.measures import MEASURES
from flightops.data.months import format_month
from flightops.data.query import TABLE_DESCRIPTIONS, TABLES, Query
from flightops.data.store import DIMENSION_TABLES
from flightops.metrics import METRICS, Period
from flightops.ui import components as ui
from flightops.ui import data
from flightops.ui.filters import current

PREVIEW_ROWS = 2_000
EXPORT_ROW_CAP = 500_000
COLUMN_TEXT = {
    **{m.name: m.description for m in MEASURES},
    "flight_date": "Flight date (local)",
    "month": "Reporting month (first day of month)",
    "carrier": "Marketing carrier code (branded network)",
    "origin": "Origin airport code",
    "origin_id": "BTS origin airport ID (stable across code changes)",
    "dest": "Destination airport code",
    "dest_id": "BTS destination airport ID",
    "dep_hour": "Scheduled local departure hour (0–23; -1 = missing)",
    "dimension": "Profile type: 'hour' (scheduled departure hour) or 'dow' (ISO day of week, 1 = Monday)",
    "bucket": "Hour (0–23) or day of week (1–7), depending on `dimension`",
}


def _dates(df: pd.DataFrame) -> pd.DataFrame:
    """Show date columns as dates rather than midnight timestamps."""
    out = df.copy()
    for col in ("month", "flight_date"):
        if col in out:
            out[col] = pd.to_datetime(out[col]).dt.date
    return out


def _csv_download(store, query: Query, label: str, key: str) -> None:
    """Lazy CSV export: the query runs only when the button is clicked (no rerun)."""
    st.download_button(
        label, data=lambda: store.run(query, limit=EXPORT_ROW_CAP).to_csv(index=False).encode("utf-8"),
        file_name=f"flightops_{query.table}_{query.id}.csv", mime="text/csv", on_click="ignore",
        icon=":material/download:", key=key,
    )


def render() -> None:
    f = current()
    ui.page_header("Data Explorer",
                   "Every table behind the dashboard, and every query the dashboard ran for you in this session, "
                   "with the exact SQL, results, source rows and CSV export.", f)
    queries_tab, tables_tab = st.tabs(["Query store", "Tables"])
    with queries_tab:
        _query_store()
    with tables_tab:
        _tables(f)


def _query_store() -> None:
    entries = data.logged()
    if not entries:
        ui.empty_state("No queries yet", "Visit any page; each query it runs is recorded here.")
        return
    listing = pd.DataFrame([{
        "Query": e["query"].id,
        "Used for": "; ".join(e["labels"]),
        "Pages": ", ".join(e["pages"]),
        "Definition": e["query"].describe(),
    } for e in entries])
    st.caption(f"{len(entries)} distinct queries this session, most recently used first. "
               "Identical queries share one ID and are served from cache.")
    st.dataframe(listing, hide_index=True, width="stretch", height=240)

    ids = [e["query"].id for e in entries]
    wanted = st.session_state.pop("explorer_query", None)
    if wanted in ids:
        st.session_state["qs_pick"] = wanted
    by_id = {e["query"].id: e for e in entries}
    picked = st.selectbox("Inspect query", ids, key="qs_pick",
                          format_func=lambda i: f"{i} · {by_id[i]['labels'][0]}")
    entry = by_id[picked]
    query: Query = entry["query"]

    left, right = st.columns([1, 1.25], gap="large")
    with left:
        st.markdown(f"**{query.describe()}**")
        st.caption(f"Used for: {'; '.join(entry['labels'])} · pages: {', '.join(entry['pages']) or '—'}")
        if query.not_applied:
            st.caption(f"Filters not applied (the table has no such column): {', '.join(query.not_applied)}")
        st.code(data.sql(query), language="sql")
        present = [k for k, m in METRICS.items() if m.available(data.store().measures[query.table])]
        if not query.rows and present:
            with st.expander("How the metric columns are derived from the summed measures"):
                st.dataframe(pd.DataFrame([{"Metric column": k, "Metric": METRICS[k].label,
                                            "Calculation": METRICS[k].calculation} for k in present]),
                             hide_index=True, width="stretch")
    with right:
        result = data.run(query, entry["labels"][0])
        st.markdown(f"**Result** · {len(result):,} rows")
        st.dataframe(_dates(result), hide_index=True, width="stretch", height=320)
        _csv_download(data.store(), query, "Download result CSV", f"dl-result-{query.id}")

    rows_query = dataclasses.replace(query, by=(), rows=True)
    n = data.row_count(rows_query)
    st.subheader("Source rows", help="The stored fact rows that this query sums, before any grouping.")
    st.caption(f"{n:,} rows in {query.table} match this query's filters. Showing the first "
               f"{min(n, PREVIEW_ROWS):,}.")
    st.dataframe(_dates(data.source_rows(rows_query, PREVIEW_ROWS)), hide_index=True, width="stretch", height=320)
    capped = f" (first {EXPORT_ROW_CAP:,})" if n > EXPORT_ROW_CAP else ""
    _csv_download(data.store(), rows_query, f"Download source rows CSV{capped}", f"dl-rows-{query.id}")


def _tables(f) -> None:
    names = [*TABLES, *DIMENSION_TABLES]
    table = st.selectbox("Table", names, key="tbl_name",
                         format_func=lambda t: f"{t} · {TABLE_DESCRIPTIONS.get(t, 'dimension table')}")
    store = data.store()
    schema = store.schemas[table].assign(Meaning=lambda d: d["column"].map(COLUMN_TEXT).fillna(""))
    with st.expander(f"Schema · {len(schema)} columns"):
        st.dataframe(schema.rename(columns={"column": "Column", "type": "Type"}), hide_index=True, width="stretch")

    if table in DIMENSION_TABLES:
        df = data.dimension(table)
        search = st.text_input("Search", placeholder="Code, name or city", key="tbl_search")
        if search:
            df = df[df.astype(str).apply(lambda col: col.str.contains(search, case=False)).any(axis=1)]
        st.dataframe(df, hide_index=True, width="stretch", height=420)
        st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), f"flightops_{table}.csv",
                           "text/csv", on_click="ignore", icon=":material/download:")
        return

    months = data.loaded_months()
    labels = [format_month(m, "short") for m in months]
    default = (format_month(f.period.start, "short"), format_month(f.period.end, "short"))
    c1, c2, c3, c4 = st.columns([1.6, 1, 1, 1])
    with c1:
        first, last = st.select_slider("Months", options=labels, value=default, key=f"tbl_months_{table}")
    start, end = months[labels.index(first)], months[labels.index(last)]
    period = Period(start, end)
    filters = {}
    dims = TABLES[table]
    with c2:
        if "carrier" in dims:
            filters["carrier"] = st.multiselect("Carrier", data.options("carrier", period),
                                                default=[f.carrier] if f.carrier else [], key=f"tbl_c_{table}")
    with c3:
        if "origin" in dims:
            filters["origin"] = st.multiselect("Origin", data.options("origin", period),
                                               default=[f.origin] if f.origin else [], key=f"tbl_o_{table}")
    with c4:
        if "dest" in dims:
            filters["dest"] = st.multiselect("Destination", data.options("dest", period),
                                             default=[f.dest] if f.dest else [], key=f"tbl_d_{table}")
        elif "dimension" in dims:
            filters["dimension"] = st.selectbox("Profile", ["hour", "dow"], key="tbl_dim")
    columns = st.multiselect("Columns", list(schema["column"]), default=list(schema["column"]),
                             key=f"tbl_cols_{table}")

    query = Query.build(table, start, end, rows=True, **{k: (v or None) for k, v in filters.items()})
    data.log(query, "Data Explorer: table browse")
    n = data.row_count(query)
    st.caption(f"`{query.id}` · {n:,} rows match. Showing the first {min(n, PREVIEW_ROWS):,}; the CSV export "
               f"includes all matching rows (up to {EXPORT_ROW_CAP:,}).")
    preview = _dates(data.source_rows(query, PREVIEW_ROWS))
    st.dataframe(preview[columns or list(preview.columns)], hide_index=True, width="stretch", height=460)
    with st.expander("SQL"):
        st.code(data.sql(query), language="sql")
    _csv_download(store, query, "Download CSV", f"dl-table-{query.id}")
