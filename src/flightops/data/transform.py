"""Transform one BTS monthly file into typed staging rows and monthly fact partitions.

Each reporting month is processed independently and written as its own Parquet
partition per fact table (``<table>/YYYY-MM.parquet``). Re-running a month
overwrites exactly that month, which is what makes the refresh idempotent.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb

from flightops.data.measures import (
    DAILY_MEASURE_NAMES,
    HOURLY_MEASURE_NAMES,
    PROFILE_MEASURE_NAMES,
    aggregate_sql,
)
from flightops.data.months import month_key
from flightops.data.reference import CARRIERS
from flightops.data.schema import SchemaError, normalize_header, validate_header

FACT_TABLES = ("fact_origin_daily", "fact_route_monthly", "fact_origin_hourly", "fact_route_profile")


@dataclass
class MonthReport:
    """Data-quality record for one processed month (persisted as JSON)."""

    month: str
    source_file: str
    source_bytes: int
    raw_rows: int = 0
    duplicate_rows: int = 0
    invalid_date_rows: int = 0
    out_of_month_rows: int = 0
    invalid_airport_rows: int = 0
    loaded_rows: int = 0
    cancelled: int = 0
    diverted: int = 0
    invalid_dep_hour: int = 0
    null_arr_flag_completed: int = 0
    null_taxi_out_departed: int = 0
    cancelled_without_code: int = 0
    cause_rows: int = 0
    cause_reconciled_rows: int = 0
    carriers: dict[str, int] = field(default_factory=dict)
    unexpected_carriers: dict[str, int] = field(default_factory=dict)
    extra_columns: list[str] = field(default_factory=list)
    table_rows: dict[str, int] = field(default_factory=dict)
    processed_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def read_header(zip_path: Path) -> tuple[str, list[str]]:
    with zipfile.ZipFile(zip_path) as archive:
        member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
        with archive.open(member) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace"))
            return member, next(reader)


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _column_names(header: list[str]) -> list[str]:
    names = normalize_header(header)
    # BTS rows end with a trailing comma, producing an unnamed final column.
    return [name or f"_unnamed_{i}" for i, name in enumerate(names)]


STAGING_SQL = """
CREATE OR REPLACE TEMP TABLE stg AS
SELECT
    TRY_CAST(FlightDate AS DATE)                                    AS flight_date,
    TRY_CAST("Year" AS INTEGER)                                     AS src_year,
    TRY_CAST("Month" AS INTEGER)                                    AS src_month,
    TRY_CAST(DayOfWeek AS TINYINT)                                  AS dow,
    upper(trim(Marketing_Airline_Network))                          AS carrier,
    upper(trim(Operating_Airline))                                  AS operating_carrier,
    TRY_CAST(OriginAirportID AS INTEGER)                            AS origin_id,
    upper(trim(Origin))                                             AS origin,
    TRY_CAST(DestAirportID AS INTEGER)                              AS dest_id,
    upper(trim(Dest))                                               AS dest,
    CASE WHEN TRY_CAST(CRSDepTime AS INTEGER) BETWEEN 0 AND 2400
         THEN CAST((TRY_CAST(CRSDepTime AS INTEGER) // 100) % 24 AS TINYINT) END AS dep_hour,
    CAST(coalesce(TRY_CAST(Cancelled AS DOUBLE), 0) AS TINYINT)     AS cancelled,
    nullif(trim(CancellationCode), '')                              AS cancel_code,
    CAST(coalesce(TRY_CAST(Diverted AS DOUBLE), 0) AS TINYINT)      AS diverted,
    TRY_CAST(DepDelay AS DOUBLE)                                    AS dep_delay,
    TRY_CAST(DepDelayMinutes AS DOUBLE)                             AS dep_delay_min,
    CAST(TRY_CAST(DepDel15 AS DOUBLE) AS TINYINT)                   AS dep_del15,
    TRY_CAST(TaxiOut AS DOUBLE)                                     AS taxi_out,
    TRY_CAST(ArrDelay AS DOUBLE)                                    AS arr_delay,
    TRY_CAST(ArrDelayMinutes AS DOUBLE)                             AS arr_delay_min,
    CAST(TRY_CAST(ArrDel15 AS DOUBLE) AS TINYINT)                   AS arr_del15,
    TRY_CAST(CRSElapsedTime AS DOUBLE)                              AS crs_elapsed,
    TRY_CAST(ActualElapsedTime AS DOUBLE)                           AS actual_elapsed,
    TRY_CAST(Distance AS DOUBLE)                                    AS distance,
    TRY_CAST(CarrierDelay AS DOUBLE)                                AS delay_carrier,
    TRY_CAST(WeatherDelay AS DOUBLE)                                AS delay_weather,
    TRY_CAST(NASDelay AS DOUBLE)                                    AS delay_nas,
    TRY_CAST(SecurityDelay AS DOUBLE)                               AS delay_security,
    TRY_CAST(LateAircraftDelay AS DOUBLE)                           AS delay_late_aircraft,
    {duplicate_expr}                                                AS is_duplicate
FROM raw
"""


def stage_csv(con: duckdb.DuckDBPyConnection, csv_path: Path, header: list[str]) -> None:
    """Load a raw BTS CSV into the typed ``stg`` temp table."""
    names = _column_names(header)
    names_sql = "[" + ", ".join(_sql_str(n) for n in names) + "]"
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW raw AS SELECT * FROM read_csv({_sql_str(csv_path.as_posix())}, "
        f"header = true, all_varchar = true, names = {names_sql}, quote = '\"')"
    )
    duplicate_expr = "coalesce(upper(trim(Duplicate)) = 'Y', false)" if "Duplicate" in names else "false"
    con.execute(STAGING_SQL.format(duplicate_expr=duplicate_expr))


def build_quality_report(con: duckdb.DuckDBPyConnection, month: dt.date, report: MonthReport) -> None:
    """Profile ``stg`` and define the ``clean`` view used by the fact builders."""
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW clean AS
        SELECT *, CAST(date_trunc('month', flight_date) AS DATE) AS month
        FROM stg
        WHERE NOT is_duplicate
          AND flight_date IS NOT NULL
          AND date_trunc('month', flight_date) = DATE '{month.isoformat()}'
          AND origin IS NOT NULL AND dest IS NOT NULL AND carrier IS NOT NULL
        """
    )
    row = con.execute(
        f"""
        SELECT
            count(*),
            count_if(is_duplicate),
            count_if(NOT is_duplicate AND flight_date IS NULL),
            count_if(NOT is_duplicate AND flight_date IS NOT NULL
                     AND date_trunc('month', flight_date) <> DATE '{month.isoformat()}'),
            count_if(NOT is_duplicate AND (origin IS NULL OR dest IS NULL OR carrier IS NULL))
        FROM stg
        """
    ).fetchone()
    (report.raw_rows, report.duplicate_rows, report.invalid_date_rows, report.out_of_month_rows,
     report.invalid_airport_rows) = row

    row = con.execute(
        """
        SELECT
            count(*),
            sum(cancelled),
            sum(diverted),
            count_if(dep_hour IS NULL),
            count_if(cancelled = 0 AND diverted = 0 AND arr_del15 IS NULL),
            count_if(cancelled = 0 AND taxi_out IS NULL),
            count_if(cancelled = 1 AND cancel_code IS NULL),
            count(delay_carrier),
            count_if(delay_carrier IS NOT NULL AND abs(
                coalesce(delay_carrier, 0) + coalesce(delay_weather, 0) + coalesce(delay_nas, 0)
                + coalesce(delay_security, 0) + coalesce(delay_late_aircraft, 0) - arr_delay_min) <= 1)
        FROM clean
        """
    ).fetchone()
    (report.loaded_rows, report.cancelled, report.diverted, report.invalid_dep_hour,
     report.null_arr_flag_completed, report.null_taxi_out_departed, report.cancelled_without_code,
     report.cause_rows, report.cause_reconciled_rows) = (int(v or 0) for v in row)

    carriers = con.execute("SELECT carrier, count(*) FROM clean GROUP BY 1 ORDER BY 2 DESC").fetchall()
    report.carriers = {code: int(n) for code, n in carriers}
    report.unexpected_carriers = {code: n for code, n in report.carriers.items() if code not in CARRIERS}


def write_facts(con: duckdb.DuckDBPyConnection, month: dt.date, processed_dir: Path) -> dict[str, int]:
    """Write every fact partition for ``month`` from the ``clean`` view."""
    key = month_key(month)
    daily = aggregate_sql(DAILY_MEASURE_NAMES)
    full = aggregate_sql()
    hourly = aggregate_sql(HOURLY_MEASURE_NAMES)
    profile = aggregate_sql(PROFILE_MEASURE_NAMES)
    queries = {
        # Departure-side airport x marketing carrier x day: trends, day-of-week, KPIs.
        "fact_origin_daily": f"""
            SELECT flight_date, month, carrier, origin, any_value(origin_id) AS origin_id,
                   {daily}
            FROM clean GROUP BY flight_date, month, carrier, origin
            ORDER BY carrier, origin, flight_date""",
        # Directional route x marketing carrier x month: routes, destinations, block time.
        "fact_route_monthly": f"""
            SELECT month, carrier, origin, any_value(origin_id) AS origin_id,
                   dest, any_value(dest_id) AS dest_id,
                   {full}
            FROM clean GROUP BY month, carrier, origin, dest
            ORDER BY carrier, origin, dest""",
        # Airport x carrier x scheduled departure hour x month: operating-day heatmaps.
        "fact_origin_hourly": f"""
            SELECT month, carrier, origin, coalesce(dep_hour, -1)::TINYINT AS dep_hour,
                   {hourly}
            FROM clean GROUP BY month, carrier, origin, coalesce(dep_hour, -1)
            ORDER BY carrier, origin, dep_hour""",
        # Route (all carriers) x hour / day-of-week x month: route operating profile.
        "fact_route_profile": f"""
            SELECT month, origin, dest, 'hour' AS dimension,
                   coalesce(dep_hour, -1)::TINYINT AS bucket, {profile}
            FROM clean GROUP BY month, origin, dest, coalesce(dep_hour, -1)
            UNION ALL
            SELECT month, origin, dest, 'dow' AS dimension, dow::TINYINT AS bucket, {profile}
            FROM clean GROUP BY month, origin, dest, dow
            ORDER BY dimension, origin, dest, bucket""",
    }
    counts: dict[str, int] = {}
    for table, query in queries.items():
        out_dir = processed_dir / table
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{key}.parquet"
        tmp = out_dir / f".{key}.parquet.tmp"
        con.execute(
            f"COPY ({query}) TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD, COMPRESSION_LEVEL 9)"
        )
        tmp.replace(target)
        counts[table] = con.execute(f"SELECT count(*) FROM '{target.as_posix()}'").fetchone()[0]
    return counts


def process_month(zip_path: Path, month: dt.date, processed_dir: Path) -> MonthReport:
    """Validate, stage and aggregate one BTS monthly file. Returns its DQ report."""
    member, header = read_header(zip_path)
    extra = validate_header(header, zip_path.name)
    report = MonthReport(
        month=month_key(month),
        source_file=zip_path.name,
        source_bytes=zip_path.stat().st_size,
        extra_columns=extra,
    )

    workdir = Path(tempfile.mkdtemp(prefix="flightops_"))
    try:
        csv_path = workdir / "month.csv"
        with zipfile.ZipFile(zip_path) as archive, archive.open(member) as src, csv_path.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1 << 22)

        con = duckdb.connect()
        con.execute(f"SET temp_directory = '{workdir.as_posix()}'")
        stage_csv(con, csv_path, header)
        build_quality_report(con, month, report)
        if report.loaded_rows == 0:
            raise SchemaError(f"{zip_path.name}: no valid rows for {month_key(month)} after validation")
        report.table_rows = write_facts(con, month, processed_dir)
        con.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    report.processed_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    return report
