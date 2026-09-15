"""Project-wide paths and analytical thresholds.

Thresholds live here, not scattered across pages, so the Methodology page and the
signal engine read the same numbers the charts use.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("FLIGHTOPS_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
METADATA_DIR = DATA_DIR / "metadata"
REFERENCE_DIR = DATA_DIR / "reference"

# --- Source -----------------------------------------------------------------
BTS_PREZIP_URL = "https://transtats.bts.gov/PREZIP/"
BTS_FILE_TEMPLATE = "On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_{year}_{month}.zip"
BTS_FIRST_MONTH = "2018-01"
BTS_MASTER_COORD_URL = (
    "https://www.transtats.bts.gov/DL_SelectFields.aspx?"
    "gnoyr_VQ=FLL&QO_fu146_anzr=N8vn6v10%20f722146%20gnoyr5"
)
SOURCE_LABEL = "U.S. DOT Bureau of Transportation Statistics"
DEFAULT_HISTORY_MONTHS = 36

# --- Analytical thresholds ----------------------------------------------------
ON_TIME_THRESHOLD_MIN = 15  # DOT convention: arrival < 15 min after schedule is on time
SEVERE_DELAY_THRESHOLD_MIN = 60

# Minimum monthly-equivalent volumes before an entity may be ranked.
MIN_AIRPORT_DEPARTURES_PER_MONTH = 1_000
MIN_ROUTE_FLIGHTS_PER_MONTH = 90  # ~3 departures per day
MIN_CARRIER_FLIGHTS_PER_MONTH = 3_000
MIN_CARRIER_AIRPORT_FLIGHTS_PER_MONTH = 150

# Map and ranking presentation limits.
MAP_TOP_AIRPORTS = 90
MAP_TOP_ROUTES = 160
HEATMAP_TOP_AIRPORTS = 25
