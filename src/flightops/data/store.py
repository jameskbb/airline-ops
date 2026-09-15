"""DuckDB access to the curated Parquet layer.

One in-memory DuckDB connection holds a view per Parquet table. Each query runs on
its own ``cursor()``, which is DuckDB's rule for sharing a connection across threads.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
import pandas as pd

from flightops.config import METADATA_DIR, PROCESSED_DIR
from flightops.data.measures import MEASURE_NAMES
from flightops.data.query import TABLES, Query
from flightops.metrics.definitions import add_metrics

DIMENSION_TABLES = ("dim_airport", "dim_carrier")


class DataNotAvailableError(RuntimeError):
    """Raised when the processed analytical layer has not been built."""


class Store:
    def __init__(self, processed_dir: Path = PROCESSED_DIR, metadata_dir: Path = METADATA_DIR):
        self.processed_dir = Path(processed_dir)
        self.metadata_dir = Path(metadata_dir)
        self.con = duckdb.connect()
        for table in TABLES:
            if not any((self.processed_dir / table).glob("*.parquet")):
                raise DataNotAvailableError(
                    f"No processed data for {table}. Run `python scripts/sync_bts.py --months 36` first."
                )
            glob = (self.processed_dir / table / "*.parquet").as_posix()
            self.con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{glob}', union_by_name = true)")
        for table in DIMENSION_TABLES:
            path = self.processed_dir / f"{table}.parquet"
            if path.exists():
                self.con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path.as_posix()}')")
        self.schemas = {t: self._describe(t) for t in (*TABLES, *DIMENSION_TABLES) if self._exists(t)}
        self.measures = {t: [c for c in self.schemas[t]["column"] if c in MEASURE_NAMES] for t in TABLES}

    def _exists(self, table: str) -> bool:
        return table in TABLES or (self.processed_dir / f"{table}.parquet").exists()

    def _describe(self, table: str) -> pd.DataFrame:
        df = self.sql_df(f"DESCRIBE {table}")
        return df.rename(columns={"column_name": "column", "column_type": "type"})[["column", "type"]]

    def sql_df(self, sql: str, params: list | None = None) -> pd.DataFrame:
        with self.con.cursor() as cursor:
            return cursor.execute(sql, params or []).df()

    # -- queries ---------------------------------------------------------------
    def run(self, query: Query, limit: int | None = None) -> pd.DataFrame:
        """Execute a query. Aggregates come back with every derivable metric added."""
        sql, params = query.sql(self.measures[query.table])
        if limit:
            sql += f"\nLIMIT {int(limit)}"
        df = self.sql_df(sql, params)
        if query.rows:
            return df
        measures = self.measures[query.table]
        df[measures] = df[measures].fillna(0).astype("int64")
        if not query.by and (df.empty or df.iloc[0]["flights"] == 0):
            df = df.iloc[0:0]
        return add_metrics(df)

    def count(self, query: Query) -> int:
        sql, params = query.sql(self.measures[query.table])
        return int(self.sql_df(f"SELECT count(*) AS n FROM ({sql})", params)["n"].iloc[0])

    # -- dimensions & metadata ------------------------------------------------------
    def loaded_months(self) -> list[dt.date]:
        df = self.sql_df("SELECT DISTINCT month FROM fact_route_monthly ORDER BY 1")
        return [pd.Timestamp(m).date() for m in df["month"]]

    def table(self, name: str) -> pd.DataFrame:
        """A whole dimension table (small)."""
        if name not in DIMENSION_TABLES:
            raise ValueError(f"{name} is not a dimension table")
        return self.sql_df(f"SELECT * FROM {name} ORDER BY 1")

    def metadata(self) -> dict:
        path = self.metadata_dir / "metadata.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def month_reports(self) -> pd.DataFrame:
        return pd.DataFrame([json.loads(p.read_text()) for p in sorted((self.metadata_dir / "months").glob("*.json"))])
