"""Synthetic fixtures. Tests never download BTS data."""

from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from flightops.data.measures import MEASURE_NAMES
from flightops.data.schema import REQUIRED_COLUMNS

HEADER = [*REQUIRED_COLUMNS.keys(), "Duplicate"]
# Mirror the real source quirks: a trailing space in one header and a trailing empty column.
RAW_HEADER = [("Operating_Airline " if c == "Operating_Airline" else c) for c in HEADER] + [""]


def flight(**overrides) -> dict:
    base = {
        "FlightDate": "2026-06-15", "Year": "2026", "Month": "6", "DayOfWeek": "1",
        "Marketing_Airline_Network": "AA", "Operating_Airline": "AA",
        "OriginAirportID": "11298", "Origin": "DFW", "OriginCityName": "Dallas/Fort Worth, TX", "OriginState": "TX",
        "DestAirportID": "11292", "Dest": "DEN", "DestCityName": "Denver, CO", "DestState": "CO",
        "CRSDepTime": "0800", "DepDelay": "-2.00", "DepDelayMinutes": "0.00", "DepDel15": "0.00",
        "TaxiOut": "15.00", "TaxiIn": "7.00", "ArrDelay": "-5.00", "ArrDelayMinutes": "0.00", "ArrDel15": "0.00",
        "Cancelled": "0.00", "CancellationCode": "", "Diverted": "0.00",
        "CRSElapsedTime": "130.00", "ActualElapsedTime": "125.00", "Distance": "641.00",
        "CarrierDelay": "", "WeatherDelay": "", "NASDelay": "", "SecurityDelay": "", "LateAircraftDelay": "",
        "Duplicate": "N",
    }
    base.update(overrides)
    return base


def delayed(minutes: int, carrier=0, weather=0, nas=0, security=0, late=0, **kw) -> dict:
    return flight(
        ArrDelay=f"{minutes}.00", ArrDelayMinutes=f"{minutes}.00", ArrDel15="1.00",
        DepDelay=f"{minutes}.00", DepDelayMinutes=f"{minutes}.00", DepDel15="1.00",
        CarrierDelay=str(carrier), WeatherDelay=str(weather), NASDelay=str(nas),
        SecurityDelay=str(security), LateAircraftDelay=str(late), **kw,
    )


def cancelled(code="A", **kw) -> dict:
    return flight(
        Cancelled="1.00", CancellationCode=code, DepDelay="", DepDelayMinutes="", DepDel15="",
        TaxiOut="", TaxiIn="", ArrDelay="", ArrDelayMinutes="", ArrDel15="", ActualElapsedTime="", **kw,
    )


def diverted(**kw) -> dict:
    return flight(Diverted="1.00", ArrDelay="", ArrDelayMinutes="", ArrDel15="", ActualElapsedTime="", **kw)


SAMPLE_ROWS = [
    flight(),
    flight(CRSDepTime="1745", Origin="DFW", Dest="ORD", DestAirportID="13930"),
    delayed(20, carrier=5, late=15),
    delayed(75, nas=30, late=45, CRSDepTime="1900"),
    delayed(16, weather=16, Marketing_Airline_Network="DL", Operating_Airline="DL"),
    cancelled("B"),
    diverted(),
    flight(Duplicate="Y"),  # code-share duplicate: excluded
    flight(FlightDate="2026-07-01"),  # outside the file month: excluded
    flight(Marketing_Airline_Network="ZZ", Operating_Airline="ZZ"),  # unexpected carrier: loaded, flagged
]


def write_bts_zip(path: Path, rows: list[dict]) -> Path:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL)
    writer.writerow(RAW_HEADER)
    for row in rows:
        writer.writerow([row.get(c, "") for c in HEADER] + [""])
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("On_Time_Test_2026_6.csv", buffer.getvalue())
    return path


@pytest.fixture
def bts_zip(tmp_path: Path) -> Path:
    return write_bts_zip(tmp_path / "bts_2026_6.zip", SAMPLE_ROWS)


def measures_row(**values) -> dict:
    row = dict.fromkeys(MEASURE_NAMES, 0)
    row.update(values)
    return row


def monthly_frame(entity_col: str, rows: list[tuple]) -> pd.DataFrame:
    """rows: (entity, 'YYYY-MM', flights, arr_eligible, arr_del15, cancelled)."""
    records = []
    for entity, month, flights, eligible, del15, canc in rows:
        rec = measures_row(flights=flights, arr_eligible=eligible, arr_del15=del15, cancelled=canc)
        rec.update({entity_col: entity, "month": dt.date.fromisoformat(month + "-01")})
        records.append(rec)
    return pd.DataFrame(records)
