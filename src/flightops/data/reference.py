"""Reference dimensions: marketing carriers and airports (BTS Master Coordinate)."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import duckdb
import requests

from flightops.config import BTS_MASTER_COORD_URL

# Marketing carrier networks that report under the Marketing Carrier On-Time
# Performance dataset since 2018. Codes seen in a file but missing here are
# surfaced as a data-quality exception rather than silently renamed.
CARRIERS: dict[str, dict[str, str]] = {
    "AA": {"name": "American Airlines", "short": "American"},
    "AS": {"name": "Alaska Airlines", "short": "Alaska"},
    "B6": {"name": "JetBlue Airways", "short": "JetBlue"},
    "DL": {"name": "Delta Air Lines", "short": "Delta"},
    "F9": {"name": "Frontier Airlines", "short": "Frontier"},
    "G4": {"name": "Allegiant Air", "short": "Allegiant"},
    "HA": {"name": "Hawaiian Airlines", "short": "Hawaiian"},
    "NK": {"name": "Spirit Airlines", "short": "Spirit"},
    "UA": {"name": "United Airlines", "short": "United"},
    "VX": {"name": "Virgin America", "short": "Virgin America"},
    "WN": {"name": "Southwest Airlines", "short": "Southwest"},
    "MX": {"name": "Breeze Airways", "short": "Breeze"},
    "SY": {"name": "Sun Country Airlines", "short": "Sun Country"},
}

MASTER_COORD_FIELDS = [
    "AIRPORT_SEQ_ID",
    "AIRPORT_ID",
    "AIRPORT",
    "DISPLAY_AIRPORT_NAME",
    "DISPLAY_AIRPORT_CITY_NAME_FULL",
    "AIRPORT_STATE_CODE",
    "AIRPORT_COUNTRY_CODE_ISO",
    "LATITUDE",
    "LONGITUDE",
    "AIRPORT_START_DATE",
    "AIRPORT_THRU_DATE",
    "AIRPORT_IS_CLOSED",
    "AIRPORT_IS_LATEST",
]


def carrier_rows() -> list[tuple[str, str, str]]:
    return [(code, meta["name"], meta["short"]) for code, meta in sorted(CARRIERS.items())]


def download_master_coordinate(dest: Path, timeout: int = 180) -> Path:
    """Download the BTS Aviation Support Tables Master Coordinate table as CSV.

    TranStats exposes this table through an ASP.NET form, so we replay the form
    post with the hidden view-state fields from a fresh GET.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "flightops-intelligence/1.0 (+https://github.com/jameskbb/airline-ops)"
    page = session.get(BTS_MASTER_COORD_URL, timeout=timeout)
    page.raise_for_status()

    def hidden(name: str) -> str:
        match = re.search(rf'id="{name}"\s+value="([^"]*)"', page.text) or re.search(
            rf'name="{name}"[^>]*value="([^"]*)"', page.text
        )
        if not match:
            raise RuntimeError(f"Master Coordinate form field {name} not found; TranStats page changed")
        return match.group(1)

    form = {k: hidden(k) for k in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")}
    form.update({field: "on" for field in MASTER_COORD_FIELDS})
    form.update({"btnDownload": "Download", "chkDownloadZip": "on"})
    response = session.post(BTS_MASTER_COORD_URL, data=form, timeout=timeout)
    response.raise_for_status()
    if not response.content.startswith(b"PK"):
        raise RuntimeError("Master Coordinate download did not return a ZIP archive")

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    member = next((n for n in archive.namelist() if "MASTER_CORD" in n.upper()), None)
    if member is None:
        raise RuntimeError(f"Master Coordinate CSV missing from archive: {archive.namelist()}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(archive.read(member))
    return dest


def build_dim_airport(con: duckdb.DuckDBPyConnection, master_csv: Path, out_path: Path) -> int:
    """Create ``dim_airport`` from the latest Master Coordinate record per airport.

    Airport attributes change over time (renamed terminals, moved coordinates), so
    we keep the row flagged ``AIRPORT_IS_LATEST = 1`` for each ``AIRPORT_ID``. The
    table is restricted to airports that appear in the loaded flight facts.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE master_coord AS
        SELECT * FROM read_csv('{master_csv.as_posix()}', header = true, all_varchar = true)
        """
    )
    con.execute(
        f"""
        COPY (
            WITH latest AS (
                SELECT
                    CAST(AIRPORT_ID AS INTEGER) AS airport_id,
                    AIRPORT AS airport,
                    DISPLAY_AIRPORT_NAME AS airport_name,
                    DISPLAY_AIRPORT_CITY_NAME_FULL AS city,
                    AIRPORT_STATE_CODE AS state,
                    TRY_CAST(LATITUDE AS DOUBLE) AS latitude,
                    TRY_CAST(LONGITUDE AS DOUBLE) AS longitude,
                    ROW_NUMBER() OVER (
                        PARTITION BY AIRPORT_ID
                        ORDER BY (AIRPORT_IS_LATEST = '1') DESC, AIRPORT_SEQ_ID DESC
                    ) AS rn
                FROM master_coord
            ),
            used AS (
                SELECT DISTINCT origin_id AS airport_id, origin AS airport_code FROM fact_origin_daily
                UNION
                SELECT DISTINCT dest_id, dest FROM fact_route_monthly
            )
            SELECT
                u.airport_code AS airport,
                u.airport_id,
                COALESCE(l.airport_name, u.airport_code) AS airport_name,
                l.city,
                l.state,
                l.latitude,
                l.longitude
            FROM used u
            LEFT JOIN latest l ON l.airport_id = u.airport_id AND l.rn = 1
            QUALIFY ROW_NUMBER() OVER (PARTITION BY u.airport_code ORDER BY u.airport_id DESC) = 1
            ORDER BY airport
        ) TO '{out_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    return con.execute(f"SELECT count(*) FROM '{out_path.as_posix()}'").fetchone()[0]
