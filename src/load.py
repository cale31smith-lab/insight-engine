"""
src/load.py

Reads validated Pydantic model lists from either:
  - CSV files on disk (the CLI path, src/run.py), or
  - pandas DataFrames already in memory (the Streamlit column-mapping path,
    after column_mapping.apply_mapping() has renamed columns to match schema.py).

Both paths funnel through the same row-validation core, so a row is
rejected identically no matter which connector it came from — this is
what lets Step 10 (Jobber/QuickBooks) reuse everything below unchanged.

Fails loudly: a single bad row raises a clear, row-numbered error instead
of silently producing NaN/None that would corrupt a metric three stages
later.
"""

import csv
from datetime import date
from pathlib import Path
from typing import Type, TypeVar, Union, get_args, get_origin

import pandas as pd
from dateutil import parser as date_parser
from pydantic import BaseModel, ValidationError

from src.schema import Customer, Invoice, Job, Quote, Technician, TimeEntry

T = TypeVar("T", bound=BaseModel)


class DataLoadError(Exception):
    """Raised when a row fails schema validation, from either a CSV or a DataFrame."""


def _unwrap_optional(annotation):
    """Optional[date] -> date, so field-type checks below work on Optional fields too."""
    if get_origin(annotation) is Union:
        non_none_args = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none_args) == 1:
            return non_none_args[0]
    return annotation


def _normalize_date(value):
    """
    Real-world exports use every date format imaginable: '01/15/2026',
    '15-Jan-2026', '2026-01-15T00:00:00', etc. Pydantic's date field only
    accepts ISO format (YYYY-MM-DD) out of the box and rejects the rest
    outright. Parse permissively here; let Pydantic do the actual type
    validation afterward on the normalized value.

    ASSUMPTION: month-first (US) parsing for ambiguous dates like '01/02/2026'
    -- correct for the US contractor market this targets, but worth revisiting
    if a source ever needs day-first parsing.
    """
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date_parser.parse(value, dayfirst=False).date()
        except (ValueError, OverflowError, TypeError):
            return value  # leave as-is; Pydantic will raise its own clear error
    return value


def _normalize_currency(value):
    """
    Real-world exports commonly format money as '$5,000.00' or with
    accounting-style negatives like '(500.00)'. Strip that down to a plain
    number Pydantic's float validator accepts.
    """
    if value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        cleaned = value.strip().replace("$", "").replace(",", "")
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        try:
            return float(cleaned)
        except ValueError:
            return value  # leave as-is; Pydantic will raise its own clear error
    return value


def _normalize_row(row: dict, model: Type[T]) -> dict:
    """
    Normalize known-messy formats (dates, currency strings) per field,
    based on the Pydantic model's own declared field types -- so this
    only ever touches fields that are actually typed as `date` or
    `float`, never guesses based on field name or content.
    """
    normalized = {}
    for field_name, value in row.items():
        field_info = model.model_fields.get(field_name)
        if field_info is None:
            normalized[field_name] = value
            continue
        field_type = _unwrap_optional(field_info.annotation)
        if field_type is date:
            normalized[field_name] = _normalize_date(value)
        elif field_type is float:
            normalized[field_name] = _normalize_currency(value)
        else:
            normalized[field_name] = value
    return normalized


def _validate_rows(rows: list[dict], model: Type[T], source_name: str) -> list[T]:
    """
    Core validator — every entry point (CSV or DataFrame) ends up here.
    Every row is normalized (dates, currency formatting) before validation,
    so both the CSV and DataFrame paths get the same tolerance for messy
    real-world formats -- this is what Step 10's live connectors will need.

    row_num starts at 2 to match a CSV's line numbering (header = line 1);
    for DataFrame sources it's the record's position + 1, still useful for
    pinpointing which row failed.
    """
    validated: list[T] = []
    for row_num, raw_row in enumerate(rows, start=2):
        try:
            normalized_row = _normalize_row(raw_row, model)
            validated.append(model.model_validate(normalized_row))
        except ValidationError as e:
            raise DataLoadError(
                f"{source_name}, row {row_num}: invalid data.\n{e}"
            ) from e
    return validated


def _load_csv(path: Path, model: Type[T]) -> list[T]:
    if not path.exists():
        raise DataLoadError(f"Missing required file: {path}")

    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            # CSV gives every field as a string; blank strings should
            # become None for optional fields rather than "" (Pydantic
            # will reject "" for a date/float field).
            rows.append({k: (v if v != "" else None) for k, v in raw_row.items()})

    return _validate_rows(rows, model, source_name=path.name)


def _load_dataframe(df: pd.DataFrame, model: Type[T], source_name: str) -> list[T]:
    """
    Same validation as _load_csv, starting from an in-memory DataFrame
    instead of a file path.

    Pandas represents missing values as NaN, not "". Those must become None
    so Optional fields behave identically to the CSV path (e.g.
    Invoice.paid_date, Job.parent_job_id).

    NOTE: do NOT use `df.where(pd.notnull(df), None)` for this. On a column
    pandas typed as float64 -- which is exactly what an all-blank column in
    a real CSV becomes -- assigning None coerces straight back to NaN, and
    Pydantic then rejects the float where it wanted a string-or-None. Convert
    per-cell after to_dict(), where values are plain Python objects and no
    dtype coercion can undo the fix.
    """
    rows = []
    for record in df.to_dict(orient="records"):
        rows.append({k: (None if _is_missing(v) else v) for k, v in record.items()})
    return _validate_rows(rows, model, source_name=source_name)


def _is_missing(value) -> bool:
    """
    True for pandas/numpy missing markers (NaN, NaT, pd.NA) and empty strings.
    Guarded because pd.isna() returns an array for list/array inputs, which
    would raise if used directly in a boolean context.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


# --- CSV-path loaders — unchanged behavior, used by the CLI (src/run.py) ---

def load_technicians(data_dir: Path) -> list[Technician]:
    return _load_csv(data_dir / "technicians.csv", Technician)


def load_customers(data_dir: Path) -> list[Customer]:
    return _load_csv(data_dir / "customers.csv", Customer)


def load_jobs(data_dir: Path) -> list[Job]:
    return _load_csv(data_dir / "jobs.csv", Job)


def load_quotes(data_dir: Path) -> list[Quote]:
    return _load_csv(data_dir / "quotes.csv", Quote)


def load_invoices(data_dir: Path) -> list[Invoice]:
    return _load_csv(data_dir / "invoices.csv", Invoice)


def load_time_entries(data_dir: Path) -> list[TimeEntry]:
    return _load_csv(data_dir / "time_entries.csv", TimeEntry)


# --- DataFrame-path loaders — new; used by app.py after column mapping ---

def load_technicians_from_df(df: pd.DataFrame) -> list[Technician]:
    return _load_dataframe(df, Technician, source_name="technicians")


def load_customers_from_df(df: pd.DataFrame) -> list[Customer]:
    return _load_dataframe(df, Customer, source_name="customers")


def load_jobs_from_df(df: pd.DataFrame) -> list[Job]:
    return _load_dataframe(df, Job, source_name="jobs")


def load_quotes_from_df(df: pd.DataFrame) -> list[Quote]:
    return _load_dataframe(df, Quote, source_name="quotes")


def load_invoices_from_df(df: pd.DataFrame) -> list[Invoice]:
    return _load_dataframe(df, Invoice, source_name="invoices")


def load_time_entries_from_df(df: pd.DataFrame) -> list[TimeEntry]:
    return _load_dataframe(df, TimeEntry, source_name="time_entries")


class Dataset(BaseModel):
    """All 6 tables loaded and validated together."""
    technicians: list[Technician]
    customers: list[Customer]
    jobs: list[Job]
    quotes: list[Quote]
    invoices: list[Invoice]
    time_entries: list[TimeEntry]


def load_all(data_dir: Path) -> Dataset:
    """CSV-file entry point — unchanged, still what the CLI (src/run.py) uses."""
    return Dataset(
        technicians=load_technicians(data_dir),
        customers=load_customers(data_dir),
        jobs=load_jobs(data_dir),
        quotes=load_quotes(data_dir),
        invoices=load_invoices(data_dir),
        time_entries=load_time_entries(data_dir),
    )


def load_all_from_dfs(dfs: dict[str, pd.DataFrame]) -> Dataset:
    """
    DataFrame entry point — used by app.py once each of the 6 uploaded
    files has been through column_mapping.render_mapping_ui() and is
    already renamed to match schema.py's field names.

    dfs must have exactly these keys:
    technicians, customers, jobs, quotes, invoices, time_entries.
    """
    return Dataset(
        technicians=load_technicians_from_df(dfs["technicians"]),
        customers=load_customers_from_df(dfs["customers"]),
        jobs=load_jobs_from_df(dfs["jobs"]),
        quotes=load_quotes_from_df(dfs["quotes"]),
        invoices=load_invoices_from_df(dfs["invoices"]),
        time_entries=load_time_entries_from_df(dfs["time_entries"]),
    )
