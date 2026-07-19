"""
tests/test_rules.py

Tests the rules engine against the same toy dataset used in
test_metrics.py (extended with technicians for utilization dollar-impact).
Verifies exactly which rules fire, and hand-checks the dollar impact
of each (see comments) — per the build guide's Step 4 acceptance test:
"a hand-crafted toy dataset fires exactly the rules you expect —
no more, no fewer."
"""

from pathlib import Path

import pytest

from src.schema import Job, Quote, Invoice, TimeEntry, Technician
from src.load import Dataset
from src.rules import run_rules

RULES_PATH = Path(__file__).parent.parent / "rules.yaml"

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

TECHNICIANS = [
    Technician(tech_id="T1", name="Tech 1", role="HVAC Tech", loaded_hourly_cost=40),
    Technician(tech_id="T2", name="Tech 2", role="HVAC Tech", loaded_hourly_cost=35),
]

DATASET = Dataset(
    technicians=TECHNICIANS, customers=[], jobs=JOBS, quotes=QUOTES,
    invoices=INVOICES, time_entries=TIME_ENTRIES,
)


def test_exactly_six_rules_fire():
    """
    Toy dataset plants 6 issues (mirroring the real synthetic shop's 6):
    negative margin (Furnace Install), high callback (T1), low
    utilization (T2), low win rate (Electrical Panel Upgrade), slow pay
    (Commercial), high AR outstanding (global). The 7th rule (pricing
    consistency) correctly does NOT fire here — it requires n>=20 jobs
    per type to avoid flagging small-sample noise, and this toy dataset
    only has 2-3 jobs per type. That's the guardrail working, not a bug.
    """
    fired = run_rules(DATASET, RULES_PATH)
    fired_ids = {f.rule_id for f in fired}
    assert fired_ids == {
        "negative_margin_job_type",
        "high_callback_tech",
        "underutilized_tech",
        "low_win_rate_job_type",
        "slow_pay_segment",
        "high_ar_outstanding",
    }
    assert len(fired) == 6


def test_negative_margin_dollar_impact():
    # abs(-0.10) * (revenue sum for Furnace Install, non-callback) = 0.10 * 3000 = 300
    fired = run_rules(DATASET, RULES_PATH)
    rule = next(f for f in fired if f.rule_id == "negative_margin_job_type")
    assert rule.key == "Furnace Install"
    assert rule.dollar_impact == pytest.approx(300.0, abs=0.01)


def test_underutilized_dollar_impact():
    # (target 0.75 - actual 0.25) * paid_hours (40) * hourly_cost (35) = 700
    fired = run_rules(DATASET, RULES_PATH)
    rule = next(f for f in fired if f.rule_id == "underutilized_tech")
    assert rule.key == "T2"
    assert rule.dollar_impact == pytest.approx(700.0, abs=0.01)


def test_low_win_rate_dollar_impact():
    # sum of lost quote amounts for Electrical Panel Upgrade (Q5 = 400)
    fired = run_rules(DATASET, RULES_PATH)
    rule = next(f for f in fired if f.rule_id == "low_win_rate_job_type")
    assert rule.key == "Electrical Panel Upgrade"
    assert rule.dollar_impact == pytest.approx(400.0, abs=0.01)


def test_slow_pay_dollar_impact():
    # unpaid invoice amount for Commercial (INV3 = 800; INV4 is paid, excluded)
    fired = run_rules(DATASET, RULES_PATH)
    rule = next(f for f in fired if f.rule_id == "slow_pay_segment")
    assert rule.key == "Commercial"
    assert rule.dollar_impact == pytest.approx(800.0, abs=0.01)


def test_high_ar_outstanding_dollar_impact():
    # total unpaid invoice amount = 800 (only INV3 is unpaid)
    fired = run_rules(DATASET, RULES_PATH)
    rule = next(f for f in fired if f.rule_id == "high_ar_outstanding")
    assert rule.dollar_impact == pytest.approx(800.0, abs=0.01)


def test_high_callback_dollar_impact():
    # median callback rate = (0.25 + 0.0) / 2 = 0.125
    # extra rate = 0.25 - 0.125 = 0.125; job_count for T1 = 4; avg_ticket = 3850/6
    fired = run_rules(DATASET, RULES_PATH)
    rule = next(f for f in fired if f.rule_id == "high_callback_tech")
    assert rule.key == "T1"
    expected = 0.125 * 4 * (3850 / 6)
    assert rule.dollar_impact == pytest.approx(expected, abs=0.01)


def test_fired_rules_sorted_by_dollar_impact_descending():
    fired = run_rules(DATASET, RULES_PATH)
    impacts = [f.dollar_impact for f in fired]
    assert impacts == sorted(impacts, reverse=True)
