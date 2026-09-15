"""End-to-end sync: discover, download, validate, transform, reconcile, publish metadata."""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from flightops.config import (
    BTS_FIRST_MONTH,
    METADATA_DIR,
    PROCESSED_DIR,
    RAW_DIR,
    REFERENCE_DIR,
    SOURCE_LABEL,
)
from flightops.data.months import month_key, month_range, parse_month
from flightops.data.reference import build_dim_airport, carrier_rows, download_master_coordinate
from flightops.data.source import download_month, list_available_months
from flightops.data.transform import FACT_TABLES, process_month

MASTER_COORD_MAX_AGE_DAYS = 30


class ReconciliationError(RuntimeError):
    """Raised when fact tables disagree about flight counts for a month."""


@dataclass
class SyncPlan:
    requested: list[dt.date]
    available: list[dt.date]
    to_process: list[dt.date] = field(default_factory=list)
    already_loaded: list[dt.date] = field(default_factory=list)
    unpublished: list[dt.date] = field(default_factory=list)
    to_prune: list[str] = field(default_factory=list)


def resolve_window(
    available: list[dt.date], months: int | None = None, start: str | None = None, end: str | None = None
) -> list[dt.date]:
    """Resolve the requested reporting window.

    ``--start/--end`` take precedence; otherwise the window is the latest ``months``
    published months. An open ``--end`` defaults to the latest published month.
    """
    if not available:
        return []
    latest = available[-1]
    end_month = parse_month(end) if end else latest
    if start:
        start_month = parse_month(start)
    else:
        count = months or 1
        start_month = month_range(parse_month(BTS_FIRST_MONTH), end_month)[-count:][0]
    start_month = max(start_month, parse_month(BTS_FIRST_MONTH))
    return month_range(start_month, end_month)


def loaded_months(processed_dir: Path = PROCESSED_DIR, metadata_dir: Path = METADATA_DIR) -> set[str]:
    """Months with a DQ report and a partition in every fact table."""
    reports = {p.stem for p in (metadata_dir / "months").glob("*.json")}
    for table in FACT_TABLES:
        reports &= {p.stem for p in (processed_dir / table).glob("*.parquet")}
    return reports


def plan_sync(
    requested: list[dt.date], available: list[dt.date], force: bool, prune: bool,
    processed_dir: Path = PROCESSED_DIR, metadata_dir: Path = METADATA_DIR,
) -> SyncPlan:
    plan = SyncPlan(requested=requested, available=available)
    published = set(available)
    have = loaded_months(processed_dir, metadata_dir)
    for month in requested:
        if month not in published:
            plan.unpublished.append(month)
        elif month_key(month) in have and not force:
            plan.already_loaded.append(month)
        else:
            plan.to_process.append(month)
    if prune:
        keep = {month_key(m) for m in requested}
        existing = {p.stem for p in (metadata_dir / "months").glob("*.json")}
        for table in FACT_TABLES:
            existing |= {p.stem for p in (processed_dir / table).glob("*.parquet")}
        plan.to_prune = sorted(existing - keep)
    return plan


def prune_months(keys: list[str], processed_dir: Path = PROCESSED_DIR, metadata_dir: Path = METADATA_DIR) -> None:
    for key in keys:
        for table in FACT_TABLES:
            (processed_dir / table / f"{key}.parquet").unlink(missing_ok=True)
        (metadata_dir / "months" / f"{key}.json").unlink(missing_ok=True)


def reconcile(processed_dir: Path = PROCESSED_DIR, metadata_dir: Path = METADATA_DIR) -> dict[str, int]:
    """Verify every fact table carries the same flight count per month as the DQ report."""
    con = duckdb.connect()
    totals: dict[str, dict[str, int]] = {}
    for table in FACT_TABLES:
        glob = (processed_dir / table / "*.parquet").as_posix()
        where = "WHERE dimension = 'hour'" if table == "fact_route_profile" else ""
        rows = con.execute(
            f"SELECT strftime(month, '%Y-%m'), sum(flights) FROM read_parquet('{glob}') {where} GROUP BY 1"
        ).fetchall()
        totals[table] = {k: int(v) for k, v in rows}
    expected = {}
    for path in sorted((metadata_dir / "months").glob("*.json")):
        expected[path.stem] = json.loads(path.read_text())["loaded_rows"]
    problems = []
    for key, loaded in expected.items():
        for table in FACT_TABLES:
            got = totals[table].get(key)
            if got != loaded:
                problems.append(f"{key} {table}: {got} flights vs {loaded} loaded rows")
    if problems:
        raise ReconciliationError("Fact reconciliation failed:\n  " + "\n  ".join(problems))
    return expected


def ensure_master_coordinate(reference_dir: Path = REFERENCE_DIR, offline: bool = False) -> Path:
    path = reference_dir / "master_coordinate.csv"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < MASTER_COORD_MAX_AGE_DAYS * 86400
    if fresh or (offline and path.exists()):
        return path
    if offline:
        raise RuntimeError("Master Coordinate table not cached and --offline was given")
    return download_master_coordinate(path)


def build_dimensions(processed_dir: Path, master_csv: Path) -> dict[str, int]:
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW fact_origin_daily AS SELECT * FROM read_parquet('{(processed_dir / 'fact_origin_daily' / '*.parquet').as_posix()}')"
    )
    con.execute(
        f"CREATE VIEW fact_route_monthly AS SELECT * FROM read_parquet('{(processed_dir / 'fact_route_monthly' / '*.parquet').as_posix()}')"
    )
    airports = build_dim_airport(con, master_csv, processed_dir / "dim_airport.parquet")
    con.execute("CREATE TABLE dim_carrier (carrier VARCHAR, carrier_name VARCHAR, carrier_short VARCHAR)")
    con.executemany("INSERT INTO dim_carrier VALUES (?, ?, ?)", carrier_rows())
    con.execute(
        f"COPY dim_carrier TO '{(processed_dir / 'dim_carrier.parquet').as_posix()}' (FORMAT PARQUET)"
    )
    missing_coords = con.execute(
        f"SELECT count(*) FROM '{(processed_dir / 'dim_airport.parquet').as_posix()}' WHERE latitude IS NULL"
    ).fetchone()[0]
    return {"dim_airport": airports, "dim_airport_missing_coordinates": missing_coords}


def write_metadata(
    available: list[dt.date], requested: list[dt.date], dims: dict[str, int],
    processed_dir: Path = PROCESSED_DIR, metadata_dir: Path = METADATA_DIR,
) -> dict:
    reports = [json.loads(p.read_text()) for p in sorted((metadata_dir / "months").glob("*.json"))]
    loaded = [r["month"] for r in reports]
    requested_keys = [month_key(m) for m in requested]
    sizes = {
        table: sum(p.stat().st_size for p in (processed_dir / table).glob("*.parquet"))
        for table in FACT_TABLES
    }
    rows = {table: sum(r["table_rows"].get(table, 0) for r in reports) for table in FACT_TABLES}
    metadata = {
        "source": SOURCE_LABEL,
        "dataset": "Marketing Carrier On-Time Performance (Beginning January 2018)",
        "source_url": "https://www.transtats.bts.gov/Tables.asp?QO_VQ=EFD&QO_anzr=Nv4yv0r",
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "coverage_start": loaded[0] if loaded else None,
        "coverage_end": loaded[-1] if loaded else None,
        "months_loaded": loaded,
        "months_requested_missing": [k for k in requested_keys if k not in loaded],
        "latest_published_month": month_key(available[-1]) if available else None,
        "raw_rows_processed": sum(r["raw_rows"] for r in reports),
        "flights_loaded": sum(r["loaded_rows"] for r in reports),
        "table_rows": rows,
        "table_bytes": sizes,
        "dimensions": dims,
    }
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def run_sync(
    months: int | None = None,
    start: str | None = None,
    end: str | None = None,
    force: bool = False,
    prune: bool = True,
    offline: bool = False,
    purge_raw: bool = False,
    raw_dir: Path = RAW_DIR,
    processed_dir: Path = PROCESSED_DIR,
    metadata_dir: Path = METADATA_DIR,
    reference_dir: Path = REFERENCE_DIR,
    log=print,
) -> dict:
    """Run the full sync. Safe to re-run: loaded months are skipped unless ``force``."""
    if offline:
        from flightops.data.source import parse_index  # noqa: F401 - offline uses the raw cache listing

        available = sorted(
            {parse_month(f"{p.stem.split('_')[-2]}-{p.stem.split('_')[-1]}") for p in raw_dir.glob("*.zip")}
        )
        log(f"Offline mode: {len(available)} cached monthly files")
    else:
        available = list_available_months()
        log(f"BTS PREZIP: {len(available)} published months, latest {month_key(available[-1])}")

    requested = resolve_window(available, months=months, start=start, end=end)
    if not requested:
        raise RuntimeError("Requested window is empty")
    plan = plan_sync(requested, available, force, prune, processed_dir, metadata_dir)
    log(
        f"Window {month_key(requested[0])} → {month_key(requested[-1])}: "
        f"{len(plan.to_process)} to process, {len(plan.already_loaded)} already loaded, "
        f"{len(plan.unpublished)} not yet published, {len(plan.to_prune)} to prune"
    )
    if plan.unpublished:
        log("  Not yet published by BTS: " + ", ".join(month_key(m) for m in plan.unpublished))

    (metadata_dir / "months").mkdir(parents=True, exist_ok=True)
    for index, month in enumerate(plan.to_process, start=1):
        started = time.time()
        zip_path = download_month(month, raw_dir) if not offline else raw_dir / _raw_name(month)
        report = process_month(zip_path, month, processed_dir)
        (metadata_dir / "months" / f"{report.month}.json").write_text(json.dumps(report.to_dict(), indent=2) + "\n")
        flags = []
        if report.unexpected_carriers:
            flags.append(f"unexpected carriers {report.unexpected_carriers}")
        if report.extra_columns:
            flags.append(f"new columns {report.extra_columns}")
        log(
            f"  [{index}/{len(plan.to_process)}] {report.month}: {report.raw_rows:,} raw → "
            f"{report.loaded_rows:,} flights ({time.time() - started:.0f}s)"
            + (f"  ⚠ {'; '.join(flags)}" if flags else "")
        )
        if purge_raw:
            zip_path.unlink(missing_ok=True)

    if plan.to_prune:
        prune_months(plan.to_prune, processed_dir, metadata_dir)
        log("  Pruned months outside window: " + ", ".join(plan.to_prune))

    expected = reconcile(processed_dir, metadata_dir)
    log(f"Reconciliation OK across {len(FACT_TABLES)} fact tables for {len(expected)} months")

    master = ensure_master_coordinate(reference_dir, offline=offline)
    dims = build_dimensions(processed_dir, master)
    log(f"Dimensions: {dims['dim_airport']} airports ({dims['dim_airport_missing_coordinates']} without coordinates)")

    metadata = write_metadata(available, requested, dims, processed_dir, metadata_dir)
    total_mb = sum(metadata["table_bytes"].values()) / 1e6
    log(
        f"Done. Coverage {metadata['coverage_start']} → {metadata['coverage_end']}, "
        f"{metadata['flights_loaded']:,} flights, processed facts {total_mb:.1f} MB"
    )
    return metadata


def _raw_name(month: dt.date) -> str:
    from flightops.data.source import file_name

    return file_name(month)
