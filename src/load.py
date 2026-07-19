"""
src/load.py

Reads the 6 CSV files into validated Pydantic model lists.
Connectors change (CSV now, Jobber/QBO API later) — this module is the
only place that knows the current connector. Everything downstream just
gets clean, validated Python objects.

Fails loudly: a single bad row raises a clear, row-numbered error instead
of silently producing NaN/None that would corrupt a metric three stages
later.
"""

import csv
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from src.schema import Customer, Invoice, Job, Quote, Technician, TimeEntry

T = TypeVar("T", bound=BaseModel)


class DataLoadError(Exception):
    """Raised when a CSV row fails schema validation."""


def _load_csv(path: Path, model: Type[T]) -> list[T]:
    if not path.exists():
        raise DataLoadError(f"Missing required file: {path}")

    rows: list[T] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for line_num, raw_row in enumerate(reader, start=2):  # header is line 1
            cleaned = {k: (v if v != "" else None) for k, v in raw_row.items()}
            try:
                rows.append(model.model_validate(cleaned))
            except ValidationError as e:
                raise DataLoadError(
                    f"{path.name}, row {line_num}: invalid data.\n{e}"
                ) from e
    return rows


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


class Dataset(BaseModel):
    """All 6 tables loaded and validated together."""
    technicians: list[Technician]
    customers: list[Customer]
    jobs: list[Job]
    quotes: list[Quote]
    invoices: list[Invoice]
    time_entries: list[TimeEntry]


def load_all(data_dir: Path) -> Dataset:
    return Dataset(
        technicians=load_technicians(data_dir),
        customers=load_customers(data_dir),
        jobs=load_jobs(data_dir),
        quotes=load_quotes(data_dir),
        invoices=load_invoices(data_dir),
        time_entries=load_time_entries(data_dir),
    )