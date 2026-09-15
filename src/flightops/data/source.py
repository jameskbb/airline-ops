"""BTS TranStats PREZIP discovery and download."""

from __future__ import annotations

import datetime as dt
import re
import time
import zipfile
from pathlib import Path

import requests

from flightops.config import BTS_FILE_TEMPLATE, BTS_PREZIP_URL

USER_AGENT = "flightops-intelligence/1.0 (+https://github.com/jameskbb/airline-ops)"
_FILE_RE = re.compile(
    r"On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_(\d{4})_(\d{1,2})\.zip",
    re.IGNORECASE,
)


def file_name(month: dt.date) -> str:
    return BTS_FILE_TEMPLATE.format(year=month.year, month=month.month)


def file_url(month: dt.date) -> str:
    return BTS_PREZIP_URL + file_name(month)


def parse_index(html: str) -> list[dt.date]:
    """Extract available reporting months from the PREZIP directory listing."""
    months = {dt.date(int(y), int(m), 1) for y, m in _FILE_RE.findall(html)}
    return sorted(months)


def list_available_months(timeout: int = 60) -> list[dt.date]:
    """Months currently published in the PREZIP directory."""
    response = requests.get(BTS_PREZIP_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    months = parse_index(response.text)
    if not months:
        raise RuntimeError("No Marketing Carrier On-Time files found in the BTS PREZIP listing")
    return months


def download_month(month: dt.date, raw_dir: Path, retries: int = 3, timeout: int = 600) -> Path:
    """Download one monthly ZIP into ``raw_dir``; reuse a valid cached copy."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / file_name(month)
    if target.exists() and _is_valid_zip(target):
        return target

    partial = target.with_suffix(".zip.part")
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with requests.get(
                file_url(month), headers={"User-Agent": USER_AGENT}, stream=True, timeout=timeout
            ) as response:
                response.raise_for_status()
                with partial.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1 << 20):
                        handle.write(chunk)
            if not _is_valid_zip(partial):
                raise RuntimeError(f"Downloaded file for {month:%Y-%m} is not a valid ZIP")
            partial.replace(target)
            return target
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            time.sleep(2 * attempt)
    partial.unlink(missing_ok=True)
    raise RuntimeError(f"Failed to download {file_url(month)} after {retries} attempts: {last_error}")


def _is_valid_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return any(name.lower().endswith(".csv") for name in archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
