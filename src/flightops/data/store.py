"""DuckDB analytical query layer over the curated Parquet facts.

The store exposes one primitive, :meth:`Store.aggregate`: sum additive measures
over a fact table for a month window, optional dimension filters and a group-by,
then derive every KPI through the metric layer. Pages never write SQL.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Mapping
from pathlib import Path

import duckdb
import pandas as pd

from flightops.config import METADATA_DIR, PROCESSED_DIR
from flightops.data.measures import MEASURE_NAMES
from flightops.metrics.definitions import add_metrics

FACTS = ("fact_origin_daily", "fact_route_monthly", "fact_origin_hourly", "fact_route_profile")

# Derived grouping columns available on tables that have their inputs.
DERIVED = {
    "dow": ("flight_date", "isodow(flight_date)"),
    "market": ("dest", "least(origin, dest) || '–' || greatest(origin, dest)"),
    "route": ("dest", "origin || '→' || dest"),
    "year": ("month", "year(month)"),
}


class DataNotAvailableError(RuntimeError):
    """Raised when the processed analytical layer has not been built."""


class Store:
    def __init__(self, processed_dir: Path = PROCESSED_DIR, metadata_dir: Path = METADATA_DIR):
        self.processed_dir = Path(processed_dir)
        self.metadata_dir = Path(metadata_dir)
        self.con = duckdb.connect(database=":memory:")
        self.columns: dict[str, list[str]] = {}
        for table in FACTS:
            files = sorted((self.processed_dir / table).glob("*.parquet"))
            if not files:
                raise DataNotAvailableError(
                    f"No processed data for {table}. Run `python scripts/sync_bts.py --months 36` first."
                )
            glob = (self.processed_dir / table / "*.parquet").as_posix()
            self.con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{glob}', union_by_name = true)")
            self.columns[table] = [r[0] for r in self.con.execute(f"DESCRIBE {table}").fetchall()]
        for dim in ("dim_airport", "dim_carrier"):
            path = self.processed_dir / f"{dim}.parquet"
            if path.exists():
                self.con.execute(f"CREATE VIEW {dim} AS SELECT * FROM read_parquet('{path.as_posix()}')")

    # -- low level --------------------------------------------------------------
    def query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        with self.con.cursor() as cursor:
            return cursor.execute(sql, params or []).df()

    def measures(self, table: str) -> list[str]:
        return [c for c in self.columns[table] if c in MEASURE_NAMES]

    def supports(self, table: str, column: str) -> bool:
        if column in DERIVED:
            return DERIVED[column][0] in self.columns[table]
        return column in self.columns[table]

    # -- primary primitive ------------------------------------------------------
    def aggregate(
        self,
        table: str,
        start: dt.date,
        end: dt.date,
        filters: Mapping[str, object] | None = None,
        by: Iterable[str] = (),
        metrics: bool = True,
    ) -> pd.DataFrame:
        """Sum measures over ``[start, end]`` (month starts, inclusive).

        ``filters`` maps a column to a scalar (equality), a list/tuple (IN) or
        ``None`` (no filter). Unsupported filter columns raise ``ValueError`` so a
        page can never silently ignore an active filter.
        """
        if table not in FACTS:
            raise ValueError(f"Unknown fact table {table}")
        by = list(by)
        clauses, params = ["month BETWEEN ? AND ?"], [start, end]
        for column, value in (filters or {}).items():
            if value is None:
                continue
            if not self.supports(table, column):
                raise ValueError(f"{table} cannot be filtered by {column}")
            expr = DERIVED[column][1] if column in DERIVED else column
            if isinstance(value, list | tuple | set):
                values = list(value)
                if not values:
                    clauses.append("false")
                    continue
                clauses.append(f"{expr} IN ({', '.join('?' for _ in values)})")
                params.extend(values)
            else:
                clauses.append(f"{expr} = ?")
                params.append(value)

        select_dims = []
        for column in by:
            if not self.supports(table, column):
                raise ValueError(f"{table} cannot be grouped by {column}")
            expr = DERIVED[column][1] if column in DERIVED else column
            select_dims.append(f"{expr} AS {column}")
        measure_sql = ", ".join(f"CAST(sum({m}) AS BIGINT) AS {m}" for m in self.measures(table))
        dims_sql = (", ".join(select_dims) + ", ") if select_dims else ""
        group_sql = f"GROUP BY {', '.join(str(i + 1) for i in range(len(by)))}" if by else ""
        order_sql = f"ORDER BY {', '.join(str(i + 1) for i in range(len(by)))}" if by else ""
        sql = f"SELECT {dims_sql}{measure_sql} FROM {table} WHERE {' AND '.join(clauses)} {group_sql} {order_sql}"
        df = self.query(sql, params)
        if not by and len(df) and pd.isna(df.iloc[0].get("flights")):
            df = df.iloc[0:0]
        df = df.fillna({m: 0 for m in self.measures(table)})
        return add_metrics(df) if metrics else df

    def totals(self, table: str, start: dt.date, end: dt.date, filters: Mapping[str, object] | None = None) -> dict:
        """Single-row aggregate as a dict (empty dict when no rows match)."""
        df = self.aggregate(table, start, end, filters)
        return {} if df.empty or df.iloc[0]["flights"] == 0 else df.iloc[0].to_dict()

    # -- dimensions & metadata ----------------------------------------------------
    def loaded_months(self) -> list[dt.date]:
        df = self.query("SELECT DISTINCT month FROM fact_route_monthly ORDER BY 1")
        return [pd.Timestamp(m).date() for m in df["month"]]

    def airports(self) -> pd.DataFrame:
        return self.query("SELECT * FROM dim_airport ORDER BY airport")

    def carriers(self) -> pd.DataFrame:
        return self.query("SELECT * FROM dim_carrier ORDER BY carrier")

    def distinct(self, table: str, column: str, start: dt.date, end: dt.date,
                 filters: Mapping[str, object] | None = None) -> pd.DataFrame:
        """Distinct values of ``column`` with flight volume, for cascading filters."""
        df = self.aggregate(table, start, end, filters, by=[column], metrics=False)
        return df[[column, "flights"]].sort_values("flights", ascending=False)

    def metadata(self) -> dict:
        path = self.metadata_dir / "metadata.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def month_reports(self) -> pd.DataFrame:
        rows = [json.loads(p.read_text()) for p in sorted((self.metadata_dir / "months").glob("*.json"))]
        return pd.DataFrame(rows)
