"""Query specifications.

Every read against the analytical layer is a :class:`Query`: a table, a month
window, filters and an optional group-by. A query is an immutable value with a
stable ID, so the app can cache it, log it, show its SQL, and re-run it in the
Data Explorer. The SQL shown to users is the SQL that runs.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

# Dimension columns of each fact table. Everything else in a fact table is an additive measure.
TABLES: dict[str, tuple[str, ...]] = {
    "fact_origin_daily": ("flight_date", "month", "carrier", "origin", "origin_id"),
    "fact_route_monthly": ("month", "carrier", "origin", "origin_id", "dest", "dest_id"),
    "fact_origin_hourly": ("month", "carrier", "origin", "dep_hour"),
    "fact_route_profile": ("month", "origin", "dest", "dimension", "bucket"),
}

TABLE_DESCRIPTIONS: dict[str, str] = {
    "fact_origin_daily": "One row per flight date × marketing carrier × origin airport",
    "fact_route_monthly": "One row per month × marketing carrier × origin × destination",
    "fact_origin_hourly": "One row per month × marketing carrier × origin × scheduled departure hour",
    "fact_route_profile": "One row per month × origin × destination × (departure hour | day of week)",
}

# Grouping columns computed from stored columns: name -> (required column, SQL expression).
DERIVED: dict[str, tuple[str, str]] = {
    "dow": ("flight_date", "isodow(flight_date)"),
    "market": ("dest", "least(origin, dest) || '–' || greatest(origin, dest)"),
    "route": ("dest", "origin || '→' || dest"),
}


def supports(table: str, column: str) -> bool:
    if column in DERIVED:
        return DERIVED[column][0] in TABLES[table]
    return column in TABLES[table]


def _expr(column: str) -> str:
    return DERIVED[column][1] if column in DERIVED else column


@dataclass(frozen=True)
class Query:
    table: str
    start: dt.date  # first month (inclusive), month start
    end: dt.date  # last month (inclusive), month start
    filters: tuple[tuple[str, object], ...] = ()
    by: tuple[str, ...] = ()
    rows: bool = False  # True: return source rows instead of summed measures
    not_applied: tuple[str, ...] = ()  # requested filters the table has no column for

    @classmethod
    def build(cls, table: str, start: dt.date, end: dt.date, by: tuple[str, ...] = (), rows: bool = False,
              **filters) -> Query:
        """Create a query. ``None`` filters mean "all"; lists become IN filters.

        Filters on columns the table does not have are kept in ``not_applied`` so
        the UI can say which filters a view ignores.
        """
        if table not in TABLES:
            raise ValueError(f"Unknown table {table}")
        for column in by:
            if not supports(table, column):
                raise ValueError(f"{table} cannot be grouped by {column}")
        applied, skipped = [], []
        for column, value in sorted(filters.items()):
            if value is None:
                continue
            if not supports(table, column):
                skipped.append(column)
                continue
            applied.append((column, tuple(value) if isinstance(value, list | tuple | set) else value))
        return cls(table, start, end, tuple(applied), tuple(by), rows, tuple(skipped))

    @property
    def id(self) -> str:
        digest = hashlib.sha1(repr((self.table, self.start, self.end, self.filters, self.by, self.rows)).encode())
        return "Q-" + digest.hexdigest()[:8]

    def describe(self) -> str:
        period = self.start.strftime("%b %Y") if self.start == self.end else (
            f"{self.start:%b %Y} – {self.end:%b %Y}")
        parts = [self.table, period]
        parts += [f"{c} = {', '.join(map(str, v)) if isinstance(v, tuple) else v}" for c, v in self.filters]
        if self.by:
            parts.append("by " + ", ".join(self.by))
        if self.rows:
            parts.append("source rows")
        return " · ".join(parts)

    def sql(self, measures: list[str]) -> tuple[str, list]:
        """Parameterized SQL and its parameters."""
        where, params = ["month BETWEEN ? AND ?"], [self.start, self.end]
        for column, value in self.filters:
            if isinstance(value, tuple):
                where.append(f"{_expr(column)} IN ({', '.join('?' for _ in value)})" if value else "false")
                params.extend(value)
            else:
                where.append(f"{_expr(column)} = ?")
                params.append(value)
        where_sql = "\n  AND ".join(where)
        if self.rows:
            order = ", ".join(c for c in TABLES[self.table] if not c.endswith("_id"))
            return f"SELECT *\nFROM {self.table}\nWHERE {where_sql}\nORDER BY {order}", params
        dims = [f"{_expr(c)} AS {c}" for c in self.by]
        sums = [f"sum({m}) AS {m}" for m in measures]
        select = ",\n  ".join(dims + sums)
        sql = f"SELECT\n  {select}\nFROM {self.table}\nWHERE {where_sql}"
        if self.by:
            positions = ", ".join(str(i + 1) for i in range(len(self.by)))
            sql += f"\nGROUP BY {positions}\nORDER BY {positions}"
        return sql, params

    def display_sql(self, measures: list[str]) -> str:
        """SQL with parameters inlined as literals, for reading and copying."""
        sql, params = self.sql(measures)
        for value in params:
            literal = f"DATE '{value.isoformat()}'" if isinstance(value, dt.date) else (
                str(value) if isinstance(value, int | float) else "'" + str(value).replace("'", "''") + "'")
            sql = sql.replace("?", literal, 1)
        return sql
