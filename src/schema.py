"""
src/schema.py

The normalized schema for the Contractor Insight Engine.
This is the one file that must survive unchanged to 500 customers —
connectors (CSV now, Jobber/QBO later) change; this never should.

Every model validates at load time. A row that doesn't fit these types
fails loudly here, not silently three stages downstream in a wrong metric.
"""

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Technician(BaseModel):
    tech_id: str
    name: str
    role: str
    loaded_hourly_cost: float = Field(gt=0)


class Customer(BaseModel):
    customer_id: str
    segment: Literal["Residential", "Property Management", "Commercial"]


class Job(BaseModel):
    job_id: str
    date: date
    customer_id: str
    tech_id: str
    job_type: str
    revenue: float = Field(ge=0)
    labor_hours: float = Field(ge=0)
    labor_cost: float = Field(ge=0)
    material_cost: float = Field(ge=0)
    is_callback: bool
    parent_job_id: Optional[str] = None


class Quote(BaseModel):
    quote_id: str
    date: date
    customer_id: str
    tech_id: str
    job_type: str
    amount: float = Field(ge=0)
    status: Literal["won", "lost", "open"]


class Invoice(BaseModel):
    invoice_id: str
    job_id: str
    customer_id: str
    segment: Literal["Residential", "Property Management", "Commercial"]
    invoice_date: date
    paid_date: Optional[date] = None  # None = unpaid -> counts toward AR outstanding
    amount: float = Field(ge=0)


class TimeEntry(BaseModel):
    tech_id: str
    week_start: date
    paid_hours: float = Field(ge=0)
    billable_hours: float = Field(ge=0)