"""
tests/test_metrics.py

Tests every metric function against a small toy dataset with
hand-calculated expected values (shown in each test's docstring/comments).
This is the acceptance test your build guide calls for in Step 3:
prove the math is right on a sample small enough to verify by hand,
before trusting it on the full 1,553-job dataset.
"""

import pytest

from src.schema import Job, Quote, Invoice, TimeEntry
from src.metrics import (
    gross_margin_by_job_type,
    callback_rate_by_tech,
    callback_rate_by_job_type,
    tech_utilization,
    unsold_estimate_value,
    win_rate_by_job_type,
    pricing_consistency,
    avg_ticket,
    revenue_per_tech_day,
    cash_cycle_by_segment,
    ar_outstanding,
)

# ---------------------------------------------------------------------------
# Toy dataset — small enough to check every number by hand.
# See comments above each test for the hand-calculation.
# ---------------------------------------------------------------------------

JOBS = [
    Job(job_id="J1", date="2026-01-05", customer_id="C1", tech_id="T1",
        job_type="AC Repair", revenue=200, labor_hours=2, labor_cost=50,
        material_cost=30, is_callback=False),
    Job(job_id="J2", date="2026-01-06", customer_id="C2", tech_id="T1",
        job_type="AC Repair", revenue=300, labor_hours=3, labor_cost=60,
        material_cost=40, is_callback=False),
    Job(job_id="J3", date="2026-01-07", customer_id="C3", tech_id="T2",
        job_type="AC Repair", revenue=100, labor_hours=1, labor_cost=20,
        material_cost=90, is_callback=False),
    Job(job_id="J4", date="2026-01-08", customer_id="C1", tech_id="T2",
        job_type="Furnace Install", revenue=1000, labor_hours=6, labor_cost=600,
        material_cost=500, is_callback=False),
    Job(job_id="J5", date="2026-01-09", customer_id="C2", tech_id="T1",
        job_type="Furnace Install", revenue=2000, labor_hours=8, labor_cost=1000,
        material_cost=1200, is_callback=False),
    Job(job_id="J6", date="2026-01-05", customer_id="C3", tech_id="T1",
        job_type="AC Repair", revenue=250, labor_hours=2, labor_cost=50,
        material_cost=30, is_callback=True),
]

QUOTES = [
    Quote(quote_id="Q1", date="2026-01-01", customer_id="C1", tech_id="T1",
          job_type="AC Repair", amount=150, status="open"),
    Quote(quote_id="Q2", date="2026-01-02", customer_id="C2", tech_id="T1",
          job_type="AC Repair", amount=250, status="won"),
    Quote(quote_id="Q3", date="2026-01-03", customer_id="C3", tech_id="T2",
          job_type="AC Repair", amount=300, status="lost"),
    Quote(quote_id="Q4", date="2026-01-04", customer_id="C1", tech_id="T2",
          job_type="Electrical Panel Upgrade", amount=500, status="open"),
    Quote(quote_id="Q5", date="2026-01-05", customer_id="C2", tech_id="T1",
          job_type="Electrical Panel Upgrade", amount=400, status="lost"),
]

INVOICES = [
    Invoice(invoice_id="INV1", job_id="J1", customer_id="C1", segment="Residential",
            invoice_date="2026-01-01", paid_date="2026-01-08", amount=200),
    Invoice(invoice_id="INV2", job_id="J2", customer_id="C2", segment="Residential",
            invoice_date="2026-01-02", paid_date="2026-01-16", amount=300),
    Invoice(invoice_id="INV3", job_id="J4", customer_id="C1", segment="Commercial",
            invoice_date="2026-01-01", paid_date=None, amount=800),
    Invoice(invoice_id="INV4", job_id="J5", customer_id="C2", segment="Commercial",
            invoice_date="2026-01-03", paid_date="2026-03-03", amount=1000),
]

TIME_ENTRIES = [
    TimeEntry(tech_id="T1", week_start="2026-01-05", paid_hours=40, billable_hours=30),
    TimeEntry(tech_id="T1", week_start="2026-01-12", paid_hours=40, billable_hours=35),
    TimeEntry(tech_id="T2", week_start="2026-01-05", paid_hours=40, billable_hours=10),
]


# ---------------------------------------------------------------------------
# 1. Gross margin by job type
# AC Repair (non-callback: J1,J2,J3): revenue=600, labor=130, material=160
#   margin = (600-130-160)/600 = 310/600 = 0.51667
# Furnace Install (J4,J5): revenue=3000, labor=1600, material=1700
#   margin = (3000-1600-1700)/3000 = -300/3000 = -0.10
# ---------------------------------------------------------------------------

def test_gross_margin_by_job_type():
    result = gross_margin_by_job_type(JOBS)
    assert result["AC Repair"] == pytest.approx(310 / 600, abs=1e-6)
    assert result["Furnace Install"] == pytest.approx(-0.10, abs=1e-6)


# ---------------------------------------------------------------------------
# 2. Callback rate
# By tech: T1 has 4 jobs (J1,J2,J5,J6), 1 callback (J6) -> 0.25
#          T2 has 2 jobs (J3,J4), 0 callbacks -> 0.0
# By job_type: AC Repair has 4 jobs, 1 callback -> 0.25
#              Furnace Install has 2 jobs, 0 callbacks -> 0.0
# ---------------------------------------------------------------------------

def test_callback_rate_by_tech():
    result = callback_rate_by_tech(JOBS)
    assert result["T1"] == pytest.approx(0.25, abs=1e-6)
    assert result["T2"] == pytest.approx(0.0, abs=1e-6)


def test_callback_rate_by_job_type():
    result = callback_rate_by_job_type(JOBS)
    assert result["AC Repair"] == pytest.approx(0.25, abs=1e-6)
    assert result["Furnace Install"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 3. Tech utilization
# T1: billable=30+35=65, paid=40+40=80 -> 65/80 = 0.8125
# T2: billable=10, paid=40 -> 10/40 = 0.25
# ---------------------------------------------------------------------------

def test_tech_utilization():
    result = tech_utilization(TIME_ENTRIES)
    assert result["T1"] == pytest.approx(0.8125, abs=1e-6)
    assert result["T2"] == pytest.approx(0.25, abs=1e-6)


# ---------------------------------------------------------------------------
# 4. Unsold estimate value + win rate
# AC Repair: open amount = 150 (Q1)
# Electrical Panel Upgrade: open amount = 500 (Q4)
# AC Repair win rate: won=1 (Q2), lost=1 (Q3) -> 1/2 = 0.5
# Electrical Panel Upgrade win rate: won=0, lost=1 (Q5) -> 0/1 = 0.0
# ---------------------------------------------------------------------------

def test_unsold_estimate_value():
    result = unsold_estimate_value(QUOTES)
    assert result["AC Repair"] == pytest.approx(150, abs=1e-6)
    assert result["Electrical Panel Upgrade"] == pytest.approx(500, abs=1e-6)


def test_win_rate_by_job_type():
    result = win_rate_by_job_type(QUOTES)
    assert result["AC Repair"] == pytest.approx(0.5, abs=1e-6)
    assert result["Electrical Panel Upgrade"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 5. Pricing consistency (coefficient of variation)
# AC Repair non-callback revenues: 200, 300, 100 (n=3)
#   mean = 200, sample std (ddof=1) = 100 -> CoV = 100/200 = 0.5
# (min_n lowered to 2 for this toy test; production default is 20)
# ---------------------------------------------------------------------------

def test_pricing_consistency():
    result = pricing_consistency(JOBS, min_n=2)
    assert result["AC Repair"] == pytest.approx(0.5, abs=1e-6)


# ---------------------------------------------------------------------------
# 6. Avg ticket / revenue per tech-day
# avg_ticket: mean of all 6 revenues = 3850/6 = 641.6667
# revenue_per_tech_day: distinct (tech,date) pairs = 5
#   (T1,01-05) [J1&J6 share it], (T1,01-06), (T2,01-07), (T2,01-08), (T1,01-09)
#   sum revenue = 3850 -> 3850/5 = 770.0
# ---------------------------------------------------------------------------

def test_avg_ticket():
    result = avg_ticket(JOBS)
    assert result == pytest.approx(3850 / 6, abs=1e-6)


def test_revenue_per_tech_day():
    result = revenue_per_tech_day(JOBS)
    assert result["2026-01"] == pytest.approx(770.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 7. Cash cycle + AR outstanding
# Residential: INV1 = 7 days, INV2 = 14 days -> mean = 10.5
# Commercial: INV3 unpaid (excluded), INV4 = 59 days -> mean = 59.0
# AR outstanding: INV3 amount (unpaid) = 800
# ---------------------------------------------------------------------------

def test_cash_cycle_by_segment():
    result = cash_cycle_by_segment(INVOICES)
    assert result["Residential"] == pytest.approx(10.5, abs=1e-6)
    assert result["Commercial"] == pytest.approx(59.0, abs=1e-6)


def test_ar_outstanding():
    result = ar_outstanding(INVOICES)
    assert result == pytest.approx(800, abs=1e-6)
