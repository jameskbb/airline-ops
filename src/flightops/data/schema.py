"""Source schema contract for the BTS Marketing Carrier On-Time Performance files.

The pipeline validates every monthly file against ``REQUIRED_COLUMNS`` before
transforming it. BTS header names are whitespace-stripped first because the source
currently ships ``"Operating_Airline "`` with a trailing space.
"""

from __future__ import annotations

REQUIRED_COLUMNS: dict[str, str] = {
    "FlightDate": "DATE",
    "Year": "INTEGER",
    "Month": "INTEGER",
    "DayOfWeek": "INTEGER",
    "Marketing_Airline_Network": "VARCHAR",
    "Operating_Airline": "VARCHAR",
    "OriginAirportID": "INTEGER",
    "Origin": "VARCHAR",
    "OriginCityName": "VARCHAR",
    "OriginState": "VARCHAR",
    "DestAirportID": "INTEGER",
    "Dest": "VARCHAR",
    "DestCityName": "VARCHAR",
    "DestState": "VARCHAR",
    "CRSDepTime": "VARCHAR",
    "DepDelay": "DOUBLE",
    "DepDelayMinutes": "DOUBLE",
    "DepDel15": "DOUBLE",
    "TaxiOut": "DOUBLE",
    "TaxiIn": "DOUBLE",
    "ArrDelay": "DOUBLE",
    "ArrDelayMinutes": "DOUBLE",
    "ArrDel15": "DOUBLE",
    "Cancelled": "DOUBLE",
    "CancellationCode": "VARCHAR",
    "Diverted": "DOUBLE",
    "CRSElapsedTime": "DOUBLE",
    "ActualElapsedTime": "DOUBLE",
    "Distance": "DOUBLE",
    "CarrierDelay": "DOUBLE",
    "WeatherDelay": "DOUBLE",
    "NASDelay": "DOUBLE",
    "SecurityDelay": "DOUBLE",
    "LateAircraftDelay": "DOUBLE",
}

# Optional columns: used when present, never required.
OPTIONAL_COLUMNS: dict[str, str] = {
    "Duplicate": "VARCHAR",
    "Flights": "DOUBLE",
}

# Columns present in the source as of 2026 that the pipeline deliberately ignores.
# Anything outside REQUIRED/OPTIONAL/KNOWN_UNUSED (and the Div1..Div5 diversion
# block) is reported as a new source column in the data-quality report.
KNOWN_UNUSED_COLUMNS: frozenset[str] = frozenset(
    {
        "Quarter", "DayofMonth", "Operated_or_Branded_Code_Share_Partners", "DOT_ID_Marketing_Airline",
        "IATA_Code_Marketing_Airline", "Flight_Number_Marketing_Airline",
        "Originally_Scheduled_Code_Share_Airline", "DOT_ID_Originally_Scheduled_Code_Share_Airline",
        "IATA_Code_Originally_Scheduled_Code_Share_Airline",
        "Flight_Num_Originally_Scheduled_Code_Share_Airline", "DOT_ID_Operating_Airline",
        "IATA_Code_Operating_Airline", "Tail_Number", "Flight_Number_Operating_Airline",
        "OriginAirportSeqID", "OriginCityMarketID", "OriginStateFips", "OriginStateName", "OriginWac",
        "DestAirportSeqID", "DestCityMarketID", "DestStateFips", "DestStateName", "DestWac", "DepTime",
        "DepartureDelayGroups", "DepTimeBlk", "WheelsOff", "WheelsOn", "CRSArrTime", "ArrTime",
        "ArrivalDelayGroups", "ArrTimeBlk", "AirTime", "DistanceGroup", "FirstDepTime", "TotalAddGTime",
        "LongestAddGTime", "DivAirportLandings", "DivReachedDest", "DivActualElapsedTime", "DivArrDelay",
        "DivDistance",
    }
)

DELAY_CAUSE_COLUMNS: dict[str, str] = {
    "carrier": "CarrierDelay",
    "weather": "WeatherDelay",
    "nas": "NASDelay",
    "security": "SecurityDelay",
    "late_aircraft": "LateAircraftDelay",
}

CANCELLATION_CODES: dict[str, str] = {
    "A": "carrier",
    "B": "weather",
    "C": "nas",
    "D": "security",
}


class SchemaError(RuntimeError):
    """Raised when a source file no longer matches the expected contract."""


def normalize_header(columns: list[str]) -> list[str]:
    return [c.strip() for c in columns]


def validate_header(columns: list[str], source: str) -> list[str]:
    """Validate a raw header. Returns unexpected extra columns (informational).

    Raises ``SchemaError`` listing every missing required column so a BTS schema
    change fails loudly with an actionable message.
    """
    normalized = [c for c in normalize_header(columns) if c]
    present = set(normalized)
    missing = [c for c in REQUIRED_COLUMNS if c not in present]
    if missing:
        raise SchemaError(
            f"{source}: BTS schema changed. Missing required columns: {', '.join(missing)}. "
            "Update flightops.data.schema.REQUIRED_COLUMNS after reviewing the source readme."
        )
    duplicates = sorted({c for c in normalized if normalized.count(c) > 1})
    if duplicates:
        raise SchemaError(f"{source}: duplicate column names after normalization: {duplicates}")
    known = set(REQUIRED_COLUMNS) | set(OPTIONAL_COLUMNS) | KNOWN_UNUSED_COLUMNS
    return [c for c in normalized if c not in known and not _is_diversion_detail(c)]


def _is_diversion_detail(column: str) -> bool:
    return len(column) > 4 and column.startswith("Div") and column[3].isdigit()
